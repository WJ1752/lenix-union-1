"""探针：PVE 生涯统计里「详情未取到」的对局，现在还能不能从 Bungie 取到明细？

背景：weapon_agg_cache 里 Seren1ty(4611686018494243351) 的 pve|all 记录
matches=9136 / missed=8762，也就是 96% 的对局没有明细。本探针直连 Bungie
（不走 destiny_data 的缓存与维护闸门），抽样拉这些对局的 PGCR，看返回什么：
  · ErrorCode 1 且有本人条目  → 明细还在，当年的 missed 是当时环境（限流/抖动）造成的，可补回
  · 1653 / 其它错误码        → Bungie 真的不给了，属于不可恢复
用法：python _rtest/pve_missed_detail_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os

import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MID = "4611686018494243351"
MTYPE = 3
MODE_PVE = 7
PAGES = [0, 1, 2, 6, 12, 20, 28, 36]   # 覆盖新→旧，够抽样就行


def api_key() -> str:
    key = os.getenv("BUNGIE_API_KEY", "")
    if key:
        return key
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        if line.startswith("BUNGIE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no BUNGIE_API_KEY")


async def main() -> None:
    async with httpx.AsyncClient(base_url="https://www.bungie.net",
                                 headers={"X-API-Key": api_key()}, timeout=20,
                                 follow_redirects=True) as c:
        r = await c.get(f"/Platform/Destiny2/{MTYPE}/Profile/{MID}/",
                        params={"components": "200"})
        prof = r.json()
        chars = list(((prof.get("Response") or {}).get("characters") or {}).get("data") or {})
        print(f"角色数 {len(chars)}：{chars}")

        acts: list[dict] = []
        for ci, cid in enumerate(chars, 1):
            for page in PAGES:
                rr = await c.get(
                    f"/Platform/Destiny2/{MTYPE}/Account/{MID}/Character/{cid}/Stats/Activities/",
                    params={"count": 250, "page": page, "mode": MODE_PVE})
                js = rr.json()
                if js.get("ErrorCode") != 1:
                    print(f"  角色{ci} 第{page}页 错误 {js.get('ErrorCode')} {js.get('ErrorStatus')}")
                    continue
                rows = (js.get("Response") or {}).get("activities") or []
                if not rows:
                    break
                for a in rows:
                    acts.append({"instance": a["activityDetails"]["instanceId"],
                                 "mode": a["activityDetails"].get("mode"),
                                 "period": a["period"], "name": None})
                print(f"  角色{ci} 第{page}页 +{len(rows)} 累计 {len(acts)}"
                      f"（最早 {rows[-1]['period'][:10]}）")
        # 去重（跨角色会重复）
        seen, uniq = set(), []
        for a in acts:
            if a["instance"] not in seen:
                seen.add(a["instance"])
                uniq.append(a)
        print(f"PVE 历史抽样共 {len(uniq)} 场（去重后）")

        # 分层抽样：在整个时间跨度上均匀取 SAMPLE 场
        SAMPLE = 40
        step = max(1, len(uniq) // SAMPLE)
        picks = uniq[::step][:SAMPLE]
        print(f"\n抽样拉取 PGCR（{len(picks)} 场，"
              f"跨度 {picks[-1]['period'][:10]} ~ {picks[0]['period'][:10]}）：")
        ok = bad = zero = 0
        codes: dict[str, int] = {}
        sem = asyncio.Semaphore(8)
        lock = asyncio.Lock()

        async def probe(a: dict) -> None:
            nonlocal ok, bad, zero
            async with sem:
                t0 = time.perf_counter()
                rr = await c.get(
                    f"/Platform/Destiny2/Stats/PostGameCarnageReport/{a['instance']}/")
                el = time.perf_counter() - t0
                try:
                    js = rr.json()
                except Exception:  # noqa: BLE001
                    js = {}
            code = js.get("ErrorCode")
            entries = (js.get("Response") or {}).get("entries") or []
            me = next((e for e in entries
                       if (e.get("player", {}).get("destinyUserInfo", {})
                           .get("membershipId") == MID)), None)
            nw = len(((me or {}).get("extended") or {}).get("weapons") or [])
            kills = (((me or {}).get("values") or {})
                     .get("kills", {}) or {}).get("basic", {}).get("value") or 0
            key = (f"http={rr.status_code} code={code} "
                   f"{js.get('ErrorStatus') or ''} 本人条目={'有' if me else '无'}")
            async with lock:
                codes[key] = codes.get(key, 0) + 1
                if code == 1 and me:
                    ok += 1
                    if not nw and not kills:
                        zero += 1
                else:
                    bad += 1
                    print(f"  NG {a['period'][:16]} inst={a['instance']} "
                          f"mode={a['mode']} {key} 耗时={el:.1f}s")

        await asyncio.gather(*(probe(a) for a in picks))
        print(f"\n小结：可正常取到 {ok} / 取不到 {bad}"
              f"（其中本人武器=0 且击杀=0 的空场 {zero} 场，照旧算成功）")
        print("返回分布：")
        for k, v in sorted(codes.items(), key=lambda kv: -kv[1]):
            print(f"  {v:3d}  {k}")


if __name__ == "__main__":
    asyncio.run(main())
