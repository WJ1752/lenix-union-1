"""DIM 板块的浏览器自检：控制台报错 + DOM 断言 + 交互（拖拽/标签/弹窗/配装编辑器）。

写操作（搬运/装备/标签写盘）全部用 route 拦下来断言请求体，不打到 Bungie、不改用户账号。
用法：先起 _rtest/dim_serve.py（8907），再跑本脚本。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8907"
SHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOT, exist_ok=True)

PROBLEMS = []


def log(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)
    if not ok:
        PROBLEMS.append(msg)


async def new_page(br, block_writes=True):
    page = await br.new_page(viewport={"width": 1680, "height": 950})
    page.on("pageerror", lambda e: PROBLEMS.append("pageerror: %s" % e))
    def _con(m):
        if m.type == "error":
            loc = (m.location or {}).get("url", "")
            PROBLEMS.append("console.error: %s @ %s" % (m.text[:60], loc[:130]))
    page.on("console", _con)
    page.on("requestfailed", lambda r: PROBLEMS.append(
        "requestfailed: %s | %s" % (r.url[:110], str(r.failure)[:50])))
    if block_writes:
        calls = {}

        async def handler(route):
            calls.setdefault("req", []).append((route.request.url, route.request.post_data))
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"ok": True, "equipped": 1, "moved": 1}))
        for pat in ("**/api/dim/move", "**/api/dim/equip", "**/api/dim/tag",
                    "**/api/dim/apply", "**/api/dim/lock", "**/api/dim/note"):
            await page.route(pat, handler)
        page._calls = calls
    return page


async def check_inventory(br):
    print("[背包页 /dim]")
    page = await new_page(br)
    await page.goto(BASE + "/dim", wait_until="domcontentloaded")
    await page.wait_for_selector(".item", timeout=30000)
    await page.wait_for_timeout(1200)
    n_item = await page.locator(".item").count()
    n_store = await page.locator(".store").count()
    n_sec = await page.locator(".sec").count()
    n_chip = await page.locator(".chip").count()
    n_slot = await page.locator(".slot[data-slot-bucket]").count()
    log(n_item > 50, "物品格子 %d 个" % n_item)
    log(n_store == 4, "仓库列 %d 个（3 角色 + 保险库）" % n_store)
    log(n_sec > 10, "小节 %d 个" % n_sec)
    log(n_chip >= 13, "筛选 chip %d 个" % n_chip)
    log(n_slot >= 8, "可拖拽装备槽 %d 个" % n_slot)

    # 搜索语法
    await page.fill("#q", "is:weapon")
    await page.wait_for_timeout(400)
    vis = await page.locator(".item:not(.searchHidden)").count()
    log(0 < vis < n_item, "is:weapon 过滤后可见 %d / %d" % (vis, n_item))
    await page.fill("#q", "tag:favorite")
    await page.wait_for_timeout(400)
    log(await page.locator(".item:not(.searchHidden)").count() == 0, "tag:favorite 当前无匹配（应为 0）")
    await page.fill("#q", "")
    await page.wait_for_timeout(300)

    # 悬停出弹窗
    await page.locator(".item").first.hover()
    await page.wait_for_timeout(1400)
    pop_vis = await page.locator("#pop:not(.hidden)").count()
    body = (await page.locator("#pop").inner_text())[:60].replace("\n", " / ")
    log(pop_vis == 1, "悬停弹窗出现：%s" % body)

    await page.keyboard.press("Escape")
    # 点格子把弹窗固定住，再在弹窗里打标签（写盘请求被拦截）
    await page.locator(".item").first.click()
    await page.wait_for_timeout(900)
    log(await page.locator("#pop:not(.hidden)").count() == 1, "点格子后弹窗固定打开")
    await page.locator("#pop .tagbtn[data-tag=favorite]").first.click()
    await page.wait_for_timeout(500)
    calls = page._calls.get("req", [])
    tag_ok = any("/api/dim/tag" in u and '"favorite"' in (d or "") for u, d in calls)
    log(tag_ok, "点标签发出 /api/dim/tag favorite 请求")
    n_tagicon = await page.locator('.item .ic-t[title="收藏"]').count()
    log(n_tagicon == 1, "格子上出现收藏图标 %d 个（应为 1）" % n_tagicon)

    # 右键菜单（先按 Esc 收起固定住的弹窗）
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)
    await page.locator(".item").nth(2).click(button="right")
    await page.wait_for_timeout(400)
    mi = await page.locator("#menu .mi").count()
    log(mi >= 8, "右键菜单项 %d 个" % mi)
    await page.keyboard.press("Escape")

    # 拖拽：把角色背包装备拖到保险库列（写请求被拦截）
    src = page.locator(".store:not(.vault) .sec[data-store] .item").first
    dst = page.locator(".store.vault .store-body")
    before = len(page._calls.get("req", []))
    await src.drag_to(dst)
    await page.wait_for_timeout(600)
    calls = page._calls.get("req", [])
    moved = [c for c in calls[before:] if "/api/dim/move" in c[0]]
    log(bool(moved), "拖到保险库发出 /api/dim/move：%s" % (moved[0][1] if moved else "无"))

    # 拖拽：武器拖到同角色的武器槽 → 走 equip
    WEAP = {"1498876634": "动能武器", "2465295065": "能量武器", "953998645": "威能武器"}
    done_eq = False
    for bkt, nm in WEAP.items():
        sec = page.locator('.store:not(.vault) .sec[data-store]', has=page.locator("b", has_text=nm))
        if not await sec.count():
            continue
        srcw = sec.first.locator(".item").first
        slotw = page.locator('.slot[data-slot-bucket="%s"]' % bkt).first
        if not await srcw.count() or not await slotw.count():
            continue
        before = len(page._calls.get("req", []))
        await srcw.drag_to(slotw)
        await page.wait_for_timeout(700)
        eq = [c for c in page._calls.get("req", [])[before:] if "/api/dim/equip" in c[0]]
        log(bool(eq), "拖 %s 到装备槽发出 /api/dim/equip：%s" % (nm, eq[0][1] if eq else "无"))
        done_eq = True
        break
    if not done_eq:
        log(False, "没找到可用的武器小节来测拖拽装备")

    await page.screenshot(path=os.path.join(SHOT, "dim_inv.png"))
    await page.close()


async def check_loadouts(br):
    print("[配装页 /dim/loadouts]")
    page = await new_page(br)
    await page.goto(BASE + "/dim/loadouts", wait_until="domcontentloaded")
    await page.wait_for_selector(".inld-hd", timeout=30000)
    await page.wait_for_timeout(1500)
    txt = await page.locator("#body").inner_text()
    log("游戏内配装" in txt, "有「游戏内配装」区块")
    log("DIM 配装" in txt, "有「DIM 配装」区块")
    log("当前装备" in txt, "有「当前装备」区块")
    n_ig = await page.locator(".lcard").count()
    log(n_ig > 0, "游戏内配装卡 %d 张" % n_ig)

    # 保存当前装备 → 编辑器
    await page.locator('[data-a=save-cur]').first.click()
    await page.wait_for_timeout(500)
    log(await page.locator("#lname").count() == 1, "保存当前装备打开了编辑器")
    n_ed = await page.locator("#editor .slot").count()
    log(n_ed >= 8, "编辑器槽位 %d 个" % n_ed)
    # 点槽位 → 候选列表（每个槽位都该有候选；空说明槽位号和桶号比错了）
    npick_total, empty_buckets = 0, []
    for i in range(min(6, await page.locator("#editor .slot[data-pick]").count())):
        sl = page.locator("#editor .slot[data-pick]").nth(i)
        b = await sl.get_attribute("data-pick")
        await sl.click()
        await page.wait_for_timeout(350)
        n = await page.locator(".pick .item").count()
        npick_total += n
        if n == 0:
            empty_buckets.append(b)
    log(npick_total > 0 and not empty_buckets,
        "槽位候选合计 %d 件（无候选的槽：%s）" % (npick_total, empty_buckets or "无"))
    if npick_total:
        await page.locator(".pick .item").first.click()
        await page.wait_for_timeout(400)
        log(await page.locator(".pick").count() == 0, "选中后候选面板关闭")
    # 保存（写盘：真的会存进 dim_user.json，测完删掉）
    await page.fill("#lname", "自检临时配装")
    await page.locator('[data-a=save]').click()
    await page.wait_for_timeout(2000)
    rows = page.locator('.ldrow[data-id]')
    n_row = await rows.count()
    log(n_row >= 1, "保存后 DIM 配装列表 %d 套" % n_row)
    names = await page.locator(".ldrow .ln").all_inner_texts()
    log(any("自检临时配装" in x for x in names), "列表里出现「自检临时配装」")
    await page.screenshot(path=os.path.join(SHOT, "dim_loadouts.png"))
    # 清理
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    idx = next((i for i, x in enumerate(names) if "自检临时配装" in x), -1)
    if idx >= 0:
        await page.locator('.ldrow[data-id] [data-a=del]').nth(idx).click()
        await page.wait_for_timeout(1500)
        left = await page.locator(".ldrow .ln").all_inner_texts()
        log(not any("自检临时配装" in x for x in left), "临时配装已删除")
    await page.close()


async def check_optimizer(br):
    print("[配装器 /dim/optimizer]")
    page = await new_page(br)
    await page.goto(BASE + "/dim/optimizer", wait_until="domcontentloaded")
    await page.wait_for_selector(".prio", timeout=30000)
    await page.wait_for_timeout(1200)
    n_prio = await page.locator(".prio").count()
    log(n_prio == 6, "属性优先级行 %d 行" % n_prio)
    log(await page.locator('[data-a=run]').count() == 1, "有「开始搜索」按钮")
    # 设两个条件后搜索
    await page.select_option('select[data-prio="144602215"]', "1")
    await page.fill('input[data-min="144602215"]', "30")
    await page.click("[data-a=run]")
    await page.wait_for_timeout(4000)
    txt = await page.locator("#res").inner_text()
    n_set = await page.locator(".setrow").count()
    log(n_set > 0, "搜出组合 %d 套（%s）" % (n_set, txt.split("\n")[0][:70]))
    log("套" in txt and "扫描" in txt, "结果区显示套数/扫描量/耗时")
    await page.screenshot(path=os.path.join(SHOT, "dim_optimizer.png"))
    await page.close()


async def check_manage(br):
    print("[管理器 /dim/manage]")
    page = await new_page(br)
    await page.goto(BASE + "/dim/manage", wait_until="domcontentloaded")
    await page.wait_for_selector(".mtab tr", timeout=30000)
    await page.wait_for_timeout(1000)
    rows = await page.locator(".mtab tbody tr").count()
    log(rows > 20, "表格行 %d 行" % rows)
    await page.locator(".mtab tbody tr input[type=checkbox]").first.check()
    await page.wait_for_timeout(300)
    log(await page.locator(".mtab tbody tr.on").count() == 1, "勾选后行高亮")
    await page.locator('[data-a="tag"][data-t="keep"]').first.click()
    await page.wait_for_timeout(800)
    calls = page._calls.get("req", [])
    log(any("/api/dim/tag" in u for u, d in calls), "批量打标签发出请求")
    await page.screenshot(path=os.path.join(SHOT, "dim_manage.png"))
    await page.close()


async def check_triumphs(br):
    print("[进度页 /dim/triumphs]")
    page = await new_page(br)
    await page.goto(BASE + "/dim/triumphs", wait_until="domcontentloaded")
    await page.wait_for_selector(".rc", timeout=40000)
    await page.wait_for_timeout(500)
    log(await page.locator(".tnode").count() > 3, "成就分类树 %d 节点" % await page.locator(".tnode").count())
    log(await page.locator(".rc").count() > 20, "成就卡片 %d 张" % await page.locator(".rc").count())
    await page.close()


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            br = await pw.chromium.launch(channel="msedge")
        except Exception:
            br = await pw.chromium.launch()
        await check_inventory(br)
        await check_loadouts(br)
        await check_optimizer(br)
        await check_manage(br)
        await check_triumphs(br)
        await br.close()
    print("\n=== 结论 ===")
    if PROBLEMS:
        print("有 %d 个问题：" % len(PROBLEMS))
        for p in PROBLEMS[:30]:
            print("  -", p)
        sys.exit(1)
    print("全部通过")


asyncio.run(main())
