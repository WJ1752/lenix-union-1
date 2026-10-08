"""光尘商店「时间强关联」测试（不联网：假 GetVendors 返回 + 可拨动的时钟）

背景（2026-10-08 用户报「光尘商店貌似还是没刷新」）：光尘货架的周期**不是每天 1 点**，
官方 GetVendors 每个 vendor 自带 nextRefreshDate，实测这栏是**周重置**（10-07 01:00 刷新、
10-14 01:00 换）。旧实现按「每天 1 点换缓存键」重取，2026-10-07 那轮周重置撞上官方维护、
货架冻在上一周，于是重取回来的还是旧货架、还落了盘——一整天都把旧货当真。

覆盖：
1. 时间口径：_ev_day / _ev_reset_prev / _ev_parse_when（含 9999 哨兵）
2. 归属点推算 _ev_stamp：周周期首跑回推、跨刷新点用上一轮官方值（最准）、官方延迟时归属不动
3. 有效期判定 _ev_reason：TTL 30 分钟、复位点后 10 分钟、过刷新点 10 分钟节流、
   维护窗口内抓的 / 早于最近一次维护恢复 → 一律重取
4. eververse_store 端到端：缓存命中不重复打接口、过刷新点自动换轮、官方延迟出 stale、
   空货架拦下不落缓存、force 强制重取、落盘缓存跨进程复用（ver 不符作废）
5. 卡片：抬头标注「哪天的 1 点刷新的货架 / 下次刷新 / 抓取时刻」，官方延迟出警告条
"""
import asyncio
import datetime
import datetime as _dt
import json
import os
import sys
import time as _time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bot_cards  # noqa: E402
import destiny_data as d2  # noqa: E402

FAILS = []
OKS = []


def check(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if extra and not cond else ""))


TZ = d2.EV_TZ


def ts(s: str) -> float:
    """北京时间字符串 → epoch（写测试用）"""
    return datetime.datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class Clock:
    """把 destiny_data 里的 time.time() 挪到测试指定的时刻（strftime 等照旧转发真模块）"""

    def __init__(self, t: float):
        self.t = t

    def time(self):
        return self.t

    def __getattr__(self, k):
        return getattr(_time, k)


def patch_clock(t: float):
    d2.time = Clock(t)


def restore_clock():
    d2.time = _time


# ---------- 1. 时间口径 ----------
def test_time_basics():
    print("\n[1] 时间口径")
    check("1 点前算前一天", d2._ev_day(ts("2026-10-08 00:30")) == "2026-10-07")
    check("1 点整算当天", d2._ev_day(ts("2026-10-08 01:00")) == "2026-10-08")
    check("1 点后算当天", d2._ev_day(ts("2026-10-08 08:20")) == "2026-10-08")
    prev = d2._ev_dt(d2._ev_reset_prev(ts("2026-10-08 08:20")))
    check("复位点回退到当天 1 点",
          (prev.month, prev.day, prev.hour, prev.minute) == (10, 8, 1, 0), prev)
    prev0 = d2._ev_dt(d2._ev_reset_prev(ts("2026-10-08 00:30")))
    check("1 点前回退到前一天 1 点", (prev0.month, prev0.day) == (10, 7), prev0)
    w = d2._ev_parse_when("2026-10-13T17:00:00Z")
    check("官方 Z 时间 → 北京时间", abs(w - ts("2026-10-14 01:00")) < 1, w)
    check("9999 哨兵当没有", d2._ev_parse_when("9999-12-31T23:59:59.999Z") == 0)
    check("坏值当没有", d2._ev_parse_when("不是时间") == 0 and d2._ev_parse_when(None) == 0)


