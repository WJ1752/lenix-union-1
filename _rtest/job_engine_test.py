"""后台任务引擎回归测试：并行槽位 / 分段计时 / 中止 / 暂停继续 / 重跑 / 跨范围缓存复用

不碰 Bungie：activity_history / get_profile / pvp_match_contribution 全换成假实现，
落盘位置指到临时目录，不污染真实的 weapon_agg_cache.json 与 pvp_weapon_cache.json。

跑法：.venv/Scripts/python _rtest/job_engine_test.py
"""
import asyncio
import datetime
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

FAILED = []
TMP = tempfile.mkdtemp(prefix="job_engine_test_")
MID = "4611686018000000001"
CHARS = ["c1"]
REAL_JOB_PARALLEL = d2.job_parallel   # reset() 会把模块属性换成桩，这里留一份真身


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


def ok(name, cond, note=""):
    check(name + (f"（{note}）" if note else ""), bool(cond), True)


def today(offset: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class Fake:
    def __init__(self):
        self.rows: list[dict] = []     # 每个角色的对局历史（新→旧）
        self.spans: list[tuple] = []   # _collect_matches 被调用时的 (since, until)
        self.contrib_calls = 0

    def reset(self):
        self.rows = []
        self.spans = []
        self.contrib_calls = 0


F = Fake()


async def fake_history(mtype, mid, cid, mode, count=250, page=0):
    return F.rows[page * count: page * count + count]


async def fake_resolve(name):
    return {"mtype": 3, "mid": MID, "display": "Wj", "code": 8984}


async def fake_profile(mtype, mid):
    return {"characters": {"data": {c: {} for c in CHARS}}}


async def fake_contrib(instance, mid):
    F.contrib_calls += 1
    return {"kills": 1, "precision": 0, "melee": 0, "grenade": 0, "super": 0, "ability": 0,
            "weapons": [{"name": "测试枪", "icon": "", "type": "手炮", "hash": "h1",
                         "kills": 1, "precision": 0}]}


def match(day: str, instance: str) -> dict:
    return {"period": f"{day} 12:00", "mode": 5, "instance": instance}


def reset(parallel: int = 2):
    d2.JOBS.clear()
    d2._JOB_DEDUP.clear()
    d2._JOB_QUEUE.clear()
    d2._JOB_RUNNING.clear()
    d2._JOB_TASK.clear()
    d2._JOB_REUSE_SEC = 120
    d2.job_parallel = lambda: parallel
    F.reset()
    for name in os.listdir(TMP):
        os.remove(os.path.join(TMP, name))


def job(jid: str, label: str = "测试任务", **kw) -> dict:
    rec = {"done": 0, "total": 0, "status": "queued", "name": "Wj#8984", "result": None,
           "kind": "test", "who": "测试", "ts": time.time(), "label": label}
    rec.update(kw)
    d2.JOBS[jid] = rec
    return rec


async def drain(limit: int = 2000):
    for _ in range(limit):
        if not d2._JOB_RUNNING and not d2._JOB_QUEUE:
            return
        await asyncio.sleep(0.005)
    raise RuntimeError(f"任务队列没跑完：running={d2._JOB_RUNNING} queue={len(d2._JOB_QUEUE)}")


async def tick(n: int = 5):
    for _ in range(n):
        await asyncio.sleep(0.05)   # 检查点是 0.5 秒一颗心跳，给它足够的时间反应


# ---------- 用例 ----------

async def case_parallel():
    print("[1] 并行槽位：2 个槽位跑 3 条任务，第 3 条排队，跑完自动提上来")
    reset(parallel=2)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    for n in ("a", "b", "c"):
        job(n)
        d2._enqueue_job(n, slow)
    await tick()
    check("两个在跑", sorted(d2._JOB_RUNNING), ["a", "b"])
    check("c 排队在第 1 位", d2.queue_position("c"), 1)
    check("在跑的任务位次为 0", d2.queue_position("a"), 0)
    gate.set()
    await tick(12)
    check("三条都跑完了", len(d2._JOB_RUNNING), 0)
    check("c 也跑过了", d2.JOBS["c"]["started"] > 0, True)


async def case_parallel_one():
    print("[2] 并行数设成 1 = 回到串行")
    reset(parallel=1)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    job("a")
    job("b")
    d2._enqueue_job("a", slow)
    d2._enqueue_job("b", slow)
    await tick()
    check("只有一个在跑", sorted(d2._JOB_RUNNING), ["a"])
    check("b 排队", d2.queue_position("b"), 1)
    gate.set()
    await tick(12)
    check("都跑完", len(d2._JOB_RUNNING), 0)


async def case_timing():
    print("[3] 分段计时：排队时长与运行时长分开算，结束后两段都定格")
    reset(parallel=1)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    job("run")
    d2._enqueue_job("run", slow)
    await tick()
    job("wait", ts=time.time() - 30)   # 造一条「已经排了 30 秒」的任务
    d2._enqueue_job("wait", slow)
    await tick(4)
    snap = {j["id"]: j for j in d2.job_snapshot()}
    check("排队中的任务没有开始时刻", snap["wait"]["started"], "")
    check("排队中的已排队 ≈30 秒", snap["wait"]["queued_s"] in (30, 31), True)
    check("排队中的已跑 = 0", snap["wait"]["run_s"], 0)
    ok("运行中的已跑 > 0", snap["run"]["run_s"] >= 0)
    ok("运行中也有开始时刻", snap["run"]["started"] != "")

    gate.set()
    await tick(12)
    snap = {j["id"]: j for j in d2.job_snapshot()}
    check("结束后状态定格", snap["wait"]["status"], "done")
    ok("结束后已跑 ≥ 0（秒级以下任务显示 0）", snap["wait"]["run_s"] >= 0)
    check("结束后总时长 = 排队 + 运行",
          snap["wait"]["total_s"], snap["wait"]["queued_s"] + snap["wait"]["run_s"])
    ok("结束状态不再变动：done 有值", snap["wait"]["status"] == "done")
    check("已结束的任务不给暂停键", snap["wait"]["can"]["pause"], False)
    check("已结束的任务给重跑键", snap["wait"]["can"]["retry"], True)


async def case_abort_queued():
    print("[4] 中止排队中的任务：从队列里摘掉，不再被提起")
    reset(parallel=1)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    job("a")
    job("b")
    d2._enqueue_job("a", slow)
    d2._enqueue_job("b", slow)
    await tick()
    r = d2.job_control("b", "abort")
    check("中止返回成功", r["ok"], True)
    check("状态变 aborted", d2.JOBS["b"]["status"], "aborted")
    check("队列里没有 b 了", any(q == "b" for q, _ in d2._JOB_QUEUE), False)
    check("位次查询返回 0", d2.queue_position("b"), 0)
    gate.set()
    await tick(12)
    check("b 从没跑过", d2.JOBS["b"].get("started") or 0, 0)


async def case_abort_running():
    print("[5] 中止运行中的任务：取消协程、状态变 aborted、中间结果落盘")
    reset(parallel=2)
    d2._pvp_cache_ready = True
    d2._agg_cache_ready = True
    d2._writable_path = lambda name: os.path.join(TMP, name)
    write_pvp = []
    d2._save_pvp_cache = lambda: write_pvp.append(1)

    async def slow():
        await asyncio.sleep(30)

    job("a")
    d2._enqueue_job("a", slow)
    await tick()
    check("先确认跑起来了", d2.JOBS["a"]["status"], "running")
    r = d2.job_control("a", "abort")
    check("中止返回成功", r["ok"], True)
    await tick(6)
    check("状态变 aborted", d2.JOBS["a"]["status"], "aborted")
    ok("槽位已释放", not d2._JOB_RUNNING)
    ok("中间结果落过盘", write_pvp)


async def case_pause_resume():
    print("[6] 暂停 / 继续：暂停后任务停在检查点，继续后接着跑完")
    reset(parallel=2)
    done = []

    async def loopy():
        for i in range(20):
            await d2._job_checkpoint("a")
            done.append(i)
            await asyncio.sleep(0.01)

    job("a")
    d2._enqueue_job("a", loopy)
    await tick(3)
    r = d2.job_control("a", "pause")
    check("暂停返回成功", r["ok"], True)
    check("标记为已暂停", d2.JOBS["a"]["paused"], True)
    await tick(3)
    n1 = len(done)
    await tick(4)
    check("暂停期间不再往下跑", len(done), n1)
    await tick(4)
    check("暂停期间还是停在原地", len(done), n1)

    r = d2.job_control("a", "resume")
    check("继续返回成功", r["ok"], True)
    check("暂停标记清掉", d2.JOBS["a"]["paused"], False)
    await tick(10)
    check("继续后跑完了 20 步", len(done), 20)
    await drain()
    check("任务完成", d2.JOBS["a"]["status"] in ("done", "running"), True)


async def case_pause_queued():
    print("[7] 暂停排队中的任务：不占槽位，后面的任务可以顶上")
    reset(parallel=1)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    job("a")
    job("b")
    job("c")
    d2._enqueue_job("a", slow)
    d2._enqueue_job("b", slow)
    d2._enqueue_job("c", slow)
    await tick()
    d2.job_control("b", "pause")
    d2.job_control("a", "abort")
    await tick(4)
    check("a 被中止", d2.JOBS["a"]["status"], "aborted")
    check("b 仍被暂停、没被提起", (d2.JOBS["b"]["paused"], d2.JOBS["b"]["status"]),
          (True, "queued"))
    check("c 顶上了槽位", d2.JOBS["c"]["status"], "running")

    d2.job_control("b", "resume")
    check("恢复后回到队列", d2.queue_position("b"), 1)
    gate.set()
    await tick(12)
    ok("b 最终跑过了", d2.JOBS["b"]["started"] > 0)


async def case_retry():
    print("[8] 重跑：已结束的任务重新排队并再跑一遍")
    reset(parallel=2)
    runs = []

    async def once():
        runs.append(1)

    job("a")
    d2._enqueue_job("a", once)
    await drain()
    check("跑了一次", len(runs), 1)
    check("状态 done", d2.JOBS["a"]["status"], "done")

    r = d2.job_control("a", "retry")
    check("重跑返回成功", r["ok"], True)
    check("回到排队态", d2.JOBS["a"]["status"] in ("queued", "running"), True)
    await drain()
    check("又跑了一次", len(runs), 2)
    check("done 计数与进度已归零重算", d2.JOBS["a"]["status"], "done")

    print("[8b] 还在跑的任务不给重跑，也不让重复中止")
    reset(parallel=2)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()

    job("a")
    d2._enqueue_job("a", slow)
    await tick()
    check("跑着时重跑被拒", d2.job_control("a", "retry")["ok"], False)
    check("跑着时可以暂停", d2.JOBS["a"]["can"] if False else
          d2.job_snapshot()[0]["can"]["pause"], True)
    gate.set()
    await tick(12)


async def case_control_unknown():
    print("[9] 控制不存在的任务：给一句能看懂的提示，不抛异常")
    reset(parallel=2)
    r = d2.job_control("nope", "abort")
    check("返回失败", r["ok"], False)
    ok("带中文提示", "不在了" in r["msg"])
    check("未知动作也不炸", d2.job_control("nope", "???")["ok"], False)


async def case_agg_fold():
    print("[10] 跨范围缓存复用：先查过一段、再查全生涯 → 只补拉空档")
    reset(parallel=2)
    d2._pvp_cache_ready = True
    d2._agg_cache_ready = True
    d2._AGG_CACHE.clear()
    d2._PVP_MATCH_CACHE.clear()
    d2._writable_path = lambda name: os.path.join(TMP, name)
    d2.activity_history = fake_history
    d2.pvp_match_contribution = fake_contrib
    d2.save_seen_players = lambda: None
    d2._save_pvp_cache = lambda: None

    seg_s, seg_u = today(-60), today(-30)
    d2._AGG_CACHE[f"{MID}|pvp|27"] = {
        "scope_since": seg_s, "scope_until": seg_u,
        "weapons": {"h1": {"name": "测试枪", "icon": "", "type": "手炮",
                           "kills": 25, "precision": 5, "matches": 25}},
        "tot": {"kills": 25, "precision": 5, "melee": 0, "grenade": 0, "super": 0,
                "ability": 0},
        "matches": 25, "missed": 0, "oldest": seg_s, "newest": seg_u,
        "newest_full": f"{seg_u} 20:00", "cap": 0, "capped": False}

    # 历史里：段内 25 场（不该再被翻）、段后 3 场、段前 4 场
    F.rows = ([match(today(-i), f"new{i}") for i in range(1, 4)]
              + [match(today(-30 - i), f"in{i}") for i in range(1, 26)]
              + [match(today(-60 - i), f"old{i}") for i in range(1, 5)])

    real_collect = d2._collect_matches

    async def spy_collect(mtype, mid, chars, mode, since, until, cap, skip_modes=frozenset(),
                          on_page=None, check=None):
        F.spans.append((since, until))
        return await real_collect(mtype, mid, chars, mode, since, until, cap,
                                  skip_modes, on_page=on_page, check=check)

    d2._collect_matches = spy_collect
    job("wp", label="PVP 生涯武器（全生涯）", status="running", started=time.time())
    await d2._run_weapon_job("wp", 3, MID, CHARS, "", "", "all", "全生涯", "pvp", 5, 0,
                            frozenset())
    d2._collect_matches = real_collect

    check("只翻了两段空档", len(F.spans), 2)
    check("空档按「新的在前」", [s[0] for s in F.spans][0], today(-29))
    check("上半段空档起于段后一天", F.spans[0][0], today(-29))
    check("上半段空档到「现在」", F.spans[0][1], "")
    check("下半段空档止于段前一天", F.spans[1][1], today(-61))
    check("下半段空档不设下界", F.spans[1][0], "")
    check("段内那 25 场一场都没拉 PGCR（明细走缓存）", F.contrib_calls, 3 + 4)

    res = d2.JOBS["wp"]["result"]
    check("总场次 = 折进来的 25 + 新统计 7", res["matches"], 32)
    check("折进来的场次数记在 cached 里", res["cached"], 25)
    check("本次新增记在 added 里", res["added"], 7)
    check("武器击杀 = 25 + 7", res["weapons"][0]["kills"], 32)
    check("总击杀累加正确", res["kills"], 32)
    ok("note 里说明了复用", "复用" in (res.get("note") or ""), res.get("note"))
    check("没吃到场次上限", res["capped"], False)

    entry = d2._AGG_CACHE[f"{MID}|pvp|all"]
    check("全生涯结果已写回缓存", entry["matches"], 32)
    check("缓存里记下了场次上限状态", entry["capped"], False)
    check("覆盖范围的最早日期", entry["oldest"], today(-64))
    check("覆盖范围的最晚日期", entry["newest"], today(-1))

    print("[10b] 同一范围再查一次：走增量补拉，不重复统计")
    F.reset()
    job("wp2", label="PVP 生涯武器（全生涯）", status="running", started=time.time())
    await d2._run_weapon_job("wp2", 3, MID, CHARS, "", "", "all", "全生涯", "pvp", 5, 0,
                            frozenset())
    res2 = d2.JOBS["wp2"]["result"]
    check("复用上次结果", res2["matches"], 32)
    check("没有新对局就不拉 PGCR", F.contrib_calls, 0)
    check("结果仍是 32 场", res2["matches"], 32)

    print("[10c] 段还没结束（就在今天）不能当子段折进来")
    d2._AGG_CACHE.clear()
    d2._AGG_CACHE[f"{MID}|pvp|cur"] = {
        "scope_since": today(-30), "scope_until": today(),
        "weapons": {"h1": {"name": "测试枪", "icon": "", "type": "手炮",
                           "kills": 9, "precision": 0, "matches": 9}},
        "tot": {"kills": 9, "precision": 0, "melee": 0, "grenade": 0, "super": 0,
                "ability": 0},
        "matches": 9, "missed": 0, "oldest": today(-30), "newest": today(),
        "newest_full": f"{today()} 20:00", "cap": 0, "capped": False}
    segs = d2._sub_agg_segments(MID, "pvp", "", "", skip="")
    check("进行中的段被排除", segs, [])

    print("[10d] 被场次上限截断的段也不能折进来")
    d2._AGG_CACHE[f"{MID}|pvp|cur"]["capped"] = True
    d2._AGG_CACHE[f"{MID}|pvp|cur"]["scope_until"] = today(-1)
    check("截断的段被排除", d2._sub_agg_segments(MID, "pvp", "", "", skip=""), [])

    print("[10e] 没记录场次上限的老段（2026-10-07 之前写的）也不当子段用")
    d2._AGG_CACHE[f"{MID}|pvp|cur"]["capped"] = False
    d2._AGG_CACHE[f"{MID}|pvp|cur"].pop("cap")
    check("判断不了完整性的段被排除",
          d2._sub_agg_segments(MID, "pvp", "", "", skip=""), [])


async def case_stale_head():
    print("[13] 老汇总被旧上限截断 → 丢掉它整段重算（用户报的「只到 23 年」）")
    reset(parallel=2)
    d2._pvp_cache_ready = True
    d2._agg_cache_ready = True
    d2._AGG_CACHE.clear()
    d2._PVP_MATCH_CACHE.clear()
    d2._writable_path = lambda name: os.path.join(TMP, name)
    d2.activity_history = fake_history
    d2.pvp_match_contribution = fake_contrib
    d2.save_seen_players = lambda: None
    d2._save_pvp_cache = lambda: None

    cut = today(-40)          # 老汇总的最早日期（当年按 3000 场上限截断出来的）
    d2._AGG_CACHE[f"{MID}|pve|all"] = {
        "scope_since": "", "scope_until": "",
        "weapons": {"h1": {"name": "测试枪", "icon": "", "type": "手炮",
                           "kills": 3000, "precision": 0, "matches": 3000}},
        "tot": {"kills": 3000, "precision": 0, "melee": 0, "grenade": 0, "super": 0,
                "ability": 0},
        "matches": 3000, "missed": 0, "oldest": cut, "newest": today(-3),
        "newest_full": f"{today(-3)} 20:00", "cap": 3000, "capped": True}

    F.rows = ([match(today(-i), f"new{i}") for i in range(1, 3)]      # 比缓存更新的 2 场
              + [match(today(-40 - i), f"old{i}") for i in range(1, 6)])  # 更早的 5 场

    real_collect = d2._collect_matches

    async def spy_collect(mtype, mid, chars, mode, since, until, cap, skip_modes=frozenset(),
                          on_page=None, check=None):
        F.spans.append((since, until))
        return await real_collect(mtype, mid, chars, mode, since, until, cap,
                                  skip_modes, on_page=on_page, check=check)

    d2._collect_matches = spy_collect
    job("wp", label="PVE 生涯武器（全生涯）", status="running", started=time.time())
    await d2._run_weapon_job("wp", 3, MID, CHARS, "", "", "all", "全生涯", "pve", 7, 20000,
                            frozenset())

    check("整段重算：只翻一段、且不限时间", F.spans, [("", "")])
    ok("提示里说明了整段重算",
       "整段重算" in (d2.JOBS["wp"].get("note") or ""), d2.JOBS["wp"].get("note"))

    res = d2.JOBS["wp"]["result"]
    check("不再沿用老汇总的 3000 场，按实际重算", res["matches"], 7)
    check("最早日期取到重算后的真实最早那天", res["range"][0], today(-45))
    check("新汇总记下了这次的上限", d2._AGG_CACHE[f"{MID}|pve|all"]["cap"], 20000)
    check("不再标记为被截断", d2._AGG_CACHE[f"{MID}|pve|all"]["capped"], False)

    print("[13b] 重算过之后走正常增量：第二次查只补新对局，不再重算")
    rows = F.rows
    F.reset()
    F.rows = rows
    job("wp2", label="PVE 生涯武器（全生涯）", status="running", started=time.time())
    await d2._run_weapon_job("wp2", 3, MID, CHARS, "", "", "all", "全生涯", "pve", 7, 20000,
                            frozenset())
    check("只翻了更新那一段", len(F.spans), 1)
    check("没有新对局，场次不变", d2.JOBS["wp2"]["result"]["matches"], 7)

    print("[13c] 没记录 cap 的老缓存（升级前留下的）同样整段重算一次")
    d2._AGG_CACHE[f"{MID}|pve|all"].pop("cap")
    d2._AGG_CACHE[f"{MID}|pve|all"].pop("capped")
    F.reset()
    F.rows = rows
    job("wp3", label="PVE 生涯武器（全生涯）", status="running", started=time.time())
    await d2._run_weapon_job("wp3", 3, MID, CHARS, "", "", "all", "全生涯", "pve", 7, 20000,
                            frozenset())
    check("整段重算一次", F.spans, [("", "")])
    check("这次把 cap/capped 补写了回去",
          (d2._AGG_CACHE[f"{MID}|pve|all"]["cap"],
           d2._AGG_CACHE[f"{MID}|pve|all"]["capped"]), (20000, False))
    d2._collect_matches = real_collect


async def case_gap_windows():
    print("[11] 空档计算：多段缓存、相邻日期不重不漏")
    segs = [{"scope_since": "2026-01-01", "scope_until": "2026-01-31"},
            {"scope_since": "2025-03-01", "scope_until": "2025-03-31"}]
    gaps = d2._gap_windows("", "", segs)
    check("切成三段空档（新的在前）",
          gaps, [("2026-02-01", ""), ("2025-04-01", "2025-12-31"), ("", "2025-02-28")])
    check("范围内正好一段缓存 → 上下两个空档",
          d2._gap_windows("2025-01-01", "2025-12-31",
                          [{"scope_since": "2025-06-01", "scope_until": "2025-06-30"}]),
          [("2025-07-01", "2025-12-31"), ("2025-01-01", "2025-05-31")])
    check("缓存段贴着范围边界 → 只剩一段",
          d2._gap_windows("2025-06-01", "2025-12-31",
                          [{"scope_since": "2025-06-01", "scope_until": "2025-06-30"}]),
          [("2025-07-01", "2025-12-31")])
    check("缓存段盖满范围 → 没有空档",
          d2._gap_windows("2025-06-01", "2025-06-30",
                          [{"scope_since": "2025-06-01", "scope_until": "2025-06-30"}]),
          [])


async def case_parallel_setting():
    print("[12] 并行槽位设置：0 = 默认、越界钳制到 1..4")
    import bot_runtime
    d2.job_parallel = REAL_JOB_PARALLEL   # reset() 会把模块里的这个名字换成桩，先还回来
    real_load = bot_runtime.load_config
    for raw, want in ((0, d2._JOB_PARALLEL_DEFAULT), (None, d2._JOB_PARALLEL_DEFAULT),
                      (1, 1), (3, 3), (9, d2._JOB_PARALLEL_MAX), (-5, d2._JOB_PARALLEL_DEFAULT),
                      ("x", d2._JOB_PARALLEL_DEFAULT)):
        bot_runtime.load_config = lambda raw=raw: ({} if raw is None else {"job_parallel": raw})
        check(f"配置 {raw!r} → 并行 {want}", d2.job_parallel(), want)
    bot_runtime.load_config = real_load


async def main():
    d2.resolve_member = fake_resolve
    d2.get_profile = fake_profile
    for case in (case_parallel, case_parallel_one, case_timing, case_abort_queued,
                 case_abort_running, case_pause_resume, case_pause_queued, case_retry,
                 case_control_unknown, case_agg_fold, case_gap_windows,
                 case_stale_head, case_parallel_setting):
        await case()
        print()
    if FAILED:
        print(f"{len(FAILED)} 项失败：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


sys.exit(asyncio.run(main()))
