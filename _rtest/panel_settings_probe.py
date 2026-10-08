"""面板页面自测：起一个测试端口的 webui，用 Playwright 打开运行状态页/管理面板/战绩卡，
收集页面 JS 报错并核对新加的「近期战绩局数」控件是否加载到值。

跑法：python _rtest/panel_settings_probe.py
"""
import asyncio
import sys
import threading
import time

sys.path.insert(0, ".")

import uvicorn  # noqa: E402

import webui  # noqa: E402

PORT = 8913


def serve() -> None:
    uvicorn.run(webui.app, host="127.0.0.1", port=PORT, log_level="error")


async def main() -> None:
    threading.Thread(target=serve, daemon=True).start()
    await asyncio.sleep(2.5)

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        br = await pw.chromium.launch(channel="msedge")
        page = await br.new_page(viewport={"width": 1200, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        await page.goto(f"http://127.0.0.1:{PORT}/runtime", wait_until="load")
        await asyncio.sleep(3.5)
        print("运行状态页 recPvp =", await page.evaluate("document.getElementById('recPvp').value"),
              "; saveRecent =", await page.evaluate("typeof saveRecent"))

        await page.goto(f"http://127.0.0.1:{PORT}/panel", wait_until="load")
        await page.evaluate("() => document.querySelector('[data-tab=settings]')?.click()")
        await asyncio.sleep(3)
        print("管理面板 recPve =", await page.evaluate("document.getElementById('recPve')?.value"),
              "; saveRecent =", await page.evaluate("typeof saveRecent"))

        # 战绩卡（面板里就是这张卡）
        await page.goto(f"http://127.0.0.1:{PORT}/card?name=Wj%238984&mode=pvp", wait_until="load")
        await asyncio.sleep(1)
        print("战绩卡 chips =", await page.evaluate("document.querySelectorAll('.chip').length"),
              "; 点图格子 =", await page.evaluate("document.querySelectorAll('.cell').length"),
              "; 高 =", await page.evaluate("document.body.scrollHeight"))

        print("页面报错:", errors or "无")
        await br.close()


asyncio.run(main())