# ---------- 2. 归属点推算 ----------
def test_stamp():
    print("\n[2] 归属刷新点推算 _ev_stamp")
    nr = ts("2026-10-14 01:00")          # 周三 01:00 = 周重置
    now = ts("2026-10-08 08:20")
    d = d2._ev_stamp({"next_refresh": nr, "sections": [{"items": [1]}]}, now, None)
    check("首跑：周周期回推到上周三 01:00",
          abs(d["refresh_at"] - ts("2026-10-07 01:00")) < 1, d2._ev_dt(d["refresh_at"]))
    check("首跑：周期=7 天", d["cycle_days"] == 7, d["cycle_days"])
    check("首跑：归属日=10-07", d["day"] == "2026-10-07", d["day"])
    check("首跑：没过刷新点，不算 stale", d["stale"] is False)

    d = d2._ev_stamp({"next_refresh": ts("2026-10-09 01:00"), "sections": []},
                     now, None)
    check("日周期回推到今天 01:00",
          abs(d["refresh_at"] - ts("2026-10-08 01:00")) < 1, d2._ev_dt(d["refresh_at"]))
    check("日周期=1 天", d["cycle_days"] == 1, d["cycle_days"])

    prev = {"data": {"next_refresh": ts("2026-10-07 01:00"),
                     "refresh_at": ts("2026-09-30 01:00"), "cycle_days": 7}}
    d = d2._ev_stamp({"next_refresh": nr, "sections": []}, ts("2026-10-07 01:05"), prev)
    check("跨刷新点：用上一轮官方说的刷新点当本轮起点",
          abs(d["refresh_at"] - ts("2026-10-07 01:00")) < 1, d2._ev_dt(d["refresh_at"]))

    prev2 = {"data": {"next_refresh": ts("2026-10-07 01:00"),
                      "refresh_at": ts("2026-09-30 01:00"), "cycle_days": 7}}
    d = d2._ev_stamp({"next_refresh": ts("2026-10-07 01:00"), "sections": []},
                     ts("2026-10-08 08:20"), prev2)
    check("官方延迟（刷新点已过仍回旧货架）：归属点是上一轮、标 stale",
          abs(d["refresh_at"] - ts("2026-09-30 01:00")) < 1 and d["stale"], d)
    check("官方延迟：周期仍按 7 天记", d["cycle_days"] == 7, d["cycle_days"])

    d = d2._ev_stamp({"next_refresh": 0.0, "sections": []}, now, None)
    check("官方没给刷新点：退回「最近一个 1 点」",
          abs(d["refresh_at"] - ts("2026-10-08 01:00")) < 1, d2._ev_dt(d["refresh_at"]))


# ---------- 3. 有效期判定 ----------
def test_reason():
    print("\n[3] 有效期判定 _ev_reason")
    real_suspect, real_since = d2.bst.suspect_at, d2.bst.clean_since
    try:
        d2.bst.suspect_at = lambda t: False
        d2.bst.clean_since = lambda: 0.0
        nr = ts("2026-10-14 01:00")
        ent = {"data": {"sections": [{"items": [1]}], "next_refresh": nr,
                        "refresh_at": ts("2026-10-07 01:00"),
                        "fetched_at": ts("2026-10-08 08:00")}}
        check("刚核过：直接用", d2._ev_reason(ent, ts("2026-10-08 08:20")) == "")
        check("过了 30 分钟 TTL：重取",
              "分钟" in d2._ev_reason(ent, ts("2026-10-08 08:31")),
              d2._ev_reason(ent, ts("2026-10-08 08:31")))

        ent2 = {"data": dict(ent["data"], refresh_at=ts("2026-10-08 08:00"))}
        check("复位点后 10 分钟内不重复问",
              d2._ev_reason(ent2, ts("2026-10-08 08:08")) == "")
        check("复位点后 10 分钟外重取（官方常晚切换）",
              d2._ev_reason(ent2, ts("2026-10-08 08:12")) != "")

        ent3 = {"data": dict(ent["data"], next_refresh=ts("2026-10-08 01:00"))}
        check("过官方刷新点但 10 分钟内刚问过：先用着",
              d2._ev_reason(ent3, ts("2026-10-08 08:05")) == "")
        check("过官方刷新点且超过节流：重取",
              "官方刷新点" in d2._ev_reason(ent3, ts("2026-10-08 08:20")))

        d2.bst.suspect_at = lambda t: t == ts("2026-10-08 08:00")
        check("维护窗口内抓的货架：重取",
              "维护窗口" in d2._ev_reason(ent, ts("2026-10-08 08:20")))
        d2.bst.suspect_at = lambda t: False
        d2.bst.clean_since = lambda: ts("2026-10-08 08:10")
        check("早于最近一次维护恢复：重取",
              "维护恢复" in d2._ev_reason(ent, ts("2026-10-08 08:20")))
        check("空 sections 的缓存不认", d2._ev_reason({"data": {"sections": []}}, 0) != "")
    finally:
        d2.bst.suspect_at, d2.bst.clean_since = real_suspect, real_since


