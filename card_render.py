"""HTML 文档 → PNG 图片卡片（小日向式图片输出的渲染层）

QQ 机器人不再回纯文本，而是把回复渲染成图片再发（见 bot_cards.py 负责排版）。
渲染用 Playwright 驱动无头浏览器：
  1. 优先用 Playwright 自带的 chromium（装了 `playwright install chromium` 时）；
  2. 否则用系统 Edge（channel=msedge，本机已验证）；
  3. 再否则用系统 Chrome。
浏览器实例进程内复用，页面每次新建；截图走 full_page，高度随内容自适应。
"""
from __future__ import annotations

import asyncio
import weakref

# exe 里主界面 / HTTPS 回跳 / QQ bot 各跑一个事件循环，而 Playwright 的浏览器实例
# 只能被创建它的那个循环驱动。模块级共用一个 browser 时，第二个循环再渲染就会报
# 「Future attached to a different loop」之类。故按事件循环各持一份。
_STATE: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict]" = weakref.WeakKeyDictionary()


class _State:
    __slots__ = ("lock", "pw", "browser")

    def __init__(self):
        self.lock = asyncio.Lock()
        self.pw = None
        self.browser = None


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


async def html_to_png(html: str, width: int = 760, scale: int = 2) -> bytes:
    """完整 HTML 文档 → PNG 字节（宽度固定，高度自适应内容）"""
    st = _state()
    async with st.lock:  # 浏览器/页面不是并发安全的，串行渲染
        for attempt in (1, 2):
            try:
                br = await _launch(st)
                page = await br.new_page(viewport={"width": width, "height": 200},
                                         device_scale_factor=scale)
                try:
                    await page.set_content(html, wait_until="load", timeout=30000)
                    try:  # 等 Bungie CDN 图标下完；等不到也用现有内容出图
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:  # noqa: BLE001
                        pass
                    return await page.screenshot(full_page=True, type="png")
                finally:
                    await page.close()
            except Exception:
                if attempt == 2:
                    raise
                st.browser = None  # 浏览器可能已崩，第二次重开


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
        if st.pw is not None:
            await st.pw.stop()
            st.pw = None
