"""明细补读 / 缺口补读 / 续跑 的回归测试（2026-10-08 那批改动）

覆盖 destiny_data.py 的新行为：
  · pgcr_detail 三态（ok / gone / retry）与 pvp_match_contribution 的老口径包装
  · _pull_details 一轮内退避补读：重试成功只算一次、进度不越过 count_total、note 用完还原
  · 缺口写进 _AGG_CACHE 的 missing 名单（落盘），下一次同范围查询自动补拉
  · 老汇总 missed>0 但没记是哪几场 → 丢掉整段重算
  · 明细/汇总缓存「没读过盘就不落盘」（防止把磁盘上那份清空；force=True 才强写）
  · 续跑登记落盘 / 销账 / 启动时重放一次（幂等）

不碰 Bungie：client() 换成假响应、activity_history / get_profile / pgcr_detail 换桩，
落盘位置指到临时目录，不写仓库里的 pvp_weapon_cache.json 与 weapon_agg_cache.json。

跑法：.venv/Scripts/python.exe _rtest/detail_repair_test.py
"""
import asyncio
import datetime
import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

FAILED = []
STATS: dict[str, int] = {}     # case 名 → 断言条数
CASE = ["?"]
TMP = tempfile.mkdtemp(prefix="detail_repair_test_")
MID = "4611686018000000001"
CHARS = ["c1"]
# reset() 会把模块属性换成桩，这里各留一份真身，免得 case 之间互相污染
REAL = {"pgcr_detail": d2.pgcr_detail, "activity_history": d2.activity_history,
        "client": d2.client, "_schedule_miss_retry": d2._schedule_miss_retry,
        "save_seen_players": d2.save_seen_players}