# ---------- 4. eververse_store 端到端 ----------
SRC = {"212": ["测试枪皮", "武器皮肤", "异域", "/i1.png", "/s1.jpg"],
       "213": ["测试飞船", "飞船", "传说", "/i2.png", "/s2.jpg"],
       "214": ["测试着色器", "着色器", "传说", "/i3.png", ""]}


class FakeVendors:
    """假 GetVendors：vendor 实体给周值、商品给日值（真实形态就是这两层），并计数"""

    def __init__(self):
        self.calls = 0
        self.items = [("212", 1250, "光尘"), ("213", 2000, "光尘"), ("214", 300, "光尘")]
        self.vendor_iso = "2026-10-13T17:00:00Z"      # → 10-14 01:00（vendor 实体的周值）
        self.item_iso = "2026-10-08T17:00:00Z"        # → 10-09 01:00（商品级 override，日值）
        self.empty = False

    def resp(self):
        self.calls += 1
        vd, sd = {}, {}
        for _, _, vendors in d2.EV_SECTIONS:
            for vh in vendors:
                vd[vh] = {"nextRefreshDate": self.vendor_iso}
                sd[vh] = {"saleItems": {}}
        # 把测试物品塞进「主要光尘优惠」的第一个 vendor（带商品级 override）
        vh0 = d2.EV_SECTIONS[0][2][0]
        sd[vh0]["saleItems"] = {
            str(n): {"itemHash": h, "overrideNextRefreshDate": self.item_iso,
                     "costs": [{"itemHash": "2817410917", "quantity": amount}]}
            for n, (h, amount, _) in enumerate([] if self.empty else self.items)}
        return {"vendors": {"data": vd}, "sales": {"data": sd}}


async def fake_responses(self):
    return [self.resp()]


def prep_store(tmp_path: str, fake: FakeVendors):
    d2._EV_INDEX = dict(SRC)
    d2._EV_CACHE.clear()
    d2._ev_cache_path = lambda: tmp_path
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    real = d2._ev_vendor_responses
    d2._ev_vendor_responses = lambda: fake_responses(fake)
    return real


def test_next_refresh_field():
    print("\n[4] 刷新时刻取值：商品级 override 优先")
    fake = FakeVendors()
    nr = d2._ev_next_refresh([fake.resp()])
    check("商品 override（每天 1 点）压过 vendor 周值",
          abs(nr - ts("2026-10-09 01:00")) < 1, d2._ev_dt(nr))
    fake.item_iso = ""
    check("商品没给 override 时退回 vendor 值",
          abs(d2._ev_next_refresh([fake.resp()]) - ts("2026-10-14 01:00")) < 1)
    check("9999 哨兵当没有",
          d2._ev_next_refresh([{"vendors": {"data": {"2168194999": {
              "nextRefreshDate": "9999-12-31T23:59:59.999Z"}}},
              "sales": {"data": {}}}]) == 0.0)


