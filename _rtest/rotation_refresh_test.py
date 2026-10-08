"""轮换刷新口径测试（不联网：假页面 + 可拨动的时钟）

背景（2026-10-09 用户报「轮换有明显问题，每周宗师刷新错误，武器错误」）：
线上 gm_cache.json 实证 —— 2026-10-07 01:02（Bungie 复位后两分钟）抓到的 lfcarry 页
还是**上一周**的宗师（Exodus Crash / The Slammer），却按新周键（2026-W41）落了盘，
于是整周都在说上周的宗师和武器。对照游戏内截图，本周应是：
    宗师 = The Arms Dealer（军火交易商）/ 欧洲无人区 / 首通掉落 Ouster Engine（驱逐引擎·榴弹发射器）
页面这版还把武器措辞换了：老正则只认「weekly challenge weapon is …」，
新页面写的是「The featured weapon is Ouster Engine, a grenade launcher.」——两头都漏。

覆盖：
1. 周界口径 _gm_week_key / _gm_week_span（周三 01:00 前后各差一周）
2. 页面上「周界」的抽取 _page_week_marks（两种句式）
3. gm_this_week 端到端：新措辞 / 老措辞 / 陈旧页拦下且不落盘 / 熔断期内不再打接口 /
   超过重试上限带警告照收 / force 绕过熔断 / 缓存命中不重复打接口
4. 武器名与目的地：冠词容错、目的地按活动 hash 反查官方定义
5. lost_sectors_today：换天判定（日期与 9 区都没变 → 拦；变一处 → 收；日期抓不到 → 照收）
6. 卡片：陈旧时警告写在脸上、缺省行带原因
7. 调度器：推送推迟上限（不会无限推迟）
"""
import asyncio
import datetime
import json
import os
import sys
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bot_cards  # noqa: E402
import bot_scheduler  # noqa: E402
import destiny_data as d2  # noqa: E402

FAILS = []
OKS = []


def check(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if extra and not cond else ""))


TZ = d2.EV_TZ


def ts(s: str) -> float:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def time(self):
        return self.t

    def __getattr__(self, k):
        return getattr(_time, k)


_real_time = d2.time


def patch_clock(t: float):
    d2.time = Clock(t)


def restore_clock():
    d2.time = _real_time


TMP = os.path.join(ROOT, "_rtest", "_rot_refresh_tmp")
os.makedirs(TMP, exist_ok=True)
GM_PATH = os.path.join(TMP, "gm_cache.json")
LS_PATH = os.path.join(TMP, "lost_sector_cache.json")


def clean():
    for p in (GM_PATH, LS_PATH):
        if os.path.exists(p):
            os.remove(p)


# ---------- 假页面 ----------

def ls_card(h, slug, name, dest, setname="Seventh Seraph",
            champs=("Barrier",), elems=("Void",)):
    """首页一张卡片的骨架（照抄真实页面：背景图 URL 里带 hash → 卡片头 → h2/p → alt 文本）"""
    alts = "".join(f'<img alt="Champion type: {c}">' for c in champs)
    alts += "".join(f'<img alt="Shield type: {e}">' for e in elems)
    return ('<div class="card" style="background-image:url(https://cf-assets.d2lostsector.report/'
            f'for-website/{h}/{h}.jpg?width=480)">'
            '<div class="card-header d-flex flex-column h-100 p-4 text-white">'
            f'<a class="text-decoration-none text-white" href="/sector/{slug}">'
            f'<h2 class="pt-0 mb-0">{name}</h2><p class="fs-4 mb-0">{dest}</p></a>'
            '<img src="https://www.bungie.net/common/destiny2_content/icons/'
            f'd620468ee50ca78200461d27e5776825.jpg" alt="{setname} set">{alts}</div></div>')


