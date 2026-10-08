"""验证：智谋对局历史里的 score 是否等于 PGCR 的 motesDeposited（含角色生涯口径）"""
import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

def _b(v, k):
    return int(((v.get(k) or {}).get("basic", {}) or {}).get("value", 0) or 0)

async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    seen, matches = set(), []
    for cid in cids:
        for x in await d2.activity_history(mtype, mid, cid, 63, count=250):
            if x["instance"] not in seen:
                seen.add(x["instance"]); matches.append(x)
    matches.sort(key=lambda x: x["period"], reverse=True)
    print(f"共 {len(matches)} 场（跨角色去重）")
    bad = 0; checked = 0; sums = {"score": 0, "motes": 0, "score_done": 0}
    sem = asyncio.Semaphore(16)
    async def one(x):
        async with sem:
            try:
                r = await d2.client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{x['instance']}/")
                resp = json.loads(r.content.decode("utf-8-sig"))
                if resp.get("ErrorCode") != 1:
                    return None
                for e in resp["Response"].get("entries", []):
                    if (e.get("player", {}).get("destinyUserInfo", {}) or {}).get("membershipId") == mid:
                        return (_b((e.get("extended") or {}).get("values") or {}, "motesDeposited"),
                                _b(e.get("values") or {}, "score"))
            except Exception:
                return None
    res = await asyncio.gather(*(one(x) for x in matches[:120]))
    for x, r in zip(matches[:120], res):
        if r is None:
            continue
        checked += 1
        mo, sc = r
        sums["motes"] += mo; sums["score"] += x["score"]
        if x["completed"]:
            sums["score_done"] += x["score"]
        if mo != x["score"]:
            bad += 1
            if bad <= 8:
                print(f"  不等: {x['period']} {x['name'][:28]} 历史 score={x['score']} PGCR motes={mo} "
                      f"PGCR score={sc} completed={x['completed']}")
    print(f"核对 {checked} 场：不等 {bad} 场")
    print(f"总和：历史 score 全部={sums['score']} / 仅完成={sums['score_done']} / PGCR motes={sums['motes']}")

asyncio.run(main())
