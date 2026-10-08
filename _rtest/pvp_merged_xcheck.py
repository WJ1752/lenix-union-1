"""交叉验证：Bungie 官方账号级 mergedAllCharacters 合并统计 vs bot 手工按角色求和

跑法：python _rtest/pvp_merged_xcheck.py [玩家名]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
KEYS = ("kills", "deaths", "assists", "activitiesEntered", "activitiesWon",
        "precisionKills", "secondsPlayed", "suicides", "score", "opponentsDefeated")


def g(d, k):
    return (d.get(k) or {}).get("basic", {}).get("value")


async def main() -> None:
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Account/{mid}/Stats/",
                              params={"groups": "101"})
    resp = r.json()
    print("ErrorCode:", resp.get("ErrorCode"), resp.get("Message"))
    R = resp.get("Response") or {}
    mac = ((R.get("mergedAllCharacters") or {}).get("results") or {}).get("allPvP", {})
    merged = (mac.get("allTime") or {})
    print("\n官方账号级 merged.allPvP.allTime:")
    for k in KEYS:
        print(f"  {k:22}={g(merged, k)}")
    print("  （字段总数）", len(merged))

    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    lst = [await d2.char_stats(mtype, mid, cid, "101,103,104") for cid in cids]
    bot = d2._sum(lst, "allPvP")
    print("\nbot 手工按角色求和 vs 官方 merged：")
    for k in KEYS:
        b = bot.get(k)
        o = g(merged, k)
        if b is None:
            print(f"  {k:22} bot=—            官方={o}")
        else:
            flag = "OK " if abs(float(b) - float(o or 0)) < 0.01 else "!! 不一致"
            print(f"  {k:22} bot={b:<12.1f} 官方={o:<12} {flag}")

    # 官方 merged 里 allPvP 之外还有哪些模式（看生涯键覆盖）
    res = (R.get("mergedAllCharacters") or {}).get("results") or {}
    print("\n官方账号级合并里的模式键:", sorted(res))
    print("单角色统计里的模式键:", sorted((R.get(f"characters", {}) or {}).get("data", {}).keys())[:1])
    ch = ((R.get("characters") or {}).get("results") or {})
    if ch:
        first = next(iter(ch.values()))
        mac2 = ((first.get("results") or {}).get("allPvP") or {})
        print("单角色(官方) allPvP 键数:", len(((mac2.get("allTime") or {}))))


asyncio.run(main())
