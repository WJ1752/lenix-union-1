import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/", params={"components": "900"})
    pr = ((r.json().get("Response") or {}).get("profileRecords") or {}).get("data") or {}
    recs = pr.get("records") or {}
    for h, lab in (("340857458", "宗师征服"), ("914587616", "终极征服"), ("4018593209", "伟大征服者(镀金)")):
        st = recs.get(h) or {}
        print(lab, "state=", st.get("state"), "objs=", [(o.get("progress"), o.get("completionValue")) for o in (st.get("objectives") or [])],
              "completedCount=", st.get("completedCount"))
    # 征服系列对局（全模式 0 扫一遍看名字）
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    names = {}
    for cid in cids:
        page = 0
        while True:
            rows = await d2.activity_history(mtype, mid, cid, 0, count=250, page=page)
            for x in rows:
                if "征服" in x["name"]:
                    k = x["name"]
                    names.setdefault(k, []).append(x["completed"])
            if len(rows) < 250 or page > 30:
                break
            page += 1
    print("\n含'征服'的对局（mode=0）:")
    for k, v in sorted(names.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):4d} 场  通关 {sum(v):3d}  {k}")

asyncio.run(main())
