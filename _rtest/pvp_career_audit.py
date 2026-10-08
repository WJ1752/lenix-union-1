"""生涯 allPvP 汇总是否等于「各 PvP 模式求和」——官方聚合有没有漏数据

跑法：python _rtest/pvp_career_audit.py [玩家名]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"


async def stats_modes(mtype, mid, cid, modes):
    r = await d2.client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
        params={"groups": "101", "modes": modes},
    )
    return (r.json().get("Response") or {})


async def main() -> None:
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    print("角色:", len(cids))

    pvp_leaf = [k for k, v in d2.MODES.items() if v.get("cat") == 2 and not v.get("agg")]
    agg = [k for k, v in d2.MODES.items() if v.get("cat") == 2 and v.get("agg")]
    print(f"cat=2 聚合模式 {agg} / 叶子模式 {len(pvp_leaf)} 个: {sorted(pvp_leaf)}")

    # 一次性把所有 PvP 模式都请求回来（modes 支持逗号列表）
    all_modes = ",".join(str(x) for x in sorted(set(pvp_leaf) | set(agg)))
    tot = {"entered": 0, "won": 0, "kills": 0, "deaths": 0, "assists": 0, "secs": 0}
    print("\n=== 单角色：逐模式官方统计 vs allPvP 汇总 ===")
    for i, cid in enumerate(cids, 1):
        st = await stats_modes(mtype, mid, cid, all_modes)
        a = (st.get("allPvP") or {}).get("allTime") or {}
        g = lambda d, k: (d.get(k) or {}).get("basic", {}).get("value", 0)
        print(f"角色{i} allPvP: 场次={g(a,'activitiesEntered'):.0f} 胜={g(a,'activitiesWon'):.0f} "
              f"击杀={g(a,'kills'):.0f} 时长={g(a,'secondsPlayed')/3600:.1f}h "
              f"模式键={len(st)}")
        kmap = d2._mode_key_map()
        s = {"entered": 0, "kills": 0, "secs": 0}
        per, aggkeys = {}, {}
        for mkey, v in sorted(st.items()):
            if mkey == "allPvP":
                continue
            mt = kmap.get(mkey)
            at = (v.get("allTime") or {})
            n = g(at, "activitiesEntered")
            if not n or not mt:
                if n and not mt:
                    aggkeys[mkey] = n
                continue
            name = d2.MODES.get(mt, {}).get("name") or mkey
            is_agg = bool(d2.MODES.get(mt, {}).get("agg"))
            per[f"{name}{'(agg)' if is_agg else ''}"] = (n, g(at, "kills"))
            if is_agg:
                continue
            s["entered"] += n
            s["kills"] += g(at, "kills")
            s["secs"] += g(at, "secondsPlayed")
        print(f"  只按叶子模式求和: 场次={s['entered']:.0f} 击杀={s['kills']:.0f} 时长={s['secs']/3600:.1f}h"
              f"  (allPvP 差: 场次{g(a,'activitiesEntered')-s['entered']:+.0f} 击杀{g(a,'kills')-s['kills']:+.0f})")
        if aggkeys:
            print("  无法归类的键:", aggkeys)
        print("  明细:", {k: int(v[0]) for k, v in sorted(per.items(), key=lambda x: -x[1][0])})

    print("\n=== 多角色合并（bot 的 /pvp 生涯口径）===")
    for cid in cids:
        st = await stats_modes(mtype, mid, cid, all_modes)
        a = (st.get("allPvP") or {}).get("allTime") or {}
        for k, s in (("activitiesEntered", "entered"), ("activitiesWon", "won"), ("kills", "kills"),
                     ("deaths", "deaths"), ("assists", "assists"), ("secondsPlayed", "secs")):
            tot[s] += (a.get(k) or {}).get("basic", {}).get("value", 0)
    print(f"  合计: 场次={tot['entered']:.0f} 胜={tot['won']:.0f} 击杀={tot['kills']:.0f} "
          f"死亡={tot['deaths']:.0f} 协助={tot['assists']:.0f} 时长={tot['secs']/3600:.1f}h")

    print("\n=== 对局历史能翻多深（单角色 mode=5，每页 250）===")
    for pg in (3, 4, 5):
        rows = await d2.activity_history(mtype, mid, cids[0], 5, count=250, page=pg)
        print(f"  page={pg} -> {len(rows):3} 场 {('最早 ' + rows[-1]['period']) if rows else ''}")


asyncio.run(main())
