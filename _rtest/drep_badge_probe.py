"""抓 dungeon.report 前端 bundle,搜徽章文案(Solo/Duo/Flawless/Contest…),确认 UI 徽章清单"""
import asyncio, json, os, re
from playwright.async_api import async_playwright

PROF = os.path.abspath("_rtest/edge_cf_profile")
OUT = "_rtest/drep_bundles"
os.makedirs(OUT, exist_ok=True)

PAT = re.compile(r'.{50}(?:[Ff]lawless|[Ss]olo|[Dd]uo|[Tt]rion|Day ?One|Contest|[Ss]herpa).{50}', re.S)


async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROF, channel="msedge", headless=False,
            viewport={"width": 1100, "height": 850})
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pg.goto("https://dungeon.report/", timeout=90000,
                      wait_until="domcontentloaded")
        ok = False
        for i in range(36):
            await pg.wait_for_timeout(5000)
            t = await pg.title()
            print(f"t+{(i+1)*5}s TITLE:", t, flush=True)
            if ("moment" not in t.lower() and "稍候" not in t and "安全验证" not in t):
                ok = True
                break
        if not ok:
            print("CF NOT PASSED")
            await ctx.close()
            return
        await pg.wait_for_timeout(5000)
        html = await pg.evaluate("() => fetch('/').then(r=>r.text())")
        srcs = sorted(set(re.findall(r'src="(/_next/static/[^"]+\.js)"', html)))
        print("BUNDLES:", len(srcs), flush=True)
        for i, s in enumerate(srcs):
            try:
                body = await pg.evaluate("u=>fetch(u).then(r=>r.text())", s)
                fn = os.path.join(OUT, f"b{i:02d}_" + s.rsplit('/', 1)[-1][:40] + ".js")
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(body)
                hits = []
                for m in PAT.finditer(body):
                    frag = m.group(0).replace("\n", " ")
                    if any(w in frag for w in ("Flawless", "Solo", "Duo", "Trion",
                                               "Day One", "DayOne", "Contest")):
                        hits.append(frag)
                if hits:
                    print(f"== {s} ({len(body)//1024}KB) {len(hits)} hits")
                    for h in hits[:14]:
                        print("   |", h)
            except Exception as e:
                print(f"== {s} FAIL {type(e).__name__} {str(e)[:100]}")
        await ctx.close()


asyncio.run(main())
