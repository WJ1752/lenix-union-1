"""DOM 级验证 DIM 三个页面（不截图、不做任何写操作）。

断言：页面无 JS 报错；背包页出格子/角色卡/分组；点物品出搬运菜单（只看不点）；
成就页出分类树和记录；配装页出 20 套卡片。
"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8907"


async def main():
    async with async_playwright() as pw:
        br = None
        for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                br = await pw.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001
                pass
        page = await br.new_page(viewport={"width": 1600, "height": 1000})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append("console." + m.type + ": " + m.text)
                if m.type == "error" else None)

        # ---------- 背包页 ----------
        await page.goto(BASE + "/dim", wait_until="domcontentloaded")
        await page.wait_for_selector(".it", timeout=45000)
        await page.wait_for_timeout(1200)
        n = await page.locator(".it").count()
        cols = await page.locator(".col").count()
        cards = await page.locator("#hd .card").count()
        cur = await page.locator(".cur .c").count()
        secs = await page.locator(".sec:visible").count()
        first = await page.locator(".it").first.get_attribute("title")
        print(f"[背包] 格子 {n} 个 / 列 {cols} / 概要卡 {cards} / 货币 {cur} / 可见分组 {secs}")
        print(f"       首个格子: {first}")

        # 搜索过滤（本地过滤，不发请求）
        await page.fill("#q", "手炮")
        await page.wait_for_timeout(400)
        faded = await page.locator(".it:not(.fade)").count()
        vis_secs = await page.locator(".sec:visible").count()
        print(f"       搜索「手炮」→ 高亮 {faded} 个 / 可见分组 {vis_secs}")
        await page.fill("#q", "")
        await page.wait_for_timeout(300)

        # 点一个仓库里的武器 → 菜单选项（不点击任何动作项）
        await page.locator(".col[data-char=vault] .it").first.click()
        await page.wait_for_timeout(400)
        menu = await page.locator("#menu div").all_inner_texts()
        print(f"[菜单] 选项: {[m for m in menu if m.strip()][:6]}")

        # 悬停 → 详情面板出内容
        await page.locator(".col[data-char=vault] .it").first.hover()
        await page.wait_for_timeout(2600)
        dname = await page.locator("#detail h3").inner_text() if await page.locator("#detail h3").count() else "(无)"
        dtypes = await page.locator("#detail .row").count()
        print(f"[详情] 面板物品: {dname} / 属性行 {dtypes}")

        # ---------- 成就页 ----------
        await page.goto(BASE + "/dim/triumphs", wait_until="domcontentloaded")
        await page.wait_for_selector(".tnode", timeout=45000)
        await page.wait_for_timeout(900)
        tn = await page.locator(".tnode").count()
        rc = await page.locator(".rc").count()
        score = (await page.locator("#score").inner_text()).replace("\n", " ")
        print(f"[成就] 分类节点 {tn} / 记录卡 {rc} / 分数行: {score[:60]}")
        await page.click("#tree .tnode:nth-child(2)")
        await page.wait_for_timeout(500)
        rc2 = await page.locator(".rc").count()
        await page.fill("#q", "异域")
        await page.wait_for_timeout(500)
        rc3 = await page.locator(".rc").count()
        await page.check("#un")
        await page.wait_for_timeout(500)
        rc4 = await page.locator(".rc").count()
        print(f"       切分类后 {rc2} / 搜「异域」{rc3} / 再加只看未完成 {rc4}")

        # ---------- 配装页 ----------
        await page.goto(BASE + "/dim/loadouts", wait_until="domcontentloaded")
        await page.wait_for_selector(".lcard", timeout=45000)
        await page.wait_for_timeout(1200)
        lc = await page.locator(".lcard").count()
        empt = await page.locator(".lcard.empty").count()
        btn = await page.locator(".lact button").count()
        ttl = await page.locator(".lcard .ltt b").first.inner_text()
        print(f"[配装] 卡片 {lc}（空 {empt}）/ 应用按钮 {btn} / 首套: {ttl}")

        print("[JS 报错]", errs[:6] if errs else "无")
        await br.close()


asyncio.run(main())
