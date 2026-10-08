"""经 9222 CDP 拿 dungeon.report 前端 bundle,搜徽章文案全集"""
import asyncio, os, re
from playwright.async_api import async_playwright

OUT = "_rtest/drep_bundles"
os.makedirs(OUT, exist_ok=True)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
        ctx = b.contexts[0]
        page = await ctx.new_page()
        await page.goto("https://dungeon.report/", timeout=60000, wait_until="commit")
        for i in range(45):
            await page.wait_for_timeout(2000)
            t = await page.title()
            if t and "moment" not in t.lower() and "稍候" not in t:
                break
        await page.wait_for_timeout(6000)
        html = await page.evaluate("() => fetch('/').then(r=>r.text())")
        srcs = sorted(set(re.findall(r'src="(/_next/static/[^"]+\.js)"', html)))
        print("BUNDLES:", len(srcs), flush=True)
        pat = re.compile(r'.{60}(?:[Ff]lawless|Solo Flawless|Duo|Trion|Day ?One|Contest).{60}', re.S)
        for i, s in enumerate(srcs):
            try:
                body = await page.evaluate("u=>fetch(u).then(r=>r.text())", s)
                fn = os.path.join(OUT, f"b{i:02d}.js")
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(body)
                hits = []
                for m in pat.finditer(body):
                    frag = m.group(0).replace("\n", " ")
                    if any(w in frag for w in ("Flawless", "Solo", "Duo", "Trion",
                                               "Day One", "DayOne", "Contest")):
                        hits.append(frag)
                if hits:
                    print(f"== {s} ({len(body)//1024}KB) {len(hits)} hits")
                    for h in hits[:20]:
                        print("   |", h)
            except Exception as e:
                print(f"== {s} FAIL {type(e).__name__} {str(e)[:100]}")
        await page.close()
        await b.close()


asyncio.run(main())
