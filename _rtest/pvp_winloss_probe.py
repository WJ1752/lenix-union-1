"""近期战绩窗口里，胜负判定的可信度：哪些模式的 standing 没写 → 被判成负场

跑法：python _rtest/pvp_winloss_probe.py [玩家名]
"""
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"


async def main() -> None:
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])

    # 直接打原始接口，保留 standing 字段
    per_mode = defaultdict(lambda: {"n": 0, "done": 0, "win": 0, "stand": defaultdict(int)})
    tot = {"n": 0, "done": 0, "win": 0, "stand_missing": 0}
    for cid in cids:
        r = await d2.client().get(
            f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/Activities/",
            params={"mode": 5, "count": 100, "page": 0})
        for a in ((r.json().get("Response") or {}).get("activities") or []):
            v = a.get("values", {})
            ad = a.get("activityDetails", {})
            mi = d2._match_mode(ad)
            done = int(v.get("completed", {}).get("basic", {}).get("value", 0)) == 1
            st = v.get("standing", {}).get("basic", {}).get("value")
            key = (mi["mode_name"] or "其他", bool(mi["mode_cat"] in d2.COMPETITIVE_CATS),
                   ad.get("mode"), ad.get("isPrivate") if "isPrivate" in ad else "")
            b = per_mode[key]
            b["n"] += 1
            if done:
                b["done"] += 1
                b["win"] += 1 if (st == 0) else 0
            b["stand"][st] = b["stand"].get(st, 0) + 1
            tot["n"] += 1
            tot["done"] += int(done)
            tot["win"] += 1 if (done and st == 0) else 0
            if done and st is None:
                tot["stand_missing"] += 1

    print(f"窗口总场次 {tot['n']} 完成 {tot['done']} standing==0 {tot['win']} "
          f"完成但无 standing 字段 {tot['stand_missing']}")
    print(f"{'模式':16} {'私':3} {'场次':>4} {'完成':>4} {'胜':>4} {'胜率':>6}  standing 分布")
    for (name, comp, mt, priv), b in sorted(per_mode.items(), key=lambda x: -x[1]["n"]):
        wr = f"{b['win'] / b['done'] * 100:5.1f}%" if b["done"] else "     -"
        sd = dict(sorted(b["stand"].items(), key=lambda x: (x[0] is None, x[0])))
        print(f"{name:16} {str(priv):3} {b['n']:4} {b['done']:4} {b['win']:4} {wr:>6}  "
              f"cat={mt} {sd}")


asyncio.run(main())
