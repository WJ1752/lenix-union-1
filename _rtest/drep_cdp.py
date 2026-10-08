import asyncio, json
from playwright.async_api import async_playwright

MID = "4611686018494788027"

async def fetch_in_page(page, url):
    return await page.evaluate(
        """async u => { const r = await fetch(u, {credentials:'omit'});
           return {status: r.status, body: await r.text()}; }""", url)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
        ctx = b.contexts[0] if b.contexts else await b.new_context()
        page = None
        for attempt in range(3):
            try:
                page = await ctx.new_page()
                await page.goto("https://dungeon.report/profile/" + MID,
                                timeout=60000, wait_until="commit")
                break
            except Exception as e:
                print(f"goto attempt {attempt+1} fail: {type(e).__name__} {str(e)[:120]}", flush=True)
                if page:
                    try: await page.close()
                    except Exception: pass
                page = None
                await asyncio.sleep(3)
        if page is None:
            print("GIVE UP"); await b.close(); return
        try:
            ok = False
            for i in range(60):
                await page.wait_for_timeout(2000)
                t = await page.title()
                if t and "moment" not in t.lower() and "稍候" not in t and "安全验证" not in t:
                    ok = True
                    print(f"PASS at +{(i+1)*2}s TITLE: {t}", flush=True)
                    break
            if not ok:
                print("CF NOT PASSED", flush=True)
                return
            await page.wait_for_timeout(10000)
            body = await page.evaluate("() => document.body.innerText.slice(0,6000)")
            print("BODY>>>", body[:2600].replace("\n", " | "), flush=True)
            for name, url in [
                ("dungeon_player", f"https://api.raidreport.dev/dungeon/player/{MID}"),
                ("wf_crotasend", f"https://api.raidreport.dev/raid/leaderboard/worldsfirst/crotasend?membershipId={MID}"),
                ("wf_crotasend_contest", f"https://api.raidreport.dev/raid/leaderboard/worldsfirst/crotasend/contest?membershipId={MID}"),
            ]:
                try:
                    r = await fetch_in_page(page, url)
                    print(f"== {name}: status={r['status']} len={len(r['body'])}", flush=True)
                    with open(f"_rtest/drep_{name}.json", "w", encoding="utf-8") as f:
                        f.write(r["body"])
                    if r["status"] == 200 and len(r["body"]) < 800:
                        print("   ", r["body"][:400], flush=True)
                except Exception as e:
                    print(f"== {name} FAIL: {type(e).__name__} {str(e)[:150]}", flush=True)
        finally:
            await page.close()
            await b.close()

asyncio.run(main())