def ls_page(label, cards):
    """首页：日历日期（#calendar3 后面那个）+ 若干卡片"""
    head = ('<div class="container pt-2"><div class="row pt-3"><div class="col">'
            '<p class="fs-6 mb-0"><svg class="bi me-2 mb-1"><use xlink:href="#calendar3">'
            f'</use></svg>{label}</p><h1 class="pb-2 fw-bold">Today&#x27;s World Lost Sectors</h1>'
            '</div></div><div class="row">')
    return head + "".join(cards) + "</div></div>"


LS_DAY1 = ls_page("Oct 8, 2026", [ls_card(2310698359, "veles_labyrinth", "Veles Labyrinth", "Cosmodrome")])
LS_DAY2 = ls_page("Oct 9, 2026", [ls_card(2571435841, "aphelions_rest", "Aphelion's Rest", "Dreaming City")])


def gm_page(gm="The Arms Dealer", through="Tuesday, October 13",
            week="October 6 to October 13", weapon="Ouster Engine", wtype="grenade launcher",
            phrasing="featured"):
    """lfcarry 轮换页的可解析骨架（句式照抄真实页面）"""
    if phrasing == "featured":
        wline = f"The featured weapon is {weapon}, a {wtype}."
    else:
        wline = f"The weekly challenge weapon is {weapon}, a {wtype}."
    return ("<html><body><nav>Grandmaster Trials of Osiris Skip the grind FAQ This week's roster</nav>"
            "<main><p>This week's roster</p>"
            f"<p>The week of {week}, 2026, ends at Tuesday reset, 17:00 UTC. "
            "Featured raids are Salvation's Edge and Vault of Glass. "
            "Featured dungeons are Warlord's Ruin and Pit of Heresy.</p>"
            f"<p>Grandmaster: {gm}. {gm} is the Grandmaster Vanguard Alert. "
            f"It stays up through {through}, at 17:00 UTC. {wline}</p>"
            "</main></body></html>")


def prep():
    """把落盘路径指到临时目录，并把联网 / 官方定义反查换成桩"""
    clean()
    d2._data_dir = lambda: TMP
    d2._GM_GUARD.clear()
    d2._LS_GUARD.clear()
    d2._GM_GUARD.wait = 300.0
    d2._LS_GUARD.wait = 300.0

    async def dest_zh(h):
        return {"1446478334": "欧洲无人区", "707920309": "涅索斯"}.get(str(h), "测试目的地")

    d2._activity_dest_zh = dest_zh


PAGES = {"gm": "", "ls": ""}
CALLS = {"gm": 0, "ls": 0}


async def fake_web(url):
    if "lfcarry" in url:
        CALLS["gm"] += 1
        return PAGES["gm"]
    if "d2lostsector" in url:
        CALLS["ls"] += 1
        return PAGES["ls"]
    raise AssertionError("测试里不该有别的抓取：" + url)


# ---------- 1. 周界口径 ----------
def test_week_span():
    print("\n[1] 周界口径（北京周三 01:00 = UTC 周二 17:00）")
    patch_clock(ts("2026-10-07 01:00"))
    check("刚过复位点：算新一周", d2._gm_week_key() == "2026-W41", d2._gm_week_key())
    patch_clock(ts("2026-10-07 00:59"))
    check("复位点前一分钟：还是上一周", d2._gm_week_key() == "2026-W40", d2._gm_week_key())
    patch_clock(ts("2026-10-13 23:00"))
    check("下周二深夜：仍是这一周", d2._gm_week_key() == "2026-W41", d2._gm_week_key())
    patch_clock(ts("2026-10-09 03:00"))
    a, b = d2._gm_week_span()
    check("周界起点 = 周二 17:00 UTC", (a.month, a.day, a.hour) == (10, 6, 17), a)
    check("周界终点 = 下周二 17:00 UTC", (b.month, b.day, b.hour) == (10, 13, 17), b)


