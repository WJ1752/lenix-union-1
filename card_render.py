"""HTML 文档 → PNG 图片卡片（小日向式图片输出的渲染层）

QQ 机器人不再回纯文本，而是把回复渲染成图片再发（见 bot_cards.py 负责排版）。
渲染用 Playwright 驱动无头浏览器：
  1. 优先用 Playwright 自带的 chromium（装了 `playwright install chromium` 时）；
  2. 否则用系统 Edge（channel=msedge，本机已验证）；
  3. 再否则用系统 Chrome。
浏览器实例进程内复用，**同一个 context + 同一个页面也复用**：卡片的图标全在
bungie.net CDN 上，换页面就等于换一个空缓存，实测 6 张远程图每次都要多花 ~500ms
且刷新多少次都不会变快；复用之后第二次起直接命中浏览器缓存。

等图策略：先等 <img> 全部 complete（≤2.5s），再给 CSS background-image 留一个很短的
networkidle 窗口（≤1.2s）。原来的 networkidle(8s) 在图标慢时会白等满 8 秒。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import sys
import weakref


def _base_dir() -> str:
    """图标缓存落盘位置：打包后放 exe 旁边，源码运行放本文件旁边"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 卡片上的图标全在 bungie.net 的 CDN 上，从国内拉一次要几百毫秒到几秒，而且
# 每次渲染都要重新拉一遍（浏览器换页面=换缓存）。这里自己拉一次存盘（图标 URL
# 是内容寻址的，拉到的内容不会变），渲染前内联成 data URI —— 浏览器不用再等网络，
# 重启程序后缓存也还在。
_ICON_DIR = os.path.join(_base_dir(), "icon_cache")
_ICON_URL = re.compile(r"https://www\.bungie\.net/[A-Za-z0-9_./\-]+")
_ICON_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_ICONS: dict[str, str] = {}          # url → data URI（进程内）
_ICON_MISS: set[str] = set()         # 拉不到的，本次进程别再试
_ICON_LIMIT = 8                      # 并发下载数
_ICON_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, object]" = \
    weakref.WeakKeyDictionary()

# exe 里主界面 / HTTPS 回跳 / QQ bot 各跑一个事件循环，而 Playwright 的浏览器实例
# 只能被创建它的那个循环驱动。模块级共用一个 browser 时，第二个循环再渲染就会报
# 「Future attached to a different loop」之类。故按事件循环各持一份。
_STATE: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict]" = weakref.WeakKeyDictionary()


