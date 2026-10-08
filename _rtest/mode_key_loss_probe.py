"""按 key 消费历史统计的地方丢了多少：官方返回的模式键 vs 索引里的 key

跑法：python _rtest/mode_key_loss_probe.py [玩家名]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"


async def main() -> None:
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cid = list(prof["characters"]["data"])[0]
    st = await d2._char_stats_full(mtype, mid, cid)
    keep = d2._mode_hours(st)
    print(f"角色1：接口返回 {len(st)} 个模式键；_mode_hours 认出来 {len(keep)} 个\n")

    g = lambda d, k: (d.get(k) or {}).get("basic", {}).get("value", 0)
    rot = d2._mode_key_map()
    lost = {}
    for key, v in st.items():
        at = v.get("allTime") or {}
        h = g(at, "secondsPlayed") / 3600
        n = g(at, "activitiesEntered")
        if key in rot:
            continue
        if h >= 0.1 or n:
            lost[key] = (h, n, g(at, "kills"), g(at, "activitiesWon"))
    tot_h = sum(h for h, _, _, _ in lost.values())
    print(f"被丢掉的模式键 {len(lost)} 个，合计 {tot_h:,.1f} 小时：")
    for k, (h, n, kills, won) in sorted(lost.items(), key=lambda x: -x[1][0]):
        print(f"  {k:22} {h:7.1f}h  场次 {n:5.0f}  击杀 {kills:6.0f}  胜 {won:4.0f}")
    print(f"\n认出来的模式（前 15，卡片按这对排序）：")
    for mt, h in sorted(keep.items(), key=lambda x: -x[1])[:15]:
        print(f"  {d2.MODES.get(mt, {}).get('name', mt):16} {h:7.1f}h")


asyncio.run(main())
