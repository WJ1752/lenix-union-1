"""探测智谋(63)可用数据：官方生涯键 / 对局历史每场键 / PGCR 扩展键"""
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
    chars = list(prof["characters"]["data"])
    print(f"player {m['display']} chars={len(chars)}")

    print("\n=== 1) 官方角色级 Stats modes=63 allTime 键 ===")
    r = await d2.client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{chars[0]}/Stats/",
        params={"groups": "101,103", "modes": 63})
    resp = r.json()
    print("ErrorCode", resp.get("ErrorCode"), "top keys", list((resp.get("Response") or {}).keys()))
    at = ((resp.get("Response") or {}).get("pvecomp_gambit") or {}).get("allTime") or {}
    for k in sorted(at):
        b = at[k].get("basic", {})
        print(f"  {k:28s} {b.get('value')}   {at[k].get('displayName')}")

    print("\n=== 2) 对局历史 mode=63 第一场 values 键 ===")
    hist = await d2.activity_history(mtype, mid, chars[0], 63, count=5)
    print("rows", len(hist))
    r2 = await d2.client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{chars[0]}/Stats/Activities/",
        params={"mode": 63, "count": 3})
    acts = ((r2.json().get("Response") or {}).get("activities") or [])
    if acts:
        a = acts[0]
        print("activityDetails:", json.dumps(a.get("activityDetails"), ensure_ascii=False))
        print("values keys:")
        for k, v in (a.get("values") or {}).items():
            print(f"  {k:28s} {v.get('basic', {}).get('value')}  {v.get('displayName')}")
        inst = a["activityDetails"]["instanceId"]
        print("\n=== 3) PGCR 扩展键 ===")
        pg = await d2.get_pgcr(inst)
        ents = pg.get("entries") or []
        print("entries", len(ents))
        if ents:
            e = ents[0]
            print("entry values keys:", list((e.get("values") or {}).keys()))
            ext = e.get("extended") or {}
            print("extended keys:", list(ext.keys()))
            print("extended.values:")
            for k, v in (ext.get("values") or {}).items():
                print(f"  {k:28s} {v.get('basic', {}).get('value')}  {v.get('displayName')}")


asyncio.run(main())
