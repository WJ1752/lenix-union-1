"""探测 3：智谋 PGCR 原始条目里有没有荧光/入侵等每场字段"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2


async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    rows = await d2.activity_history(mtype, mid, cids[0], 63, count=1)
    inst = rows[0]["instance"]
    r = await d2.client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{inst}/")
    resp = json.loads(r.content.decode("utf-8-sig"))
    d = resp["Response"]
    print("instance", inst, "keys", list(d.keys()))
    print("activityDetails", json.dumps(d.get("activityDetails"), ensure_ascii=False))
    for i, e in enumerate(d.get("entries") or []):
        p = e.get("player") or {}
        ui = p.get("destinyUserInfo") or {}
        nm = ui.get("bungieGlobalDisplayName") or ui.get("displayName")
        v = e.get("values") or {}
        ext = (e.get("extended") or {})
        print(f"\n [{i}] {nm} top keys={sorted(e.keys())}")
        print(f"     values: " + ", ".join(f"{k}={v[k].get('basic',{}).get('value')}" for k in sorted(v)))
        ev = ext.get("values") or {}
        print(f"     extended.values: " + ", ".join(f"{k}={ev[k].get('basic',{}).get('value')}" for k in sorted(ev)))
        print(f"     extended keys: {sorted(ext.keys())}")


asyncio.run(main())