# ---------- 2. 页面周界抽取 ----------
def test_marks():
    print("\n[2] 页面周界抽取 _page_week_marks")
    txt = ("It stays up through Tuesday, October 13, at 17:00 UTC. "
           "The week of October 6 to October 13, 2026, ends at Tuesday reset, 17:00 UTC. "
           "Repeat clears drop loot until the October 13 reset.")
    check("两种句式都抓到 10-13", (10, 13) in d2._page_week_marks(txt), d2._page_week_marks(txt))
    old = "It stays up through Tuesday, October 6, at 17:00 UTC."
    check("上一周的页面抓到的是 10-06", d2._page_week_marks(old) == {(10, 6)},
          d2._page_week_marks(old))
    check("跨月也算得对",
          (11, 3) in d2._page_week_marks("through Tuesday, November 3, at 17:00 UTC"))
    check("没写周界时返回空集", d2._page_week_marks("Grandmaster: The Arms Dealer.") == set())


# ---------- 3/4. 宗师端到端 ----------
def test_gm():
    print("\n[3] 宗师端到端（本周页面 = 军火交易商 / 驱逐引擎）")
    prep()
    d2._web_get_text = fake_web
    patch_clock(ts("2026-10-09 03:10"))
    PAGES["gm"] = gm_page()
    CALLS["gm"] = 0
    g = asyncio.run(d2.gm_this_week())
    check("副本名 = The Arms Dealer", g["en"] == "The Arms Dealer", g.get("en"))
    check("中文名 = 军火交易商", g["zh"] == "军火交易商", g.get("zh"))
    check("目的地（按 hash 反查）= 欧洲无人区", g["dest_zh"] == "欧洲无人区", g.get("dest_zh"))
    check("横图是真图不是占位图",
          g["pgcr"].endswith("strike_the_arms_dealer.jpg"), g.get("pgcr"))
    check("武器 = 驱逐引擎（榴弹发射器）",
          (g["weapon"].get("zh"), g["weapon"].get("type")) == ("驱逐引擎", "榴弹发射器"),
          g.get("weapon"))
    check("武器英文名与类型词都留下", g["weapon"].get("en") == "Ouster Engine"
          and g["weapon"].get("type_en") == "grenade launcher", g.get("weapon"))
    check("不算陈旧", g["stale"] is False and g["warn"] == "")
    check("周键 = 2026-W41", g["week"] == "2026-W41", g.get("week"))
    saved = json.load(open(GM_PATH, encoding="utf-8"))
    check("落盘 ver=3 / key=2026-W41", saved["ver"] == 3 and saved["key"] == "2026-W41", list(saved))
    n = CALLS["gm"]
    asyncio.run(d2.gm_this_week())
    check("同一周命中缓存不重复打接口", CALLS["gm"] == n, CALLS["gm"])

    print("\n[4] 老措辞 / 冠词容错")
    PAGES["gm"] = gm_page(gm="Exodus Crash", through="Tuesday, October 13",
                          week="October 6 to October 13",
                          weapon="The Slammer", wtype="sword", phrasing="challenge")
    d2._GM_GUARD.clear()
    clean()
    g2 = asyncio.run(d2.gm_this_week(force=True))
    check("老句式（weekly challenge weapon is …）也认",
          g2["weapon"].get("zh") == "急锋" and g2["weapon"].get("type") == "刀剑", g2.get("weapon"))
    check("没写目的地时改用 hash 反查（Exodus Crash → 涅索斯）",
          g2["dest_zh"] == "涅索斯", g2.get("dest_zh"))
    check("冠词容错：The Slammer / Slammer 都指向同一把",
          d2._weapon_by_en("Slammer").get("zh") == d2._weapon_by_en("The Slammer").get("zh") == "急锋",
          (d2._weapon_by_en("Slammer"), d2._weapon_by_en("The Slammer")))

    print("\n[5] 陈旧页（复位后对方还没换页）：拦下、不落盘、熔断")
    clean()
    d2._GM_GUARD.clear()
    CALLS["gm"] = 0
    PAGES["gm"] = gm_page(gm="Exodus Crash", through="Tuesday, October 6",
                          week="September 29 to October 6",
                          weapon="The Slammer", wtype="sword", phrasing="challenge")
    try:
        asyncio.run(d2.gm_this_week())
        check("陈旧页必须报错", False, "居然过了")
    except d2.DataSuspiciousError as exc:
        check("陈旧页报 DataSuspiciousError 且说明周界对不上", "10月6日" in str(exc), exc)
    check("陈旧页不落盘（否则整周都错）", not os.path.exists(GM_PATH))
    check("抓了一次", CALLS["gm"] == 1, CALLS["gm"])
    try:
        asyncio.run(d2.gm_this_week())
    except d2.DataSuspiciousError:
        pass
    check("熔断期内不再打接口（5 分钟内）", CALLS["gm"] == 1, CALLS["gm"])
    try:
        asyncio.run(d2.gm_this_week(force=True))
        check("force 也不放行陈旧数据（宁可缺，不出上一周的宗师）", False, "居然过了")
    except d2.DataSuspiciousError:
        check("force 绕过熔断照抓（=又打了一次接口），但陈旧数据照样拦",
              CALLS["gm"] == 2, CALLS["gm"])

    print("\n[6] 重试到上限：带警告照收（板块不会被永久锁死）")
    d2._GM_GUARD.clear()
    clean()
    for i in (1, 2):
        try:
            asyncio.run(d2.gm_this_week(force=True))
            check(f"第 {i} 次仍应拦下", False, "没拦")
        except d2.DataSuspiciousError:
            check(f"第 {i} 次拦下", True)
    g4 = asyncio.run(d2.gm_this_week(force=True))
    check("第 3 次照收", g4["en"] == "Exodus Crash", g4.get("en"))
    check("照收时带 stale + warn（卡片写在脸上）", g4["stale"] is True and g4["warn"], g4.get("warn"))
    check("照收才落盘", os.path.exists(GM_PATH))

    print("\n[7] 页面没写周界时用「跟上一轮一字不差」兜底")
    d2._GM_GUARD.clear()
    clean()
    PAGES["gm"] = gm_page()
    asyncio.run(d2.gm_this_week(force=True))                 # 先存下本周正常的一条
    PAGES["gm"] = ("<html><body><p>Grandmaster: The Arms Dealer. "
                   "The featured weapon is Ouster Engine, a grenade launcher.</p></body></html>")
    d2._GM_GUARD.clear()
    try:
        asyncio.run(d2.gm_this_week(force=True))
        check("没写周界 + 内容与上一轮一致 → 拦", False, "居然过了")
    except d2.DataSuspiciousError as exc:
        check("没写周界 + 内容与上一轮一致 → 拦", "一字不差" in str(exc), exc)


