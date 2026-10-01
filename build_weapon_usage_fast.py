# -*- coding: utf-8 -*-
"""高速快照爬取：CDP + 页内 fetch（免渲染）+ 错峰多页面并发。

跑法：.venv\\Scripts\\python.exe _tmp_fastcrawl.py --test        # 单页预检（2 把）
      .venv\\Scripts\\python.exe _tmp_fastcrawl.py --workers 4 --sleep 0.3
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import weapon_usage as wu

CF_TITLES = ("Just a moment", "请稍候", "Attention Required")
CF_BODY = ("cf-chl", "challenge-platform")
OUT = "manifest_index/weapon_usage_snapshot.json"
SEED_URL = "https://www.light.gg/db/items/42435996/"


def wrap_raw(raw: dict) -> dict:
    return {"source": "lightgg", "fetched_at": datetime.date.today().isoformat(),
            "cols": [[list(p) for p in col] for col in raw.get("cols") or []],
            "plugs": [list(p) for p in raw.get("plugs") or []],
            "mw": [list(p) for p in raw.get("mw") or []],
            "mods": [list(p) for p in raw.get("mods") or []],
            "combos": [list(c) for c in raw.get("combos") or []]}


def body_challenged(html: str) -> bool:
    # 注意：challenge-platform 是 CF 注入在【正常页面】上的运行时脚本，不是挑战标志；
    # 只看开头是否有挑战页特征文本/挑战帧标记。
    head = html[:4000]
    return ("Just a moment" in head) or ("请稍候" in head) or ("cf-chl-" in head)


async def open_worker_page(ctx, wid: int, patience_s: float = 40.0):
    """开一页并耐心等挑战放行：标题不再含挑战特征 且 页面已含 community-average。"""
    page = await ctx.new_page()
    try:
        await page.goto(SEED_URL, timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"worker {wid} goto: {type(e).__name__}: {str(e)[:70]}", flush=True)
    deadline = time.monotonic() + patience_s
    while time.monotonic() < deadline:
        try:
            title = await page.title()
            html = await page.content()
        except Exception:
            title, html = "", ""
        if ('id="community-average"' in html) or (
                title and not any(m in title for m in CF_TITLES)
                and not body_challenged(html)):
            print(f"worker {wid} 就绪（title={title[:40]}）", flush=True)
            return page
        await page.wait_for_timeout(1500)
    print(f"worker {wid} 超时未放行，放弃该页", flush=True)
    await page.close()
    return None


async def main():
    from playwright.async_api import async_playwright
    test = "--test" in sys.argv
    workers = 6
    sleep = 0.3
    for i, a in enumerate(sys.argv):
        if a == "--workers":
            workers = int(sys.argv[i + 1])
        if a == "--sleep":
            sleep = float(sys.argv[i + 1])

    wf = wu._load_weapons_full()
    targets = [h for h, w in wf.items()
               if any("特性" in (c.get("t") or "") for c in (w.get("plugs", {}).get("cols") or []))]
    snap = wu._load_json(OUT, {}) or {}
    todo = [h for h in targets if h not in snap]
    if test:
        todo = ["42435996"] + [h for h in targets if h != "42435996"][:1]
        workers, sleep = 1, 0.2
    print(f"targets {len(targets)} | snapshot {len(snap)} | todo {len(todo)} | workers {workers} sleep {sleep}", flush=True)
    if not todo:
        return 0

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    q: asyncio.Queue = asyncio.Queue()
    for h in todo:
        q.put_nowait(h)
    lock = asyncio.Lock()
    stats = {"ok": 0, "fail": 0, "done": 0}
    t0 = time.monotonic()

    async def worker(wid):
        await asyncio.sleep(wid * 3.0)          # 错峰启动，避免并发冲刚过验证的域
        page = await open_worker_page(ctx, wid)
        if page is None:
            return
        try:
            while True:
                try:
                    h = q.get_nowait()
                except asyncio.QueueEmpty:
                    return
                html = ""
                try:
                    html = await page.evaluate(
                        "h => fetch('/db/items/' + h + '/').then(r => r.text())", h)
                except Exception:
                    html = ""
                if html and body_challenged(html):
                    html = ""
                raw = None
                if html:
                    try:
                        raw = wu.parse_lightgg_html(html)
                    except Exception:
                        raw = None
                async with lock:
                    if raw:
                        snap[h] = wrap_raw(raw)
                        stats["ok"] += 1
                    else:
                        stats["fail"] += 1
                    stats["done"] += 1
                    if stats["done"] % 25 == 0:
                        wu._dump_json(OUT, snap)
                    if stats["done"] % 50 == 0 or stats["done"] == len(todo):
                        el = time.monotonic() - t0
                        rate = stats["done"] / el if el else 0
                        eta = (len(todo) - stats["done"]) / rate if rate else 0
                        print(f"[{stats['done']}/{len(todo)}] ok={stats['ok']} fail={stats['fail']} "
                              f"{rate:.1f}/s ETA {eta / 60:.1f}min", flush=True)
                await asyncio.sleep(sleep)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    await asyncio.gather(*(worker(i) for i in range(workers)))
    wu._dump_json(OUT, snap)
    print(f"DONE ok={stats['ok']} fail={stats['fail']} snapshot_total={len(snap)}", flush=True)
    try:
        await browser.close()   # CDP：只断开连接
    except Exception:
        pass
    await pw.stop()
    return 0


sys.exit(asyncio.run(main()))
