"""探测 2：官方智谋生涯键是否完整（与对局历史对数）+ PGCR 真玩家条目的扩展键"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

KEYS = ("activitiesEntered", "activitiesWon", "kills", "deaths", "assists",
        "motesDeposited", "motesDenied", "motesLost", "motesPickedUp",
        "invasionKills", "invasionDeaths", "invasions", "invaderKills", "invaderDeaths",
        "primevalKills", "primevalDamage", "highValueKills", "blockerKills",
        "motesDeposited", "roundsPlayed", "roundsWon", "secondsPlayed",
        "smallBlockersSent", "mediumBlockersSent", "largeBlockersSent", "bankOverage")


async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    chars = list(prof["characters"]["data"])

    print("=== 官方角色级智谋统计（三角色） ===")
    totals = {k: 0.0 for k in KEYS}
    for ci, cid in enumerate(chars, 1):
        r = await d2.client().get(
            f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
            params={"groups": "101,103", "modes": 63})
        at = ((r.json().get("Response") or {}).get("pvecomp_gambit") or {}).get("allTime") or {}
        vals = {k: float((at.get(k) or {}).get("basic", {}).get("value", 0) or 0) for k in KEYS}
        print(f" 角色{ci}: " + " ".join(f"{k}={vals[k]:.0f}" for k in
              ("activitiesEntered", "activitiesWon", "motesDeposited", "motesDenied",
               "invasionKills", "invaderKills", "primevalKills")) )
        for k in KEYS:
            totals[k] += vals[k]
    print(" 合计: " + " ".join(f"{k}={totals[k]:.0f}" for k in
          ("activitiesEntered", "activitiesWon", "motesDeposited", "motesDenied",
           "motesPickedUp", "motesLost", "invasionKills", "invasionDeaths", "invasions",
           "invaderKills", "invaderDeaths", "primevalKills", "highValueKills",
           "blockerKills", "roundsPlayed", "roundsWon", "smallBlockersSent",
           "mediumBlockersSent", "largeBlockersSent", "bankOverage", "secondsPlayed")))

    print("\n=== 对局历史 mode=63 翻满：真实场次 ===")
    per = {}
    for ci, cid in enumerate(chars, 1):
        n, page = 0, 0
        while True:
            rows = await d2.activity_history(mtype, mid, cid, 63, count=250, page=page)
            n += len(rows)
            if len(rows) < 250:
                break
            page += 1
            if page > 8:
                break
        per[cid] = n
        print(f" 角色{ci}: 历史 {n} 场")
    seen = set()
    uniq = 0
    for cid in chars:
        for page in range(0, 9):
            rows = await d2.activity_history(mtype, mid, cid, 63, count=250, page=page)
            for m2 in rows:
                key = m2["instance"] or f"{m2['ref']}{m2['period']}"
                if key not in seen:
                    seen.add(key)
                    uniq += 1
            if len(rows) < 250:
                break
    print(f" 去重合计 {uniq} 场；官方合计 {totals['activitiesEntered']:.0f} 场")

    print("\n=== PGCR 各条目 ===")
    rows = await d2.activity_history(mtype, mid, chars[0], 63, count=1)
    inst = rows[0]["instance"]
    pg = await d2.get_pgcr(inst)
    for i, e in enumerate(pg.get("entries") or []):
        p = e.get("player") or {}
        nm = ((p.get("destinyUserInfo") or {}).get("bungieGlobalDisplayName") or "?")
        v = (e.get("values") or {})
        ext = (e.get("extended") or {}).get("values") or {}
        print(f" [{i}] {nm} standing={v.get('standing',{}).get('basic',{}).get('value')} "
              f"values={sorted(v.keys())}")
        print(f"      ext={sorted(ext.keys())}")
        if ext:
            print("      ext vals: " + ", ".join(f"{k}={ext[k].get('basic',{}).get('value')}"
                                                 for k in sorted(ext)))


asyncio.run(main())