def test_store():
    print("\n[5] eververse_store 端到端（每天 1 点换架）")
    tmp = os.path.join(ROOT, "_rtest", "_ev_time_cache.json")
    fake = FakeVendors()
    real = prep_store(tmp, fake)
    real_suspect, real_since = d2.bst.suspect_at, d2.bst.clean_since
    d2.bst.suspect_at = lambda t: False
    d2.bst.clean_since = lambda: 0.0
    try:
        patch_clock(ts("2026-10-08 08:20"))
        store = asyncio.run(d2.eververse_store())
        check("首查真的打了接口", fake.calls == 1, fake.calls)
        check("货架商品拿到", sum(len(s["items"]) for s in store["sections"]) == 3)
        check("归属 10-08 01:00 那轮",
              abs(store["refresh_at"] - ts("2026-10-08 01:00")) < 1, d2._ev_dt(store["refresh_at"]))
        check("下次刷新 10-09 01:00", abs(store["next_refresh"] - ts("2026-10-09 01:00")) < 1)
        check("周期=1 天（每天一刷）", store["cycle_days"] == 1, store["cycle_days"])
        check("归属日=10-08", store["day"] == "2026-10-08", store["day"])
        check("抓取时刻记下", abs(store["fetched_at"] - ts("2026-10-08 08:20")) < 1)
        check("没过刷新点，不 stale", store["stale"] is False)
        check("就是当前这一轮", d2.ev_behind(store, ts("2026-10-08 08:20")) is False)

        store = asyncio.run(d2.eververse_store())
        check("20 分钟内复用缓存（不重复打接口）", fake.calls == 1, fake.calls)

        patch_clock(ts("2026-10-08 08:55"))          # 超过 30 分钟 TTL
        asyncio.run(d2.eververse_store())
        check("过了 TTL 自动跟官方核一次", fake.calls == 2, fake.calls)

        d2._EV_CACHE.clear()
        asyncio.run(d2.eververse_store())
        check("落盘缓存能读回（不再打接口）", fake.calls == 2, fake.calls)
        saved = json.load(open(tmp, encoding="utf-8"))
        check("落盘记录带 ver/时间戳", saved.get("ver") == d2.EV_CACHE_VER
              and saved["data"].get("next_refresh"), list(saved))

        # 次日 01:00 换架：过了刷新点必须换轮
        patch_clock(ts("2026-10-09 01:05"))
        fake.item_iso = "2026-10-09T17:00:00Z"       # → 10-10 01:00
        store = asyncio.run(d2.eververse_store())
        check("过刷新点自动重取", fake.calls == 3, fake.calls)
        check("新一轮归属点 = 上一轮官方说的刷新点",
              abs(store["refresh_at"] - ts("2026-10-09 01:00")) < 1, d2._ev_dt(store["refresh_at"]))
        check("新一轮：下次刷新 10-10 01:00",
              abs(store["next_refresh"] - ts("2026-10-10 01:00")) < 1)
        check("新一轮不算 stale / 不是 behind",
              store["stale"] is False and d2.ev_behind(store, ts("2026-10-09 01:05")) is False)
        check("归属日跟着换", store["day"] == "2026-10-09", store["day"])

        # 官方晚切：刷新点到了但接口还给上一轮货（override 停在过去）
        patch_clock(ts("2026-10-10 01:06"))
        fake.item_iso = "2026-10-09T17:00:00Z"
        store = asyncio.run(d2.eververse_store())
        check("官方晚切时重取一次", fake.calls == 4, fake.calls)
        check("官方晚切：标 stale", store["stale"] is True)
        check("官方晚切：归属点仍是上一轮（不把旧货标成新轮）",
              abs(store["refresh_at"] - ts("2026-10-09 01:00")) < 1, d2._ev_dt(store["refresh_at"]))
        check("官方晚切：behind=True（卡片据此标「今天的新货架还没取到」）",
              d2.ev_behind(store, ts("2026-10-10 01:06")) is True)
        n = fake.calls
        asyncio.run(d2.eververse_store())
        check("官方晚切：10 分钟内不重复打接口", fake.calls == n, fake.calls)
        patch_clock(ts("2026-10-10 01:20"))
        asyncio.run(d2.eververse_store())
        check("官方晚切：过 10 分钟再核一次", fake.calls == n + 1, fake.calls)

        patch_clock(ts("2026-10-10 01:22"))
        d2._EV_CACHE.clear()
        asyncio.run(d2.eververse_store(force=True))
        check("force=True 强制重取", fake.calls == n + 2, fake.calls)

        # 维护窗口内抓的 / 早于维护恢复的货架一律重取（用户报的「没刷新」就是维护期货架冻住）
        patch_clock(ts("2026-10-10 02:00"))
        d2.bst.suspect_at = lambda t: True
        d2._EV_CACHE.clear()
        asyncio.run(d2.eververse_store())
        check("维护窗口内的货架重取", fake.calls == n + 3, fake.calls)
        d2.bst.suspect_at = lambda t: False
        d2.bst.clean_since = lambda: ts("2026-10-10 01:30")
        before = json.load(open(tmp, encoding="utf-8"))["data"]["fetched_at"]
        patch_clock(ts("2026-10-10 02:30"))
        d2._EV_CACHE.clear()
        asyncio.run(d2.eververse_store())
        check("早于最近一次维护恢复的货架重取", fake.calls == n + 4, fake.calls)
        check("重取后落盘时间戳往前走",
              json.load(open(tmp, encoding="utf-8"))["data"]["fetched_at"] > before)
        d2.bst.clean_since = lambda: 0.0

        # 空货架：拦下、不落缓存（缓存里仍是上一份）
        before = json.load(open(tmp, encoding="utf-8"))["data"]["fetched_at"]
        patch_clock(ts("2026-10-10 03:00"))
        fake.empty = True
        try:
            asyncio.run(d2.eververse_store(force=True))
            check("空货架必须报错", False)
        except d2.DataSuspiciousError:
            check("空货架必须报错", True)
        after = json.load(open(tmp, encoding="utf-8"))["data"]["fetched_at"]
        check("空货架不覆盖落盘缓存", before == after)

        # 版本不符的旧缓存作废
        fake.empty = False
        patch_clock(ts("2026-10-10 03:30"))
        d = json.load(open(tmp, encoding="utf-8"))
        d["ver"] = d2.EV_CACHE_VER - 1
        json.dump(d, open(tmp, "w", encoding="utf-8"))
        d2._EV_CACHE.clear()
        n = fake.calls
        asyncio.run(d2.eververse_store())
        check("ver 不符的旧缓存作废重取", fake.calls == n + 1, fake.calls)
    finally:
        restore_clock()
        d2._ev_vendor_responses = real
        d2.bst.suspect_at, d2.bst.clean_since = real_suspect, real_since
        d2._EV_CACHE.clear()
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------- 6. 卡片标注 ----------
def test_card():
    print("\n[6] 卡片时间标注")
    store = {"sections": [{"name": "主要光尘优惠", "cur": "光尘", "items": [
        {"hash": "212", "n": "测试枪皮", "ty": "武器皮肤", "tier": "异域",
         "icon": "http://x/i.png", "shot": "http://x/s.jpg", "cost": 1250, "cur": "光尘",
         "big": True}]}],
        "refresh_at": ts("2026-10-08 01:00"), "next_refresh": ts("2026-10-09 01:00"),
        "fetched_at": ts("2026-10-08 08:20"), "cycle_days": 1, "stale": False,
        "day": "2026-10-08"}
    patch_clock(ts("2026-10-08 08:20"))
    try:
        html = bot_cards.eververse_card(store)
        check("标注归属：本轮货架 10月8日 01:00（周四） 刷新",
              "本轮货架" in html and "10月8日 01:00（周四） 刷新" in html)
        check("标下次刷新 10月9日 01:00（周五）",
              "下次刷新" in html and "10月9日 01:00（周五）" in html)
        check("标抓取时刻 10月8日 08:20", "数据抓取" in html and "10月8日 08:20" in html)
        check("口径写明每天 01:00 刷新，不再出现周重置",
              "每天 01:00 刷新" in html and "周重置" not in html)
        check("叶脚写明归属那一轮", "10月8日 01:00（周四） 那轮" in html)
        check("当天货架正常时不报警", "新货架还没取到" not in html)
        check("没有「官方货架」那套提示", "官方货架" not in html)

        behind = dict(store, refresh_at=ts("2026-10-07 01:00"),
                      next_refresh=ts("2026-10-08 01:00"), stale=True, day="2026-10-07")
        html = bot_cards.eververse_card(behind)
        check("还是上一轮时，时间条上写明缺的是哪一轮",
              "10月7日 01:00（周三） 刷新" in html
              and "还没取到 10月8日 01:00（周四） 这轮新货架" in html
              and "手里仍是 10月7日 01:00（周三） 那轮" in html)
        check("不再另出提示块", "evstale" not in html)
    finally:
        restore_clock()