# ---------- 8. 遗失区域换天判定 ----------
def test_ls():
    print("\n[8] 遗失区域：页面还没换天就别收")
    prep()
    d2._web_get_text = fake_web
    patch_clock(ts("2026-10-09 01:10"))
    PAGES["ls"] = LS_DAY1
    CALLS["ls"] = 0
    a = asyncio.run(d2.lost_sectors_today())
    check("当天数据收下并落盘", a["ok"] and os.path.exists(LS_PATH))
    check("页面日期留档（当指纹用）", a["label"] == "Oct 8, 2026", a.get("label"))
    check("中文名走本地映射（Veles Labyrinth → 溪谷迷宫）",
          a["sectors"][0]["zh"] == "溪谷迷宫", a["sectors"][0].get("zh"))
    check("不陈旧", a["stale"] is False)

    patch_clock(ts("2026-10-10 01:05"))
    PAGES["ls"] = LS_DAY1                                    # 页面还没换天
    d2._LS_GUARD.clear()
    try:
        asyncio.run(d2.lost_sectors_today())
        check("新一天但页面日期/区域都没变 → 拦", False, "居然过了")
    except d2.DataSuspiciousError as exc:
        check("新一天但页面日期/区域都没变 → 拦", "还没换天" in str(exc), exc)
    saved = json.load(open(LS_PATH, encoding="utf-8"))
    check("拦下时不覆盖昨天那份缓存", saved["key"] == "2026-10-09", saved.get("key"))

    n = CALLS["ls"]
    patch_clock(ts("2026-10-10 01:06"))
    try:
        asyncio.run(d2.lost_sectors_today())
    except d2.DataSuspiciousError:
        pass
    check("熔断期内（5 分钟）不再打接口", CALLS["ls"] == n, CALLS["ls"])

    patch_clock(ts("2026-10-10 01:40"))                      # 过熔断窗口，页面也换了天
    PAGES["ls"] = LS_DAY2
    b = asyncio.run(d2.lost_sectors_today())
    check("过熔断后页面换了天就收下",
          b["day"] == "2026-10-10" and b["label"] == "Oct 9, 2026", b.get("day"))
    check("换天后落盘键跟着换",
          json.load(open(LS_PATH, encoding="utf-8"))["key"] == "2026-10-10")

    patch_clock(ts("2026-10-11 01:05"))
    PAGES["ls"] = ls_page("", [ls_card(2310698359, "veles_labyrinth", "Veles Labyrinth", "Cosmodrome")])
    d2._LS_GUARD.clear()
    c = asyncio.run(d2.lost_sectors_today())
    check("页面抓不到日期时判据自动失效（不会把板块锁死）",
          c["ok"] and c["label"] == "" and c["stale"] is False, c.get("label"))