def _icon_client():
    """按事件循环各持一个 httpx 客户端（理由同上：连接池绑循环）"""
    import httpx
    loop = asyncio.get_running_loop()
    cli = _ICON_CLIENTS.get(loop)
    if cli is None or cli.is_closed:
        cli = httpx.AsyncClient(timeout=10, follow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0 (D2Query card renderer)"})
        _ICON_CLIENTS[loop] = cli
    return cli


async def _icon(sem: asyncio.Semaphore, url: str) -> str:
    """一个图标 → data URI；拿不到就原样返回 URL（让浏览器自己再试）"""
    hit = _ICONS.get(url)
    if hit is not None:
        return hit
    if url in _ICON_MISS:
        return url
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    h = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(_ICON_DIR, h[:2], h[2:] + ext[:5])
    data = b""
    try:
        data = open(path, "rb").read()
    except OSError:
        data = b""
    if not data:
        async with sem:
            try:
                r = await _icon_client().get(url)
                if r.status_code == 200 and r.content:
                    data = r.content
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
            except Exception:  # noqa: BLE001  网络问题就当没有，别拖住整张卡
                data = b""
    if not data:
        _ICON_MISS.add(url)
        return url
    uri = (f"data:{_ICON_MIME.get(ext, 'image/jpeg')};base64,"
           + base64.b64encode(data).decode())
    if len(_ICONS) < 1500:
        _ICONS[url] = uri
    return uri


async def inline_icons(html: str) -> str:
    """把 HTML 里 bungie.net 的图片地址换成内联的 data URI"""
    urls = sorted(set(_ICON_URL.findall(html)))
    if not urls:
        return html
    sem = asyncio.Semaphore(_ICON_LIMIT)
    for url, uri in zip(urls, await asyncio.gather(*(_icon(sem, u) for u in urls))):
        if uri != url:
            html = html.replace(url, uri)
    return html


class _State:
    __slots__ = ("lock", "pw", "browser", "ctx", "ctx_size", "page")

    def __init__(self):
        self.lock = asyncio.Lock()
        self.pw = None
        self.browser = None
        self.ctx = None          # 复用同一个 BrowserContext（= 复用 HTTP 缓存）
        self.ctx_size = None     # (width, scale)：尺寸变了才重建
        self.page = None


def _state() -> _State:
    loop = asyncio.get_running_loop()
    st = _STATE.get(loop)
    if st is None:
        st = _State()
        _STATE[loop] = st
    return st


async def _launch(st: _State):
    """拿到可用浏览器（首次调用时按 chromium → Edge → Chrome 依次尝试）"""
    if st.browser is not None and st.browser.is_connected():
        return st.browser
    st.ctx = st.page = None          # 旧浏览器没了，挂在它上面的 context/page 一并作废
    from playwright.async_api import async_playwright
    if st.pw is None:
        st.pw = await async_playwright().start()
    err = None
    for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            st.browser = await st.pw.chromium.launch(**kwargs)
            return st.browser
        except Exception as exc:  # noqa: BLE001
            err = exc
    raise RuntimeError(f"没有可用的浏览器渲染卡片（chromium / Edge / Chrome 都起不来）：{err}")


async def _page(st: _State, width: int, scale: int):
    """复用页面（含缓存）；宽度/缩放变了才重建 context"""
    br = await _launch(st)
    if st.page is not None and not st.page.is_closed() and st.ctx_size == (width, scale):
        return st.page
    if st.page is not None and not st.page.is_closed():
        await st.page.close()
    st.page = None
    if st.ctx is None or st.ctx_size != (width, scale):
        if st.ctx is not None:
            await st.ctx.close()
            st.ctx = None
        st.ctx = await br.new_context(viewport={"width": width, "height": 200},
                                      device_scale_factor=scale)
        st.ctx_size = (width, scale)
    st.page = await st.ctx.new_page()
    return st.page


async def _drop(st: _State):
    """渲染失败后把页面扔掉重建；浏览器也断了就整个重来"""
    try:
        if st.page is not None and not st.page.is_closed():
            await st.page.close()
    except Exception:  # noqa: BLE001
        pass
    st.page = None
    if st.browser is not None and not st.browser.is_connected():
        st.browser = None
        st.ctx = None
        st.ctx_size = None


async def _settle(page) -> None:
    """等图标画完：img 全 complete → 再给 CSS 背景图一个很短的 networkidle 窗口"""
    try:
        await page.wait_for_function("Array.from(document.images).every(i => i.complete)",
                                     timeout=2500)
    except Exception:  # noqa: BLE001  等不到也用现有内容出图
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=1200)
    except Exception:  # noqa: BLE001
        pass


async def html_to_png(html: str, width: int = 760, scale: int = 2) -> bytes:
    """完整 HTML 文档 → PNG 字节（宽度固定，高度自适应内容）"""
    st = _state()
    async with st.lock:  # 浏览器/页面不是并发安全的，串行渲染
        html = await inline_icons(html)   # 图标先落到本地缓存并内联，渲染不等 CDN
        for attempt in (1, 2):
            try:
                page = await _page(st, width, scale)
                await page.set_content(html, wait_until="load", timeout=30000)
                await _settle(page)
                return await page.screenshot(full_page=True, type="png")
            except Exception:
                await _drop(st)          # 页面/浏览器可能已崩，第二次重开
                if attempt == 2:
                    raise


async def prewarm(width: int = 760, scale: int = 2) -> bool:
    """提前把浏览器和页面起好（启动时后台调用，见 nonebot_plugins/destiny2.py）：
    首次查询不用再等一次浏览器冷启动（实测 ~1.8s）。失败不影响后续渲染。"""
    st = _state()
    async with st.lock:
        try:
            await _page(st, width, scale)
            return True
        except Exception:  # noqa: BLE001
            return False


async def close():
    """释放浏览器（服务退出时调用，可选）"""
    try:
        st = _STATE.get(asyncio.get_running_loop())
    except RuntimeError:          # 没有运行中的循环：退回到逐个关
        for st in list(_STATE.values()):
            await _close_state(st)
        return
    if st is not None:
        await _close_state(st)


async def _close_state(st: _State):
    try:
        if st.browser is not None:
            await st.browser.close()
    finally:
        st.browser = None
        st.ctx = None
        st.ctx_size = None
        st.page = None
        if st.pw is not None:
            await st.pw.stop()
            st.pw = None