# ---------- 7. 时钟口径：全盘北京时间 ----------
class _StrictDateTime(_dt.datetime):
    """不带 tz 的 fromtimestamp/now/today 一律报错——模拟「本机时区不是北京时间」。

    在 +08 的机器上，把逻辑写回本机时间也看不出区别；这个假模块让那种写法当场炸，
    测试立刻抓住（用户要求：全盘时钟只认中国北京时间）。
    """

    @classmethod
    def fromtimestamp(cls, t, tz=None):
        if tz is None:
            raise AssertionError("fromtimestamp 没带时区（用了本机时区）")
        return super().fromtimestamp(t, tz)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            raise AssertionError("datetime.now() 没带时区（用了本机时区）")
        return super().now(tz)

    @classmethod
    def today(cls):
        raise AssertionError("datetime.today() 用了本机时区")


class _StrictDTModule:
    datetime = _StrictDateTime

    def __getattr__(self, k):
        return getattr(_dt, k)


def test_beijing_clock():
    print("\n[7] 时钟口径：全盘北京时间")
    import bot_scheduler
    real_d2, real_sched = d2.datetime, bot_scheduler.datetime
    now = ts("2026-10-08 08:20")
    try:
        d2.datetime = _StrictDTModule()
        bot_scheduler.datetime = _StrictDTModule()
        check("_ev_dt / _cn_now 都带时区", d2._ev_dt(now).utcoffset() == datetime.timedelta(hours=8)
              and d2._cn_now().utcoffset() == datetime.timedelta(hours=8))
        check("_ev_day 算得出", d2._ev_day(now) == "2026-10-08")
        check("复位点算得出",
              d2._ev_dt(d2._ev_reset_prev(now)).strftime("%m-%d %H:%M") == "10-08 01:00")
        check("当前轮 / behind 算得出", d2.ev_behind(
            {"refresh_at": ts("2026-10-07 01:00")}, now) is True)
        check("_ev_stamp 算得出（周期与归属点）",
              d2._ev_stamp({"next_refresh": ts("2026-10-09 01:00")}, now, None)["cycle_days"] == 1)
        check("宗师周键算得出", d2._gm_week_key(now) == "2026-W41", d2._gm_week_key(now))
        check("老九在场窗口算得出（周六 01:00 到场）",
              d2._xur_present(ts("2026-10-10 00:59")) is False
              and d2._xur_present(ts("2026-10-10 01:00")) is True)
        check("老九下次抵达算得出",
              d2._ev_dt(d2._xur_next_arrival(now)).strftime("%m-%d %H:%M") == "10-10 01:00")
        check("扭曲星球算得出", (d2.distortion_now() or {}).get("ok") in (True, False))
        check("轮换周键算得出", bot_scheduler.rotation_week_key() == "2026-10-07",
              bot_scheduler.rotation_week_key())
    finally:
        d2.datetime, bot_scheduler.datetime = real_d2, real_sched


def main():
    test_time_basics()
    test_stamp()
    test_reason()
    test_next_refresh_field()
    test_store()
    test_card()
    test_beijing_clock()
    print(f"\n通过 {len(OKS)} / 共 {len(OKS) + len(FAILS)}")
    if FAILS:
        print("失败：")
        for f in FAILS:
            print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