# ---------- 9. 卡片 ----------
def test_card():
    print("\n[9] 卡片：陈旧 / 缺省都要写在脸上")
    blk = bot_cards._gm_block({"ok": True, "zh": "军火交易商", "dest_zh": "欧洲无人区",
                               "pgcr": "https://x/strike.jpg", "stale": True,
                               "weapon": {"zh": "驱逐引擎", "type": "榴弹发射器"}})
    check("陈旧标记出警告条", "⚠" in blk and "还没换轮" in blk)
    check("正常字段照出", "军火交易商" in blk and "驱逐引擎" in blk and "欧洲无人区" in blk)
    blk2 = bot_cards._gm_block({"ok": False, "why": "数据源还没换轮（第 1/2 次）"})
    check("缺省行写出原因", "没抓到宗师数据" in blk2 and "还没换轮" in blk2, blk2)
    blk3 = bot_cards._ls_block({"ok": False, "why": "页面还没换天"})
    check("遗失区域缺省行也带原因", "没抓到遗失区域" in blk3 and "还没换天" in blk3, blk3)
    ok = bot_cards.rotation_card(
        {"raids": ["救赎的边缘"], "dungeons": [], "label": "10月07日 - 10月13日", "matched": True},
        {}, {"ok": True, "sectors": []}, {"ok": True, "zh": "军火交易商"})
    check("整卡能渲染（宗师块在、遗失区域块缺省）", "本周轮换" in ok and "军火交易商" in ok)


# ---------- 10. 调度器推迟上限 ----------
def test_push_defer():
    print("\n[10] 推送推迟有上限")
    real_key = bot_scheduler.rotation_week_key
    bot_scheduler.rotation_week_key = lambda: "2026-10-07"
    bot_scheduler._PUSH_DEFER["key"], bot_scheduler._PUSH_DEFER["n"] = "", 0
    try:
        r = [bot_scheduler._push_defer() for _ in range(6)]
        check("前 4 次推迟、之后照推", r[:4] == [True] * 4 and r[4:] == [False, False], r)
        bot_scheduler.rotation_week_key = lambda: "2026-10-14"
        check("换周期后计数重置", bot_scheduler._push_defer() is True)
    finally:
        bot_scheduler.rotation_week_key = real_key


def main():
    try:
        test_week_span()
        test_marks()
        test_gm()
        test_ls()
        test_card()
        test_push_defer()
    finally:
        restore_clock()
        clean()
    print(f"\n通过 {len(OKS)} / 共 {len(OKS) + len(FAILS)}")
    if FAILS:
        print("失败：")
        for f in FAILS:
            print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
