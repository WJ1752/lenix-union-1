"""热力图结果缓存 / 闸门 / 增量补拉的回归测试

不碰 Bungie：resolve_member / get_profile / activity_history 全换成假实现，
并且把落盘位置指到临时目录，不污染真实的 heatmap_cache.json。

跑法：.venv/Scripts/python _rtest/heat_cache_test.py
"""
import asyncio
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

FAILED = []
TMP = tempfile.mkdtemp(prefix="heat_cache_test_")

NEWEST = "2026-09-25 21:00"
PER_CHAR = 600          # 每角色 600 场 → 一次全量要翻 4 页


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


def gen(n, newest=NEWEST):
    t = datetime.datetime.fromisoformat(newest)
    return [{"period": (t - datetime.timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"),
             "duration": 600, "kills": 10} for i in range(n)]


class Fake:
    """假 Bungie：CALLS 记录翻了几次历史，HIST 是每个角色的完整对局（新→旧）"""

    def __init__(self):
        self.calls = []
        self.hist = {}
        self.chars = {"c1": {}, "c2": {}}
        self.last_played = {}


F = Fake()


async def fake_resolve(name):
    return {"mtype": 3, "mid": "4611686018000000001", "display": "Wj", "code": 8984}


async def fake_profile(mtype, mid):
    data = {cid: {"dateLastPlayed": F.last_played.get(cid, "")}
            for cid in F.chars}
    return {"characters": {"data": data}}


async def fake_history(mtype, mid, cid, mode, count=250, page=0):
    F.calls.append(cid)
    rows = F.hist.get(cid) or []
    return rows[page * count: page * count + count]


def reset(chars=None, hist=None, last_played=None):
    F.calls.clear()
    F.chars = chars if chars is not None else {"c1": {}, "c2": {}}
    F.hist = hist if hist is not None else {}
    F.last_played = last_played or {}
    d2.JOBS.clear()
    d2._JOB_DEDUP.clear()
    d2._JOB_QUEUE.clear()
    d2._JOB_RUNNING = None
    # _heat_cache_ready 也置回 False：下一次会重新读文件，等价于「重启一次进程」
    d2._HEAT_CACHE.clear()
    d2._heat_cache_ready = False


async def drain():
    for _ in range(500):
        if d2._JOB_RUNNING is None and not d2._JOB_QUEUE:
            break
        await asyncio.sleep(0)
    else:
        raise RuntimeError("任务队列没跑完")


async def run_heat(name="Wj#8984"):
    jid = await d2.start_heatmap(name, who="测试")
    await drain()
    return jid, d2.JOBS[jid]


async def main():
    d2.resolve_member = fake_resolve
    d2.get_profile = fake_profile
    d2.activity_history = fake_history
    d2._writable_path = lambda name: os.path.join(TMP, name)

    both = gen(PER_CHAR)
    at_newest = {cid: NEWEST for cid in ("c1", "c2")}

    print("[1] 首次全量：翻完所有页、结果落盘、不是缓存")
    reset(hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)}, last_played=at_newest)
    jid, j = await run_heat()
    check("状态 done", j["status"], "done")
    check("cached 为假", j["cached"], False)
    check("两角色各翻 4 页", len(F.calls), 8)
    check("总数 1200 场", j["result"]["matches"], 1200)
    check("newest_full 是最新那场", j["result"]["newest_full"], NEWEST)
    check("落盘了", os.path.exists(os.path.join(TMP, "heatmap_cache.json")), True)
    saved = json.load(open(os.path.join(TMP, "heatmap_cache.json"), encoding="utf-8"))
    check("缓存里是本人", sorted(saved), ["3:4611686018000000001"])
    days_before = dict(j["result"]["days"])

    print("[2] 没有新数据（上号时间不晚于已统计到的那场）→ 直接出缓存，一次历史都不翻")
    reset(hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)}, last_played=at_newest)
    jid2, j2 = await run_heat()
    check("零次翻页", len(F.calls), 0)
    check("直接 done", j2["status"], "done")
    check("cached 为真", j2["cached"], True)
    check("没有新增", j2["result"]["added"], 0)
    check("场次与上次一致", j2["result"]["matches"], 1200)
    check("日历数据一致", j2["result"]["days"], days_before)

    print("[3] 打了 5 把再查 → 只补拉新增那几场，历史不重算也不丢")
    new5 = gen(5, newest="2026-09-26 02:00")
    reset(hist={"c1": new5 + gen(PER_CHAR), "c2": gen(PER_CHAR)},
          last_played={"c1": "2026-09-26 02:00", "c2": NEWEST})
    jid3, j3 = await run_heat()
    check("每角色只翻 1 页（遇旧的就停）", len(F.calls), 2)
    check("新增 5 场", j3["result"]["added"], 5)
    check("总数 1205 场", j3["result"]["matches"], 1205)
    check("cached（读过缓存）", j3["cached"], True)
    check("newest_full 前移到新那场", j3["result"]["newest_full"], "2026-09-26 02:00")
    # 边界那天的旧场次不能因为补拉被重复计入
    boundary = "2026-09-25"
    new_on_boundary = sum(1 for m in new5 if m["period"][:10] == boundary)
    check("边界日只加了新增的那几场",
          j3["result"]["days"][boundary]["matches"] - days_before[boundary]["matches"],
          new_on_boundary)
    check("老日子一天都没丢", all(k in j3["result"]["days"] for k in days_before), True)

    print("[4] 上号了但没打（历史里没有更新的场次）→ 补拉后 added 为 0，数据不变")
    reset(hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)},
          last_played={"c1": "2026-09-26 23:59", "c2": NEWEST})
    jid4, j4 = await run_heat()
    check("每角色 1 页就够", len(F.calls), 2)
    check("added 为 0", j4["result"]["added"], 0)
    check("总数不变", j4["result"]["matches"], 1205)
    check("日历不变", j4["result"]["days"], j3["result"]["days"])

    print("[5] 角色变了 → 缓存不可信，退回全量")
    reset(chars={"c1": {}, "c2": {}, "c3": {}},
          hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)},
          last_played={"c1": NEWEST, "c2": NEWEST, "c3": NEWEST})
    jid5, j5 = await run_heat()
    check("退回全量：c1/c2 各 4 页 + c3 空页 1 次", len(F.calls), 9)
    check("不是缓存", j5["cached"], False)

    print("[6] 有角色拿不到上号时间 → 保守重跑，不吃缓存")
    reset(hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)},
          last_played={"c1": NEWEST})       # c2 缺 dateLastPlayed
    jid6, j6 = await run_heat()
    check("退回全量而非零翻页", len(F.calls), 8)

    print("[7] 缓存文件坏了 / 读不到 → 照旧全量，不炸")
    reset(hist={"c1": gen(PER_CHAR), "c2": gen(PER_CHAR)}, last_played=at_newest)
    open(os.path.join(TMP, "heatmap_cache.json"), "w", encoding="utf-8").write("{坏掉的 json")
    jid7, j7 = await run_heat()
    check("仍然出结果", j7["status"], "done")
    check("总数还是 1200", j7["result"]["matches"], 1200)

    print("[8] 缓存条目上限：超出后丢最久没更新的")
    reset()
    d2._HEAT_CACHE.clear()
    for i in range(d2._HEAT_CACHE_MAX + 10):
        d2._HEAT_CACHE[f"3:player{i}"] = {"days": {"2026-01-01": {}}, "chars": ["c1"],
                                          "updated": f"2026-01-{i % 28 + 1:02d} 00:00:00"}
    d2._save_heat_cache()
    check("降到上限以内", len(d2._HEAT_CACHE) <= d2._HEAT_CACHE_MAX, True)

    print()
    shutil.rmtree(TMP, ignore_errors=True)
    if FAILED:
        print(f"{len(FAILED)} 项失败：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


sys.exit(asyncio.run(main()))
