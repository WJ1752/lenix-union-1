import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])

    # 1) 900 组件字段
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/", params={"components": "900"})
    pr = ((r.json().get("Response") or {}).get("profileRecords") or {}).get("data") or {}
    print("profileRecords 标量字段:", {k: v for k, v in pr.items() if not isinstance(v, (dict, list))})

    # 2) mode=7 里有没有 征服；mode=18 拿到什么
    for mode in (7, 18):
        names = {}
        for cid in cids:
            page = 0
            while True:
                rows = await d2.activity_history(mtype, mid, cid, mode, count=250, page=page)
                for x in rows:
                    if "征服" in x["name"] or "日落" in x["name"]:
                        names[x["name"]] = names.get(x["name"], 0) + 1
                if len(rows) < 250 or page > 20:
                    break
                page += 1
        print(f"\nmode={mode} 征服/日落 场次:")
        for n, c in sorted(names.items(), key=lambda kv: -kv[1]):
            print(f"   {c:4d}  {n}")

asyncio.run(main())
