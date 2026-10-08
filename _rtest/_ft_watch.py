"""后台轮询：把 204/1000 的原始形态按时间戳记下来，抓「进副本瞬间」的数据。

用法: python _rtest/_ft_watch.py <分钟> [玩家名...]
"""
import asyncio, json, sys, time
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2, bot_fireteam as ft

MINS = float(sys.argv[1]) if len(sys.argv) > 1 else 20
NAMES = sys.argv[2:] or ["Required#9992", "十七#0215"]

async def snap(nm, mtype, mid):
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": "200,204,1000"}, no_cache=True)
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        return f"{nm}: ERR {resp.get('ErrorCode')} {resp.get('Message')}"
    prof = resp["Response"]
    ca = ((prof.get("characterActivities") or {}).get("data") or {})
    tr = (prof.get("profileTransitoryData") or {}).get("data") or {}
    ta = (tr.get("currentActivity") or {})
    parts = []
    for cid, d in ca.items():
        h = d.get("currentActivityHash") or 0
        k = await ft._activity_kind(h) if h else "zero"
        lbl = (await ft._activity_label(h)) if h else ""
        parts.append(f"[{cid[-5:]} h={h}:{k}:{lbl!r}@{d.get('dateActivityStarted')}]")
    cur = ft._current(prof)
    lp = int(ta.get("numberOfPlayers") or 0) + int(ta.get("numberOfOpponents") or 0)
    return (f"{nm} | 204 {' '.join(parts)} | 1000 start={ta.get('startTime')} "
            f"players={ta.get('numberOfPlayers')} opp={ta.get('numberOfOpponents')} "
            f"party={len(tr.get('partyMembers') or [])} | _current={cur[1] if cur else None} "
            f"live={lp}")

async def main():
    ids = []
    for n in NAMES:
        m = await d2.resolve_member(n)
        ids.append((n, m["mtype"], m["mid"]) if m else (n, 0, ""))
    end = time.time() + MINS * 60
    while time.time() < end:
        ts = time.strftime("%H:%M:%S")
        for nm, mt, mid in ids:
            if not mid:
                print(f"{ts} {nm}: 解析失败", flush=True); continue
            try:
                print(f"{ts} {await snap(nm, mt, mid)}", flush=True)
            except Exception as e:
                print(f"{ts} {nm}: EXC {type(e).__name__} {e}", flush=True)
        await asyncio.sleep(20)

asyncio.run(main())
