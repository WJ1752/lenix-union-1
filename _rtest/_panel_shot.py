"""面板「任务与日志」页截图（看维护横幅 + 未发送图片的缩略图/按钮样式）"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.async_api import async_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_panel_shot.png")

async def main():
    async with async_playwright() as pw:
        for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                br = await pw.chromium.launch(**kw)
                break
            except Exception as e:
                last = e
        else:
            raise last
        pg = await br.new_page(viewport={"width": 1280, "height": 1000})
        await pg.goto("http://127.0.0.1:8999/panel", wait_until="load")
        await pg.click("span.tb[data-t='jobs']")
        await asyncio.sleep(2.5)          # 等日志/任务轮询回来
        await pg.screenshot(path=OUT, full_page=True)
        print("截图:", OUT)
        await br.close()

asyncio.run(main())
