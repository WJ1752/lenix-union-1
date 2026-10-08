"""探针：用 PGCR 的 startSeconds/timePlayedSeconds 还原「谁还在场」。

用法: python _rtest/ft_window_probe.py [玩家名#编号] [历史条数]
"""
import asyncio
import datetime as dt
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2  # noqa: E402
import bot_fireteam as ft  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Required#9992"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12


async def main():
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    print(f"member: {m['display']}#{d2.fmt_code(m['code'])} mtype={mtype} mid={mid}")
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": "200,204,1000"}, no_cache=True)
    resp = __import__("json").loads(r.content.decode("utf-8-sig"))["Response"]
    cid = ft._active_cid(resp)
    cur = ft._current(resp)
    now = dt.datetime.now(dt.timezone.utc)
    print("当前:", None if not cur else
          f"hash={cur[1]} {await ft._activity_label(cur[1])} started={cur[2]} "
          f"elapsed={(now - cur[2]).total_seconds():.0f}s")

    # 原始历史（不剔巡逻），看进行中的那局在不在
    raw = await d2.activity_history(mtype, mid, cid, 0, count=N)
    print(f"\n原始历史 {len(raw)} 条：")
    for e in raw:
        p = ft._parse_dt(e.get("period", ""))
        end = ft._end_of(e)
        live = "← 进行中" if (cur and p and abs((p - cur[2]).total_seconds()) < 600) else ""
        print(f"  {e.get('period')} dur={e.get('duration'):>6} end={end} ref={e.get('ref')} "
              f"{e.get('name')!r} inst={e.get('instance')} {live}")

    print("\n逐场 PGCR 参与者在场窗口（start / play / 离场时刻 / 全程?）：")
    for e in raw:
        inst = e.get("instance")
        if not inst:
            continue
        try:
            pg = await ft._pgcr(inst, fresh=True)
        except Exception as ex:  # noqa: BLE001
            print(f"  {inst} <拉取失败 {type(ex).__name__}>")
            continue
        ad = pg.get("activityDetails") or {}
        dur = max([int(x.get("duration") or 0) for x in [e]] or [0])
        ents = pg.get("entries") or []
        seen = set()
        print(f"\n  [{inst}] {e.get('name')!r} modes={ad.get('modes')} 历史dur={dur}s entries={len(ents)}")
        for x in ents:
            i = ft._entry_info(x)
            v = x.get("values") or {}
            st = ((v.get("startSeconds") or {}).get("basic") or {}).get("value")
            tp = ((v.get("timePlayedSeconds") or {}).get("basic") or {}).get("value")
            comp = ((v.get("completed") or {}).get("basic") or {}).get("value")
            pc = ((v.get("playerCount") or {}).get("basic") or {}).get("value")
            dup = " (重复账号)" if i["mid"] in seen else ""
            seen.add(i["mid"])
            left = None
            if st is not None and tp is not None:
                left = st + tp
            print(f"     {i['name']:<22} start={st} play={tp} 离场@{left} "
                  f"全程={left is not None and dur and left >= dur - 5} completed={comp} "
                  f"playerCount={pc} kills={i['kills']}{dup}")
    await d2.close() if hasattr(d2, "close") else None


if __name__ == "__main__":
    asyncio.run(main())
