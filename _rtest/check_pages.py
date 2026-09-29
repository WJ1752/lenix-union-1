"""页面排版自查：导航是否统一（同一排、条目一致）、内容是否居中、角落按钮是否还在
用法: python _rtest/check_pages.py [base_url]   默认 http://127.0.0.1:8910
"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8910"
PAGES = ["/", "/catalog", "/perks", "/rotation", "/eververse", "/panel",
         "/weapon?hash=2150012407"]


async def main():
    pw = await async_playwright().start()
    br = err = None
    for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            br = await pw.chromium.launch(**kw)
            break
        except Exception as exc:  # noqa: BLE001
            err = exc
    page = await br.new_page(viewport={"width": 1900, "height": 1000})
    for path in PAGES:
        resp = await page.goto(BASE + path, wait_until="load", timeout=30000)
        nav = await page.eval_on_selector_all(
            ".d2nav .nv",
            "els=>els.map(e=>[e.textContent.trim(), Math.round(e.getBoundingClientRect().top)])")
        rows = sorted({t for _, t in nav})
        corner = await page.eval_on_selector_all(".corner", "els=>els.length")
        # 内容列中心 vs 视口中心：取导航下方第一个块级容器的水平中心
        center = await page.evaluate("""() => {
            const cands = ['.wrap', '.card', 'body > div', 'iframe#card'];
            for (const sel of cands) {
              const el = document.querySelector(sel);
              if (el && el.getBoundingClientRect().width > 200) {
                const r = el.getBoundingClientRect();
                return [Math.round(r.left), Math.round(r.right), Math.round(r.width)];
              }
            }
            return null;
        }""")
        off = abs((center[0] + center[2] / 2) - 950) if center else None
        print(f"http{resp.status} {path}")
        print(f"   导航: {len(nav)} 项 {[n for n, _ in nav]} 行数={len(rows)}")
        print(f"   角落按钮: {corner}   内容列 l/r/w={center} 中心偏差={off}")
    await br.close()
    await pw.stop()


asyncio.run(main())
