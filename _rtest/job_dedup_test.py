"""后台任务去重的回归测试：同一个「谁 + 查什么」不该重复全量跑

不碰 Bungie：把 resolve_member / get_profile 换成假实现，然后直接驱动
start_heatmap / _start_weapon_job，看返回的 jid 与 JOBS 里实际建了几条任务。

跑法：.venv/Scripts/python _rtest/job_dedup_test.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


MEMBERS = {
    "Wj#8984": {"mtype": 3, "mid": "4611686018000000001", "display": "Wj", "code": 8984},
    "Ann#1234": {"mtype": 3, "mid": "4611686018000000002", "display": "Ann", "code": 1234},
}


async def fake_resolve(name):
    return dict(MEMBERS[name]) if name in MEMBERS else None


async def fake_profile(mtype, mid):
    return {"characters": {"data": {"c1": {}, "c2": {}}}}


def reset():
    """每个用例从干净状态开始，并且让新任务跑完/不跑都行"""
    d2.JOBS.clear()
    d2._JOB_DEDUP.clear()
    d2._JOB_QUEUE.clear()
    d2._JOB_RUNNING.clear()
    d2._JOB_TASK.clear()
    # 这些用例只看「建了几条任务」，工厂函数一律不真跑：真跑会去打 Bungie 接口，
    # 结果回来时用例早翻页了，反而把后面几条的状态搅乱
    d2._enqueue_job = lambda jid, factory: d2.JOBS.setdefault(jid, {}).update(_factory=factory)


def finish(jid, status="done"):
    """把任务直接置成已结束（不真跑工厂函数）"""
    d2.JOBS[jid].update(status=status, ended=time.time())


async def main():
    d2.resolve_member = fake_resolve
    d2.get_profile = fake_profile

    print("[1] 热力图：连发两次同一个人 → 复用同一个 jid，只建一条任务")
    reset()
    a = await d2.start_heatmap("Wj#8984", who="群810807201")
    b = await d2.start_heatmap("Wj#8984", who="私聊 · Pessimist")
    check("两次 jid 相同", a, b)
    check("只建了一条任务", len(d2.JOBS), 1)
    check("记了两笔复用", d2.JOBS[a].get("reused"), 1)
    check("复用人记下来了", d2.JOBS[a].get("reused_by"), ["私聊 · Pessimist"])
    check("job_shared 为真", d2.job_shared(a), True)

    print("[2] 热力图：换一个人 → 另起一条（去重不能误伤）")
    reset()
    a = await d2.start_heatmap("Wj#8984")
    b = await d2.start_heatmap("Ann#1234")
    check("不同玩家不同 jid", a != b, True)
    check("两条任务", len(d2.JOBS), 2)

    print("[3] 生涯武器：同范围不同写法（27 / s27 / 赛季27）算同一个窗口")
    reset()
    a = await d2.start_pvp_weapons("Wj#8984", "27")
    b = await d2.start_pvp_weapons("Wj#8984", "s27")
    c = await d2.start_pvp_weapons("Wj#8984", "赛季27")
    check("三种写法同一个 jid", (a, b, c), (a, a, a))
    check("只建了一条任务", len(d2.JOBS), 1)

    print("[4] 生涯武器：换范围 / 换模式 → 各算各的，不互相复用")
    reset()
    a = await d2.start_pvp_weapons("Wj#8984", "all")
    b = await d2.start_pvp_weapons("Wj#8984", "s27")
    c = await d2.start_pve_weapons("Wj#8984", "s27")
    check("全生涯与 S27 不同 jid", a != b, True)
    check("PVP 与 PVE 同赛季也不同 jid", b != c, True)
    check("三条任务", len(d2.JOBS), 3)

    print("[5] 跑完 120 秒内仍复用；超过窗口才重跑")
    reset()
    a = await d2.start_heatmap("Wj#8984")
    finish(a)
    check("刚跑完 → 复用", await d2.start_heatmap("Wj#8984"), a)
    d2.JOBS[a]["ended"] = time.time() - d2.job_reuse_window() - 1
    b = await d2.start_heatmap("Wj#8984")
    check("过窗口 → 新建", b != a, True)
    check("任务变两条", len(d2.JOBS), 2)

    print("[6] 失败的任务不进复用窗口（不能拿错误结果当缓存发出去）")
    reset()
    a = await d2.start_heatmap("Wj#8984")
    finish(a, status="error")
    b = await d2.start_heatmap("Wj#8984")
    check("error → 重新跑", b != a, True)

    print("[7] 任务被 _prune_jobs 清掉后，去重映射跟着清（不指向空 jid）")
    reset()
    a = await d2.start_heatmap("Wj#8984")
    check("映射已登记", d2._JOB_DEDUP.get("3:4611686018000000001:heat"), a)
    d2.JOBS.pop(a)
    d2._prune_jobs()
    check("失效映射被清掉", d2._JOB_DEDUP, {})

    print("[8] 复用后返回的 jid 仍能正常出结果（调用方只是照旧轮询 JOBS）")
    reset()
    a = await d2.start_heatmap("Wj#8984")
    # 不给 ended：盖住「工厂已把 status 置成 done、_run_queued 收尾还没跑到」那一瞬
    d2.JOBS[a].update(status="done", result={"display": "Wj#8984", "days": {}})
    b = await d2.start_heatmap("Wj#8984")
    check("done 但还没打 ended 也复用", b, a)
    check("结果还在", d2.JOBS[b]["result"]["display"], "Wj#8984")

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败：")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


sys.exit(asyncio.run(main()))
