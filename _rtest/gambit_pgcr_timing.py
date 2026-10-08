import sys, os, json, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

def _b(v, k):
    return int(((v.get(k) or {}).get("basic", {}) or {}).get("value", 0) or 0)

async def one(inst, mid, sem):
    async with sem:
        try:
            r = await d2.client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{inst}/")
            resp = json.loads(r.content.decode("utf-8-sig"))
            if resp.get("ErrorCode") != 1:
                return None
            for e in resp["Response"].get("entries", []):
                if (e.get("player", {}).get("destinyUserInfo", {}) or {}).get("membershipId") == mid:
                    ev = (e.get("extended") or {}).get("values") or {}
                    return {"motes": _b(ev, "motesDeposited"), "denied": _b(ev, "motesDenied"),
                            "invk": _b(ev, "invasionKills"), "invdead": _b(ev, "invasionDeaths")}
            return None
        except Exception as exc:
            return f"ERR {type(exc).__name__}"

async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cids = list(prof["characters"]["data"])
    seen, matches = set(), []
    for cid in cids:
        for x in await d2.activity_history(mtype, mid, cid, 63, count=250):
            k = x["instance"]
            if k in seen: continue
            seen.add(k); matches.append(x)
    matches.sort(key=lambda x: x["period"], reverse=True)
    win = matches[:100]
    print(f"窗口 {len(win)} 场")
    t = time.time()
    sem = asyncio.Semaphore(16)
    res = await asyncio.gather(*(one(x["instance"], mid, sem) for x in win))
    ok = sum(1 for r in res if isinstance(r, dict))
    errs = [r for r in res if isinstance(r, str)]
    print(f"冷缓存 100 场耗时 {time.time() - t:.1f}s · 成功 {ok} · 错误 {len(errs)} {errs[:3]}")
    tot = {k: sum(r.get(k, 0) for r in res if isinstance(r, dict)) for k in ("motes", "denied", "invk", "invdead")}
    print("窗口汇总:", tot)

asyncio.run(main())
