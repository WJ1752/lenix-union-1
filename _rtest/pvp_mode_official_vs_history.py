"""只对 PvP 模式：官方分模式统计 vs 对局历史场次（角色 1）

跑法：python _rtest/pvp_mode_official_vs_history.py [玩家名] [角色序号1-3]
"""
import asyncio
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
IDX = int(sys.argv[2]) - 1 if len(sys.argv) > 2 else 0
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
g = lambda d, k: (d.get(k) or {}).get("basic", {}).get("value", 0)  # noqa: E731


async def batches(mtype, mid, cid, modes):
    out: dict = {}
    for i in range(0, len(modes), 15):
        for attempt in (1, 2):
            r = await d2.client().get(
                f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
                params={"groups": "101", "modes": ",".join(str(x) for x in modes[i:i + 15])})
            resp = r.json()
            if resp.get("ErrorCode") == 1:
                for k, v in (resp.get("Response") or {}).items():
                    out.setdefault(k, v)
                break
            print(f"  批次 {i//15+1} 第{attempt}次失败: {resp.get('ErrorCode')} {resp.get('Message')}")
            await asyncio.sleep(1.5)
    return out


async def main() -> None:
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cid = list(prof["characters"]["data"])[IDX]
    pvp = sorted(k for k, v in d2.MODES.items() if v.get("cat") == 2)
    st = await batches(mtype, mid, cid, pvp)
    rot = {norm(v["key"]): int(k) for k, v in d2.MODES.items() if v.get("key")}
    rows = []
    for pg in range(0, 4):
        rows += await d2.activity_history(mtype, mid, cid, 5, count=250, page=pg)
    hist = Counter(x["mode"] for x in rows if x["mode"])
    off: dict[int, float] = {}
    for key, v in st.items():
        mt = rot.get(norm(key))
        if mt is not None and d2.MODES[mt]["cat"] == 2:
            off[mt] = g(v.get("allTime") or {}, "activitiesEntered")
    print(f"\n角色{IDX+1}：历史 {len(rows)} 场 / 官方 allPvP {off.get(5, 0):.0f} 场\n")
    print(f"{'模式':18} {'官方':>6} {'历史':>6}")
    for mt in sorted(set(off) | set(hist), key=lambda x: -(hist.get(x, 0) + off.get(x, 0))):
        o, h = off.get(mt, 0), hist.get(mt, 0)
        if o or h:
            flag = "  <<<" if abs(o - h) >= 3 else ""
            print(f"{d2.MODES.get(mt, {}).get('name', mt):18} {o:6.0f} {h:6}{flag}")
    print(f"\n官方 allPvP 场次 {off.get(5,0):.0f}；把官方叶子模式非试炼求和 ≈ "
          f"{sum(v for k, v in off.items() if not d2.MODES[k]['agg'] and k != 84):.0f}")


asyncio.run(main())
