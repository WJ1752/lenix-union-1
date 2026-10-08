import asyncio, json, os
from playwright.async_api import async_playwright

PROF = os.path.abspath("_rtest/edge_cf_profile")

async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROF, channel="msedge", headless=False,
            viewport={"width": 1100, "height": 850})
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pg.goto("https://dungeon.report/profile/4611686018494788027", timeout=90000,
                      wait_until="domcontentloaded")
        ok = False
        for i in range(36):
            await pg.wait_for_timeout(5000)
            t = await pg.title()
            print(f"t+{(i+1)*5}s TITLE:", t, flush=True)
            if ("moment" not in t.lower() and "稍候" not in t and "安全验证" not in t):
                ok = True
                break
        if ok:
            await pg.wait_for_timeout(8000)   # 等 SPA 数据渲染
            try:
                r = await ctx.request.get("https://api.raidreport.dev/dungeon/player/4611686018494788027",
                                          timeout=30000)
                raw = await r.text()
                print("API STATUS:", r.status, "LEN:", len(raw), flush=True)
                with open("drep_raw.json", "w", encoding="utf-8") as f:
                    f.write(raw)
            except Exception as e:
                print("API fail:", type(e).__name__, str(e)[:200], flush=True)
            body = await pg.evaluate("() => document.body ? document.body.innerText.slice(0,6000) : 'NOBODY'")
            print("BODY>>>", body[:3000].replace("\n", " | "), flush=True)
        else:
            print("CF NOT PASSED", flush=True)
        await ctx.close()

asyncio.run(main())
