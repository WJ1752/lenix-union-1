"""/pvp 数据完整性探针：把 Bungie 原始返回 与 bot 卡片实际使用的字段/场次做对比

跑法：python _rtest/pvp_completeness_probe.py [玩家名]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"


async def main() -> None:
    m = await d2.resolve_member(NAME)
    print("member:", m)
    if not m:
        return
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    chars = prof.get("characters", {}).get("data", {})
    print("角色数:", len(chars))
    cids = list(chars)

    print("\n=== A. 不同 groups 返回的模式与 allPvP 字段 ===")
    for groups in ("101,103,104", "1,2,3", "1", "101", "102", "103", "104"):
        try:
            st = await d2.char_stats(mtype, mid, cids[0], groups)
        except Exception as e:  # noqa: BLE001
            print(f"groups={groups}: ERROR {e}")
            continue
        top = sorted(st.keys())
        at = (st.get("allPvP") or {}).get("allTime") or {}
        print(f"groups={groups}: 顶层模式键 {len(top)} -> {top[:14]}{'...' if len(top) > 14 else ''}")
        if at:
            print(f"   allPvP.allTime 字段 {len(at)}: {sorted(at)}")

    print("\n=== B. bot 实际保留的 9 个字段 vs 原始字段 ===")
    st = await d2.char_stats(mtype, mid, cids[0], "101,103,104")
    at = (st.get("allPvP") or {}).get("allTime") or {}
    kept = ("kills", "deaths", "assists", "activitiesEntered", "activitiesWon",
            "killsDeathsRatio", "killsDeathsAssists", "precisionKills", "winRate")
    for k in kept:
        v = at.get(k, {}).get("basic", {}).get("value")
        print(f"  {k:22} {'=' if k in at else 'MISSING':7} {v}")
    print("  原始里有、但 bot 没用上的字段:")
    print("   ", sorted(set(at) - set(kept)))

    print("\n=== C. 生涯(多角色求和) vs 单角色原始 ===")
    lst = [await d2.char_stats(mtype, mid, cid, "101,103,104") for cid in cids]
    tot = d2._sum(lst, "allPvP")
    print("  bot 生涯合计:", {k: round(v, 2) for k, v in tot.items()})
    raw_sum = {}
    for s in lst:
        a = (s.get("allPvP") or {}).get("allTime") or {}
        for k in ("kills", "deaths", "assists", "activitiesEntered", "activitiesWon",
                  "precisionKills", "secondsPlayed", "suicides", "score", "efficiency"):
            if k in a:
                raw_sum[k] = raw_sum.get(k, 0) + a[k]["basic"]["value"]
    print("  原始多角色求和:", {k: round(v, 2) for k, v in raw_sum.items()})

    print("\n=== D. 近期战绩窗口：ActivityHistory 一次能给多少 ===")
    for cnt in (100, 250):
        rows = await d2.activity_history(mtype, mid, cids[0], 5, count=cnt, page=0)
        print(f"  count={cnt} -> 返回 {len(rows)} 场；最早 {rows[-1]['period'] if rows else '-'} / 最新 {rows[0]['period'] if rows else '-'}")
    # 翻到深页看能翻多远（每页 250）
    for pg in (1, 2, 5, 10, 20, 40, 59, 60):
        rows = await d2.activity_history(mtype, mid, cids[0], 5, count=250, page=pg)
        print(f"  page={pg:3} -> {len(rows):3} 场 {('最早 ' + rows[-1]['period']) if rows else ''}")

    print("\n=== E. bot 的 mode_report(5) 全量 ===")
    rep = await d2.mode_report(NAME, 5)
    print(f"  近期窗口: total={rep['total']} completed={rep['completed']} wins={rep['wins']} "
          f"losses={rep['losses']} 胜率={rep['win_rate']:.1f}% 场均击杀={rep['avg_kills']:.1f}")
    print(f"  K/D={rep['kd']:.2f} KDA={rep['kda']:.2f} 效率={rep['eff']:.2f} 时长={rep['hours']:.1f}h")
    print("  模式细分(只覆盖上面这个窗口):")
    for b in rep["breakdown"]:
        print(f"    {b['name']:16} n={b['n']:4} done={b['done']:4} 胜率={b['win_rate']:5.1f}% "
              f"KD={b['kd']:.2f} 场均={b['avg_kills']:.1f}")

    print("\n=== F. 生涯胜率/场次 与 卡片口径 ===")
    card_win = tot["activitiesWon"] / tot["activitiesEntered"] * 100 if tot["activitiesEntered"] else 0
    print(f"  卡片「生涯统计」：场次 {tot['activitiesEntered']:.0f} 胜 {tot['activitiesWon']:.0f} 胜率 {card_win:.1f}%")
    print(f"  卡片「近期战绩」：场次 {rep['total']} 胜率 {rep['win_rate']:.1f}%（窗口内）")


asyncio.run(main())
