"""全功能回归测试：对运行中的查询站做全接口内容断言
用法: python test_sweep.py [base_url]   (默认 http://127.0.0.1:8900)
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8900"
results = []


def get(path, timeout=90):
    from urllib.parse import quote
    if "?" in path:
        p, qs = path.split("?", 1)
        path = p + "?" + "&".join(
            f"{k}={quote(v, safe=chr(37))}" for k, v in (x.split("=", 1) for x in qs.split("&")))
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "sweep"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "ignore")


def check(name, path, must=(), none_of=("Traceback", "Internal Server Error"), timeout=90):
    try:
        code, body = get(path, timeout)
    except Exception as e:
        results.append((name, f"FAIL 请求异常 {e}"))
        return ""
    problems = [m for m in must if m not in body] + [n for n in none_of if n in body]
    if code != 200 or problems:
        results.append((name, f"FAIL http{code} 缺失/异常: {problems}"))
    else:
        results.append((name, "ok"))
    time.sleep(3)
    return body


PLAYERS = ["Wj%238984", "goldenmidi%230582"]

for p in PLAYERS:
    tag = "Wj" if "Wj" in p else "goldenmidi"
    b = check(f"{tag}/总览", f"/card?name={p}&mode=all", must=("生涯概况", "角色", "PVP 生涯", "智谋 生涯"))
    pvp_must = ("PVP 熔炉竞技场战绩", "最近对局", "胜率", "生涯统计")
    pve_must = ("PVE 战绩", "通关率", "生涯统计")
    if tag == "Wj":  # 只有 Wj 的样本确定有试炼/突袭记录，避免对样本不足的账号误报
        pvp_must += ("模式细分", "奥斯里斯试炼")
        pve_must += ("突袭任务",)
    check(f"{tag}/PVP", f"/card?name={p}&mode=pvp", must=pvp_must)
    check(f"{tag}/PVE", f"/card?name={p}&mode=pve", must=pve_must,
          none_of=("Traceback", "Internal Server Error", "tagl'>失败"))
    check(f"{tag}/智谋", f"/card?name={p}&mode=gambit", must=("智谋战绩", "胜率"))
    check(f"{tag}/战绩", f"/card?name={p}&mode=history", must=("对局", "mtag"))
    br = check(f"{tag}/Raid", f"/card?name={p}&mode=raid")
    bd = check(f"{tag}/地牢", f"/card?name={p}&mode=dungeon", must=("地牢",))
    check(f"{tag}/称号", f"/card?name={p}&mode=titles",
          must=("称号", "传承称号", "进度", "可镀金 / 已镀金", "ptag gild'>可镀金", "class='gildable'"))
    fb = check(f"{tag}/锻造", f"/card?name={p}&mode=patterns",
               must=("完成数", "phead", "pcount", "pgrid"),
               none_of=("Traceback", "Internal Server Error", "异域催化"))
    # 智能排序：顶部必须是没集齐的组（已全部集齐的组整组垫底）
    if fb:
        import re as _re
        heads = _re.findall(r"pcount'[^>]*>(\d+)\s*/\s*(\d+)<", fb)
        if len(heads) > 1 and int(heads[0][0]) >= int(heads[0][1]):
            results.append((f"{tag}/锻造排序", "FAIL 首组已集齐，未集齐的组没排在最前"))
        else:
            results.append((f"{tag}/锻造排序", "ok"))
    # 从 Raid 页取一个副本详情
    if br and "rrow" in br:
        import re
        m = re.search(r"mode=raidg&amode=(\d+)&base=([^&']+)", br)
        if m:
            check(f"{tag}/Raid详情", f"/card?name={p}&mode=raidg&amode={m.group(1)}&base={m.group(2)}",
                  must=("通关次数", "无暇"))
        else:
            results.append((f"{tag}/Raid详情", "FAIL 详情链接未带 amode/base"))
    # 地牢详情：必须沿用 amode=82，否则会拿突袭历史筛出 0/0/0（历史 bug）
    if bd:
        import re as _re
        m2 = _re.search(r"mode=raidg&amode=82&base=([^&']+)", bd)
        if m2:
            body2 = check(f"{tag}/地牢详情", f"/card?name={p}&mode=raidg&amode=82&base={m2.group(1)}",
                          must=("通关次数", "无暇", "最近对局"),
                          none_of=("Traceback", "Internal Server Error", "tagl'>失败"))
            if body2:
                results.append((f"{tag}/地牢详情非空", "ok" if "副本 " not in body2 else "FAIL 标题回退为哈希"))

# 武器:异域/金色/ legend/催化/带枪托
for name, q, must in [
    ("异域-枯萎囤积", "2357297366", ("催化", "枯萎囤积催化", "固定")),
    # 催化改成按武器插槽定位后的回归项：全面爆发的中文催化名与「武器名+催化」对不上，
    # 早先整块不显示；社区数值「Nanite Damage is increased by 25%」也必须出现
    ("异域-全面爆发", "3824673936", ("全面爆发催化", "Nanite Damage")),
    ("传奇-秋风", "2150012407", ("Perk 池", "枪管")),
    ("锻造-新太平洋", "2459087496", ("Perk 池",)),
]:
    b = check(f"武器{name}", f"/weapon?hash={q}", must=must)
    if name.startswith("传奇") and b:
        results.append(("武器详情-属性条", "ok" if "sbar" in b else "FAIL 无属性条"))
# 武器图鉴：全量索引 + 页内筛选（原「武器查询」页已并入图鉴，导航入口和页面都撤了）
c = check("武器图鉴", "/catalog", must=("武器图鉴", "d2nav", "facets"))
if c:
    results.append(("图鉴-无旧入口", "ok" if "/weapons" not in c else "FAIL 仍有指向 /weapons 的链接"))
# 老入口直接访问：要么 404，要么被送到图鉴
try:
    code, body = get("/weapons?q=秋风")
    results.append(("老武器查询页", "ok" if "武器图鉴" in body else f"FAIL http{code} 未跳转"))
except urllib.error.HTTPError as e:
    results.append(("老武器查询页", "ok" if e.code == 404 else f"FAIL http{e.code}"))

# Perk
for q in ("蜻蜓", "热力四射", "狂暴", "旋风 opportunit".replace(" opportunit", "nice")):
    pass
for q in ("蜻蜓", "热力四射", "狂暴", "测距仪", "禅意时刻"):
    b = check(f"perk-{q}", f"/perks?q={q}", must=("wcard",))
    if b:
        has_ci = "社区数据" in b
        results.append((f"perk-{q}-中文数值", "ok" if has_ci else "warn 无社区数值"))

# 首页/面板
b = check("首页", "/", must=("总览", "武器图鉴"))
if b:
    # Bot 面板已并进上方导航，右上角那个角落按钮撤了
    results.append(("首页-无角落按钮", "ok" if 'class="corner"' not in b else "FAIL 右上角按钮仍在"))
    # 两个生涯武器标签页 + 各自独立的赛季下拉（PVE 默认当前赛季）
    problems = []
    for token in ('data-m="wpve"', 'id="scope"', 'id="scope_pve"', 'data-for="wpve"',
                  "JOB_TABS", "wpve:'/start_wpve'", "syncScopeBar", "scopeKey(m)"):
        if token not in b:
            problems.append(token)
    if "__SCOPES" in b:
        problems.append("模板占位符未替换")
    if 'value="s' not in b:
        problems.append("赛季下拉为空")
    results.append(("首页/PVE生涯武器入口", "ok" if not problems else f"FAIL 缺失 {problems}"))
    # PVE 下拉默认必须选中当前赛季（第一个 <option> 带 selected）
    import re as _re
    m = _re.search(r'<select id="scope_pve"[^>]*>(.*?)</select>', b, _re.S)
    sel = m.group(1) if m else ""
    ok = "selected" in sel and "当前赛季" in sel and "/start_wpve" in b
    results.append(("首页/PVE默认范围", "ok" if ok else "FAIL PVE 下拉未默认当前赛季"))
# 本周轮换：exe 里主界面 / HTTPS 回跳 / QQ bot 各跑一个事件循环，共享 httpx 客户端时
# 会报「Event object ... is bound to a different event loop」→ 整页「本周轮换获取失败」
check("本周轮换", "/rotation", must=("本周轮换", "突袭", "d2nav", "width:900px"),
      none_of=("Traceback", "Internal Server Error", "获取失败", "Event loop"))
check("光尘商店", "/eververse", must=("d2nav", "width:900px"),
      none_of=("Traceback", "Internal Server Error", "Event loop"))
check("面板", "/panel", must=("Bot 后端管理",))
# 英文/繁体词条（build_locale_index.py → manifest_index/name_i18n.json）：面板的联想与
# 查询接口也要认，不然 QQ 侧改了这里悄悄退化没人发现
check("词条/英文武器名", "/api/suggest?type=weapon&q=gjallar", must=("加拉尔号角",))
check("词条/繁体武器名", "/api/suggest?type=weapon&q=龍之氣息", must=("龙息",))
check("词条/英文perk", "/perks?q=Incandescent", must=("辉耀炽热",))
check("词条/英文套装", "/armorsets?q=Seventh%20Seraph", must=("第七炽天使",))
b = check("bot状态", "/api/bot/status")
check("bot群API", "/api/bot/groups")

# PGCR:从战绩页取一场
try:
    code, body = get("/card?name=Wj%238984&mode=history")
    import re
    m = re.search(r"/pgcr\?i=(\d+)", body)
    if not m:
        code2, body2 = get("/card?name=Wj%238984&mode=pvp")
        m = re.search(r"/pgcr\?i=(\d+)", body2)
    if m:
        check("对局详情PGCR", f"/pgcr?i={m.group(1)}", must=("玩家排行",))
    else:
        results.append(("对局详情PGCR", "SKIP 无对局链接"))
except Exception as e:
    results.append(("对局详情PGCR", f"FAIL {e}"))

# 后台任务:生涯武器 + 热力图
def job_flow(kind, start_url, result_url, extra="", must=()):
    try:
        j = {}
        for attempt in range(3):
            code, body = get(f"{start_url}?name=Wj%238984{extra}")
            j = json.loads(body)
            if j.get("job"):
                break
            time.sleep(45)  # Bungie 限流退避
        if not j.get("job"):
            results.append((kind, f"FAIL 未返回job 响应={body[:80]}"))
            return
        jid = j["job"]
        for _ in range(150):
            code, body = get(f"/job/{jid}", timeout=20)
            st = json.loads(body)
            if st.get("status") == "done":
                break
            if st.get("status") == "error":
                results.append((kind, f"FAIL 任务错误 {st.get('error')}"))
                return
            time.sleep(2)
        code, body = get(f"{result_url}?job={jid}")
        missing = [t for t in must if t not in body]
        ok = code == 200 and "Traceback" not in body and not missing
        results.append((kind, "ok" if ok else f"FAIL 结果页 http{code} 缺失{missing}"))
    except Exception as e:
        results.append((kind, f"FAIL {e}"))

job_flow("PVP生涯武器", "/start_wpvp", "/wpvp_result",
         must=("PVP 生涯武器", "爆头率", "总击杀"))
job_flow("PVE生涯武器", "/start_wpve", "/wpve_result",
         must=("PVE 生涯武器", "当前赛季", "爆头率", "wrow"))
job_flow("热力图", "/start_heat", "/heat_result")

print()
fails = 0
for name, r in results:
    mark = "✗" if r.startswith("FAIL") else ("!" if r.startswith("warn") else "✓")
    if r.startswith("FAIL"):
        fails += 1
    print(f"{mark} {name}: {r}")
print(f"\n共 {len(results)} 项,失败 {fails}")
