"""给 README 抓界面截图：对运行中的查询站逐页截图到 docs/screenshots/。
用法: .venv/Scripts/python.exe _rtest/shoot_readme.py [base_url]
只读操作，不会触发任何 Bungie 写接口。
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8900"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "screenshots")

# (文件名, 路径, 视口宽, 高, 等待毫秒)
PAGES = [
    ("home.png",     "/",               1400, 900,  2500),
    ("catalog.png",  "/catalog",        1400, 1100, 6000),
    ("perks.png",    "/perks",          1400, 900,  2500),
    ("panel.png",    "/panel",          1400, 900,  2500),
    ("eververse.png", "/eververse",     1400, 1000, 4000),
    ("rotation.png", "/rotation",       1400, 1000, 4000),
    # 带数据的战绩卡（门面图）
    ("card-all.png",  "/card?name=Wj%238984&mode=all",       1400, 1000, 12000),
    ("card-raid.png", "/card?name=Wj%238984&mode=raid",      1400, 1200, 12000),
    ("card-titles.png", "/card?name=Wj%238984&mode=titles",  1400, 1200, 12000),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900},
                                  device_scale_factor=1)
        page = ctx.new_page()
        for name, path, w, h, wait in PAGES:
            try:
                page.set_viewport_size({"width": w, "height": h})
                page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(wait)
                page.screenshot(path=os.path.join(OUT, name))
                print(f"ok  {name:16s} {path}")
            except Exception as e:  # noqa: BLE001
                print(f"ERR {name:16s} {path} -> {e}")
        browser.close()


if __name__ == "__main__":
    main()