def check(name, got, want):
    STATS[CASE[0]] = STATS.get(CASE[0], 0) + 1
    if got != want:
        FAILED.append(f"{CASE[0]} · {name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


def ok(name, cond, note=""):
    check(name + (f"（{note}）" if note else ""), bool(cond), True)


def today(offset: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


def match(day: str, instance: str) -> dict:
    return {"period": f"{day} 12:00", "mode": 5, "instance": instance}


class Fake:
    def __init__(self):
        self.rows: list[dict] = []     # 每个角色的对局历史（新→旧）
        self.hist_calls = 0            # activity_history 被调用次数
        self.detail_calls = 0          # 逐场明细拉取次数

    def reset(self):
        self.rows = []
        self.hist_calls = 0
        self.detail_calls = 0


F = Fake()
FAIL_DETAIL: set[str] = set()          # 这些 instance 一直 retry（补读也拿不到）


async def fake_history(mtype, mid, cid, mode, count=250, page=0):
    F.hist_calls += 1
    return F.rows[page * count: page * count + count]


async def fake_resolve(name):
    return {"mtype": 3, "mid": MID, "display": "Wj", "code": 8984}


async def fake_profile(mtype, mid):
    return {"characters": {"data": {c: {} for c in CHARS}}}


async def fake_detail(instance, mid):
    """逐场明细桩：FAIL_DETAIL 里的永远 retry，其余直接 ok"""
    F.detail_calls += 1
    if instance in FAIL_DETAIL:
        return ("retry", None)
    return ("ok", {"kills": 1, "precision": 0, "melee": 0, "grenade": 0, "super": 0,
                   "ability": 0,
                   "weapons": [{"name": "测试枪", "icon": "", "type": "手炮", "hash": "h1",
                                "kills": 1, "precision": 0}]})


class FakeHTTP:
    """d2.client() 的假身：按 instance 路由到预先造好的 httpx.Response（或异常）"""

    def __init__(self):
        self.route: dict = {}
        self.calls_by_instance: dict[str, int] = {}

    async def get(self, url, **kw):
        inst = str(url).rstrip("/").rsplit("/", 1)[-1]
        self.calls_by_instance[inst] = self.calls_by_instance.get(inst, 0) + 1
        r = self.route.get(inst)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise AssertionError(f"假客户端没配 {inst} 的响应")
        return r


def resp(payload) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return httpx.Response(200, content=body)


def pgcr_body(mid: str, kills: int = 7) -> dict:
    """一份最小的 PGCR 成功响应：本人条目 + 近战/精准 + 一把武器"""
    return {"ErrorCode": 1, "ErrorStatus": "Success", "Message": "Ok",
            "Response": {"entries": [{
                "player": {"destinyUserInfo": {"membershipId": mid}},
                "values": {"kills": {"basic": {"value": kills}}},
                "extended": {
                    "values": {"precisionKills": {"basic": {"value": 2}},
                               "weaponKillsMelee": {"basic": {"value": 1}},
                               "weaponKillsGrenade": {"basic": {"value": 0}},
                               "weaponKillsSuper": {"basic": {"value": 0}},
                               "weaponKillsAbility": {"basic": {"value": 0}}},
                    "weapons": [{"referenceId": 999999999999,
                                 "values": {
                                     "uniqueWeaponKills": {"basic": {"value": 3}},
                                     "uniqueWeaponPrecisionKills": {"basic": {"value": 1}}}}],
                }}]}}


def reset():
    """每个 case 开头清干净：内存表、落盘目录、桩、退避等待全复位"""
    d2.JOBS.clear()
    d2._JOB_DEDUP.clear()
    d2._JOB_QUEUE.clear()
    d2._JOB_RUNNING.clear()
    d2._JOB_TASK.clear()
    d2._JOB_RESUME.clear()
    for h in list(d2._MISS_TIMERS.values()):   # 别让上一轮排的自动补读落到本轮
        try:
            h.cancel()
        except Exception:  # noqa: BLE001 已经跑过/循环关了都无所谓
            pass
    d2._MISS_TIMERS.clear()
    d2._AGG_CACHE.clear()
    d2._PVP_MATCH_CACHE.clear()
    d2._pvp_cache_ready = False
    d2._agg_cache_ready = False
    d2._resumed_once = False
    d2._PGCR_RETRY_WAIT = 0     # 测试里不真等 8s/16s/45s 的退避
    d2._PGCR_STORM_WAIT = 0
    d2.pgcr_detail = REAL["pgcr_detail"]
    d2.activity_history = REAL["activity_history"]
    d2.client = REAL["client"]
    d2._schedule_miss_retry = REAL["_schedule_miss_retry"]
    d2.save_seen_players = lambda: None
    d2._writable_path = lambda name: os.path.join(TMP, name)
    F.reset()
    FAIL_DETAIL.clear()
    for name in os.listdir(TMP):
        p = os.path.join(TMP, name)
        if os.path.isfile(p):
            os.remove(p)


def job(jid: str, label: str = "测试任务", **kw) -> dict:
    rec = {"done": 0, "total": 1, "status": "running", "name": "Wj#8984", "result": None,
           "kind": "pvp", "who": "测试", "ts": time.time(), "started": time.time(),
           "label": label}
    rec.update(kw)
    d2.JOBS[jid] = rec
    return rec


def run_weapon(jid: str, cap: int = 0, scope: str = "all", kind: str = "pvp"):
    """照 job_engine_test 的参数口径调一次 _run_weapon_job（mode: pvp=5）"""
    return d2._run_weapon_job(jid, 3, MID, CHARS, "", "", scope, "全生涯", kind,
                             5 if kind == "pvp" else 7, cap, frozenset())


# ---------- 用例 ----------

async def case_pgcr_tristate():
    print("[1] pgcr_detail 三态：1653→gone / 36→retry / 有本人条目→ok（缓存命中不再请求）")
    reset()
    http = FakeHTTP()
    d2.client = lambda: http
    http.route = {
        "inst_gone": resp({"ErrorCode": 1653, "ErrorStatus": "DestinyPGCRNotFound"}),
        "inst_rate": resp({"ErrorCode": 36, "ErrorStatus": "RateLimitExceeded"}),
        "inst_ok": resp(pgcr_body(MID)),
        "inst_other": resp(pgcr_body("4611686018000000999")),
        "inst_junk": resp(b"<html>Bungie maintenance</html>"),
        "inst_boom": d2.bst.BungieMaintenanceError("维护中"),
    }
    check("ErrorCode 1653 → gone", await d2.pgcr_detail("inst_gone", MID), ("gone", None))
    check("ErrorCode 36（限流）→ retry",
          await d2.pgcr_detail("inst_rate", MID), ("retry", None))
    status, detail = await d2.pgcr_detail("inst_ok", MID)
    check("ErrorCode 1 且有本人条目 → ok", status, "ok")
    check("明细里的击杀数", detail["kills"], 7)
    check("明细里的精准击杀", detail["precision"], 2)
    check("明细里的近战击杀", detail["melee"], 1)
    check("明细里的武器条目",
          detail["weapons"],
          [{"name": "未知武器", "icon": "", "type": "", "hash": "999999999999",
            "kills": 3, "precision": 1}])
    ok("明细写进了 _PVP_MATCH_CACHE",
       d2._PVP_MATCH_CACHE.get("inst_ok", {}).get(MID) is detail)
    n_before = http.calls_by_instance.get("inst_ok")
    check("第二次调用直接命中缓存，仍是 ok",
          (await d2.pgcr_detail("inst_ok", MID))[0], "ok")
    check("没有对同一场再发请求", http.calls_by_instance.get("inst_ok"), n_before)
    check("ErrorCode 1 但名单里没有该 mid → gone",
          await d2.pgcr_detail("inst_other", MID), ("gone", None))
    check("响应不是 JSON → retry", await d2.pgcr_detail("inst_junk", MID), ("retry", None))
    check("维护闸门抛 BungieMaintenanceError → retry",
          await d2.pgcr_detail("inst_boom", MID), ("retry", None))

    print("[1b] pvp_match_contribution 是老口径包装：非 ok 一律 None")
    check("gone → None", await d2.pvp_match_contribution("inst_gone", MID), None)
    check("retry → None", await d2.pvp_match_contribution("inst_rate", MID), None)
    hit = await d2.pvp_match_contribution("inst_ok", MID)
    ok("ok → 明细 dict", bool(hit) and hit["kills"] == 7, hit)


async def case_pull_rounds():
    print("[2] 补读轮：前两轮 retry、第三轮 ok —— apply 只算一次、pending 清空、note 还原")
    reset()
    job("pj", label="PVP 生涯武器（全生涯）", total=1, done=0, note="起点")
    tries: dict[str, int] = {}
    notes: list[str] = []

    async def flaky(instance, mid):
        F.detail_calls += 1
        notes.append((d2.JOBS.get("pj") or {}).get("note") or "")
        tries[instance] = tries.get(instance, 0) + 1
        if tries[instance] <= 2:
            return ("retry", None)
        return ("ok", {"instance": instance})

    d2.pgcr_detail = flaky
    applied = []
    todo = [match(today(-1), "i1")]
    pending, gone = await d2._pull_details(
        "pj", MID, todo, lambda d: applied.append(d), {"i1"}, 1)
    check("同一场连试 3 遍才到手", tries, {"i1": 3})
    check("重试成功只算一次：apply 恰好被调用 1 次", len(applied), 1)
    check("apply 拿到的是拿到手的那份明细", applied[0], {"instance": "i1"})
    check("返回的 pending 为空", pending, [])
    check("官方无明细计数 0", gone, 0)
    check("done 按首遍推进、没越过 count_total", d2.JOBS["pj"]["done"], 1)
    check("补读期间的 note 借用任务自己的字段",
          notes, ["起点", "1 场明细补读中（第 2/3 遍）", "1 场明细补读中（第 3/3 遍）"])
    check("收尾把 note 还原成进来时那条", d2.JOBS["pj"]["note"], "起点")

    print("[2b] 多场混补读 + 官方确认没有：每场只记一次，done 不因补读虚高")
    job("pj2", label="PVP 生涯武器（全生涯）", total=3, done=0)
    tries.clear()
    applied2 = []

    async def flaky2(instance, mid):
        F.detail_calls += 1
        tries[instance] = tries.get(instance, 0) + 1
        if instance == "k_gone":
            return ("gone", None)
        if instance != "k1" and tries[instance] <= 2:
            return ("retry", None)
        return ("ok", {"instance": instance})

    d2.pgcr_detail = flaky2
    todo2 = [match(today(-1), "k1"), match(today(-2), "k2"), match(today(-3), "k_gone")]
    pending2, gone2 = await d2._pull_details(
        "pj2", MID, todo2, lambda d: applied2.append(d), {"k1", "k2", "k_gone"}, 3)
    check("k2 补读两遍后到手", tries.get("k2"), 3)
    check("k_gone 一遍就结案、不进补读名单", tries.get("k_gone"), 1)
    check("apply 每场只记一次", sorted(d["instance"] for d in applied2), ["k1", "k2"])
    check("返回的 pending 为空", pending2, [])
    check("gone 计数 1", gone2, 1)
    check("done 卡在 count_total 上（3 场拉了 3+2+2 次也没顶上 5）",
          d2.JOBS["pj2"]["done"], 3)


async def case_missing_gap():
    print("[3] 缺口落盘：3 场一直 retry → missed/missing/miss_tries 记进汇总缓存")
    reset()
    d2.activity_history = fake_history
    d2.pgcr_detail = fake_detail
    d2._load_agg_cache()                      # 走临时目录那份，别碰仓库里的缓存
    insts = [f"g{i}" for i in range(1, 9)]
    F.rows = [match(today(-i), insts[i - 1]) for i in range(1, 9)]
    FAIL_DETAIL.update(insts[:3])             # 头 3 场（今天-1/-2/-3）一直 retry
    key = f"{MID}|pvp|all"
    job("wp", label="PVP 生涯武器（全生涯）", name="Wj#8984")
    d2._note_resume("wp", {"type": "weapon", "name": "Wj#8984", "scope": "all",
                           "kind": "pvp", "key": key})
    sched = []
    real_sched = d2._schedule_miss_retry

    def spy_sched(jid, tries, n, **kw):
        sched.append((jid, tries, n))
        return real_sched(jid, tries, n, **kw)

    d2._schedule_miss_retry = spy_sched
    await run_weapon("wp")

    st = d2.JOBS["wp"]
    res = st["result"] or {}
    entry = d2._AGG_CACHE.get(key) or {}
    check("任务跑完（不是 error）", (st["status"], st.get("error")), ("done", None))
    check("枚举到 8 场", res.get("matches"), 8)
    check("缺口 3 场", entry.get("missed"), 3)
    want_missing = sorted(f"{insts[i]}@{today(-(i + 1))} 12:00" for i in range(3))
    check("missing 名单正是那 3 场（instance@period 格式）",
          sorted(entry.get("missing") or []), want_missing)
    check("miss_tries 自增到 1", entry.get("miss_tries"), 1)
    check("官方无明细数 0", entry.get("gone"), 0)
    check("result.missed 同口径", res.get("missed"), 3)
    check("result.gone 同口径", res.get("gone"), 0)
    check("covered = matches - missed - gone",
          (res.get("covered"), res.get("matches"), res.get("missed"), res.get("gone")),
          (5, 8, 3, 0))
    check("incomplete 与缺口占比自洽（3 场不够 5%）",
          res.get("incomplete"), res.get("missed") > max(50, res.get("matches") * 0.05))
    check("auto_retry 打开", res.get("auto_retry"), True)
    check("缺口名单也落了盘（临时目录那份）",
          (json.load(open(os.path.join(TMP, d2._AGG_CACHE_FILE), encoding="utf-8"))
           .get(key) or {}).get("missing"), want_missing)
    check("按 (jid, 轮次, 缺口数) 排了自动补读", sched, [("wp", 1, 3)])
    ok("自动补读的定时器排上了", "wp" in d2._MISS_TIMERS)
    check("任务被标成「有补读在等」，续跑名单不销", st.get("_miss_pending"), True)
    ok("续跑名单还在", "wp" in d2._JOB_RESUME)

    print("[3b] 清掉失败名单再跑一次同范围：那 3 场被重新拉，缺口清零")
    FAIL_DETAIL.clear()
    n0 = F.detail_calls
    job("wp2", label="PVP 生涯武器（全生涯）", name="Wj#8984")
    await run_weapon("wp2")
    st2 = d2.JOBS["wp2"]
    res2 = st2["result"] or {}
    entry2 = d2._AGG_CACHE.get(key) or {}
    check("第二次也是 done", (st2["status"], st2.get("error")), ("done", None))
    check("正好补拉那 3 场（明细请求 +3）", F.detail_calls - n0, 3)
    check("补读的旧账不动进度条（没有新增枚举）", res2.get("added"), 0)
    check("这次带上的是 3 场", res2.get("carried"), 3)
    check("missing 清空", entry2.get("missing"), [])
    check("missed 归零", entry2.get("missed"), 0)
    check("miss_tries 归零", entry2.get("miss_tries"), 0)
    check("covered 回到 8", res2.get("covered"), 8)
    check("缺口补齐 → 续跑名单销账", "wp" in d2._JOB_RESUME, False)
    check("没有新的自动补读被排上", sched, [("wp", 1, 3)])


async def case_stale_migrate():
    print("[4] 老缓存迁移：missed>0 却没记 missing 名单 → 丢掉整段重算")
    reset()
    d2.activity_history = fake_history
    d2.pgcr_detail = fake_detail
    d2._load_agg_cache()
    F.rows = [match(today(-i), f"m{i}") for i in range(1, 6)]
    key = f"{MID}|pvp|all"
    d2._AGG_CACHE[key] = {
        "scope_since": "", "scope_until": "",
        "weapons": {"h1": {"name": "测试枪", "icon": "", "type": "手炮",
                           "kills": 30, "precision": 0, "matches": 30}},
        "tot": {"kills": 30, "precision": 0, "melee": 0, "grenade": 0, "super": 0,
                "ability": 0},
        "matches": 30, "missed": 7, "gone": 0, "miss_tries": 1,
        "oldest": today(-30), "newest": today(-4), "newest_full": f"{today(-4)} 20:00",
        "cap": 5000, "capped": False, "updated": "2026-10-07 22:00:00"}
    job("old", label="PVP 生涯武器（全生涯）", name="Wj#8984")
    n0 = F.hist_calls
    with redirect_stdout(io.StringIO()) as buf:
        await run_weapon("old", cap=5000)
    log = buf.getvalue()
    st = d2.JOBS["old"]
    res = st["result"] or {}
    check("整段重算：活动历史被重新翻了一遍", F.hist_calls, n0 + 1)
    ok("note 里说明重新统计", "重新统计" in (st.get("note") or ""), st.get("note"))
    ok("日志里说明补不回来", "补不回来" in log)
    ok("没有把它当可复用的段（note 里没有「复用」）", "复用" not in (st.get("note") or ""))
    check("不再沿用老汇总的 30 场，按实际重算", res.get("matches"), 5)
    check("老汇总的 missed 一并丢掉", res.get("missed"), 0)
    check("覆盖范围按重算结果", res.get("range"), (today(-5), today(-1)))
    check("新汇总不再背老名单", d2._AGG_CACHE[key].get("missing"), [])


async def case_cache_guard():
    print("[5] 没读过盘的缓存不落盘：不会把磁盘上那份清空（force=True 才强写）")
    reset()
    p = os.path.join(TMP, d2._PVP_CACHE_FILE)
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"orig": {"k": 1}}')
    d2._PVP_MATCH_CACHE.clear()
    d2._PVP_MATCH_CACHE["inst_new"] = {MID: {"kills": 1}}
    d2._pvp_cache_ready = False
    d2._save_pvp_cache()
    with open(p, encoding="utf-8") as f:
        check("ready=False 时文件原样未变", f.read(), '{"orig": {"k": 1}}')
    d2._save_pvp_cache(force=True)
    with open(p, encoding="utf-8") as f:
        check("force=True 时被覆盖成内存里那份",
              json.load(f), {"inst_new": {MID: {"kills": 1}}})

    print("[5b] 汇总缓存同理")
    q = os.path.join(TMP, d2._AGG_CACHE_FILE)
    with open(q, "w", encoding="utf-8") as f:
        f.write('{"keep": 1}')
    d2._AGG_CACHE.clear()
    d2._AGG_CACHE["k"] = {"matches": 1}
    d2._agg_cache_ready = False
    d2._save_agg_cache()
    with open(q, encoding="utf-8") as f:
        check("ready=False 时文件原样未变", f.read(), '{"keep": 1}')


async def case_resume():
    print("[6] 续跑：_note_resume 落盘 / _clear_resume 销账 / 启动时重放一次")
    reset()
    desc1 = {"type": "weapon", "name": "某人#0001", "scope": "all", "kind": "pve",
             "key": "K"}
    job("jid1", label="PVE 生涯武器（全生涯）")
    d2._note_resume("jid1", desc1)
    saved = d2._load_job_state()
    check("jobs_state.json 能读回一条", len(saved), 1)
    check("描述原样", saved[0].get("desc"), desc1)
    check("jid 也写进去了", saved[0].get("jid"), "jid1")
    check("记下了发起人", saved[0].get("who"), "测试")
    check("标签也记了（提示语要用）", saved[0].get("label"), "PVE 生涯武器（全生涯）")
    ok("落的就是临时目录那份", os.path.exists(os.path.join(TMP, d2._RESUME_FILE)))
    d2._clear_resume("jid1")
    check("销账后读回空", d2._load_job_state(), [])
    check("内存表也空了", d2._JOB_RESUME, {})

    print("[6b] _clear_resume_key 按去重键销账（任务可能换过 jid）")
    job("jidk", label="带键的")
    d2._note_resume("jidk", {"type": "weapon", "name": "丙#3", "scope": "all",
                             "kind": "pvp", "key": "K3"})
    d2._note_resume("jidk2", {"type": "weapon", "name": "丙#3", "scope": "all",
                              "kind": "pvp", "key": "K3"})
    check("同一个 key 两条都在", len(d2._load_job_state()), 2)
    d2._clear_resume_key("K3")
    check("按 key 一次销掉两条", d2._load_job_state(), [])
    d2._clear_resume_key("没这个键")   # 不许炸
    check("销不存在的键也不炸", d2._load_job_state(), [])

    print("[6c] resume_saved_jobs：两条都重放、只跑一次（幂等）")
    job("jid2", label="A")
    job("jid3", label="B")
    desc2 = {"type": "weapon", "name": "甲#1", "scope": "all", "kind": "pvp", "key": "K2"}
    desc3 = {"type": "raid", "name": "乙#2", "mode": 4}
    d2._note_resume("jid2", desc2)
    d2._note_resume("jid3", desc3)
    calls = []
    real_relaunch = d2._relaunch_desc

    async def fake_relaunch(desc, who="", retry=False):
        calls.append((dict(desc), who, retry))
        return "jidX"

    d2._relaunch_desc = fake_relaunch
    try:
        d2._resumed_once = False
        out = await d2.resume_saved_jobs()
        check("返回续起来的任务标签", out, ["A", "B"])
        check("两条描述都交给了 _relaunch_desc", [c[0] for c in calls], [desc2, desc3])
        check("都按 retry=True 续跑（不复用刚结束的那份）",
              [c[2] for c in calls], [True, True])
        check("who 用落盘时的发起人", [c[1] for c in calls], ["测试", "测试"])
        ok("_resumed_once 变真", d2._resumed_once)
        check("第二次调用返回空列表（幂等）", await d2.resume_saved_jobs(), [])
        check("没有第二次重放", len(calls), 2)
    finally:
        d2._relaunch_desc = real_relaunch


async def main():
    d2.resolve_member = fake_resolve
    d2.get_profile = fake_profile
    real_bst = (d2.bst.is_down, d2.bst.guard, d2.bst.guard_sync)

    async def noop():
        return None

    d2.bst.is_down = lambda: False      # 测试里不维护（维护分支另有 maint_guard_test）
    d2.bst.guard = noop
    d2.bst.guard_sync = lambda: None
    try:
        for case in (case_pgcr_tristate, case_pull_rounds, case_missing_gap,
                     case_stale_migrate, case_cache_guard, case_resume):
            CASE[0] = case.__name__
            await case()
            print()
    finally:
        d2.bst.is_down, d2.bst.guard, d2.bst.guard_sync = real_bst
    print("各 case 断言数：")
    for k in (case.__name__ for case in (case_pgcr_tristate, case_pull_rounds,
                                        case_missing_gap, case_stale_migrate,
                                        case_cache_guard, case_resume)):
        print(f"  {k}: {STATS.get(k, 0)} 条")
    if FAILED:
        print(f"{len(FAILED)} 项失败：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


sys.exit(asyncio.run(main()))
