"""线上面板截图（新构建 8900）：总览的服务器状态瓷砖 + 任务与日志页"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.async_api import async_playwright

D = os.path.dirname(os.path.abspath(__file__))

async def main():
    async with async_playwright() as pw:
        for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                br = await pw.chromium.launch(**kw); break
            except Exception as e:
                last = e
        else:
            raise last
        pg = await br.new_page(viewport={"width": 1280, "height": 620})
        await pg.goto("http://127.0.0.1:8900/panel", wait_until="load")
        await asyncio.sleep(3)
        await pg.screenshot(path=os.path.join(D, "_live_overview.png"))
        await pg.click("span.tb[data-t='jobs']")
        await asyncio.sleep(3)
        await pg.screenshot(path=os.path.join(D, "_live_jobs.png"))
        print("已截图 总览 + 任务与日志")
        await br.close()

asyncio.run(main())
