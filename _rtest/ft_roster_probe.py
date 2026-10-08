"""探针：/队伍 名单实时性 —— PGCR entries 里「仍在场」与「已离场」的人各有什么签名。

用法: python _rtest/ft_roster_probe.py [玩家名#编号]
"""
import asyncio
import datetime as dt
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2  # noqa: E402
import bot_fireteam as ft  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Required#9992"


async def prof_of(mtype, mid, comps):
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": comps}, no_cache=True)
    return json.loads(r.content.decode("utf-8-sig")).get("Response") or {}


def tr_brief(prof):
    tr = (prof.get("profileTransitoryData") or {}).get("data") or {}
    ta = tr.get("currentActivity") or {}
    return (f"start={ta.get('startTime')} players={ta.get('numberOfPlayers')} "
            f"opp={ta.get('numberOfOpponents')} party="
            f"{[p.get('membershipId') for p in (tr.get('partyMembers') or [])]}")


def act204(prof):
    out = []
    for cid, d in ((prof.get("characterActivities") or {}).get("data") or {}).items():
        out.append(f"h={d.get('currentActivityHash')} started={d.get('dateActivityStarted')}")
    return "; ".join(out)


async def main():
    m = await d2.resolve_member(NAME)
    if not m:
        print("没找到玩家", NAME)
        return
    mtype, mid = m["mtype"], m["mid"]
    print(f"member: {m['display']}#{d2.fmt_code(m['code'])} mtype={mtype} mid={mid}")

    prof = await prof_of(mtype, mid, "200,204,1000")
    print("self 1000:", tr_brief(prof))
    print("self 204 :", act204(prof))
    cur = ft._current(prof)
    if cur:
        print(f"self cur : cid={cur[0][-5:]} hash={cur[1]} ({await ft._activity_label(cur[1])}) "
              f"kind={await ft._activity_kind(cur[1])} bucket={await ft.activity_bucket(cur[1])} "
              f"started={cur[2]}")

    cid = ft._active_cid(prof)
    hist = await ft._hist_rows(mtype, mid, cid, count=5)
    if not hist:
        print("没有对局历史")
        return
    now = dt.datetime.now(dt.timezone.utc)
    print(f"\n历史前 3（now={now}）：")
    for e in hist[:3]:
        print("  ", e["period"], repr(e["name"]), "dur=", e["duration"],
              "inst=", e["instance"], "end=", ft._end_of(e))

    target = next((e for e in hist if (ft._end_of(e) or now) >= now - dt.timedelta(hours=3)),
                  hist[0])
    print(f"\nPGCR {target['instance']} ({target['name']}) 逐 entry 实时签名：")
    pgcr = await ft._pgcr(target["instance"], fresh=True)
    ad = pgcr.get("activityDetails") or {}
    print("  modes=%s entries=%s" % (ad.get("modes"), len(pgcr.get("entries") or [])))
    for x in (pgcr.get("entries") or []):
        i = ft._entry_info(x)
        vals = x.get("values") or {}
        dur = (vals.get("activityDurationSeconds") or {}).get("basic", {}).get("value")
        st = ((vals.get("standing") or {}).get("basic") or {}).get("value")
        print(f"\n  - {i['name']} mid={i['mid']} mtype={i['mtype']} team={i['team']} "
              f"kills={i['kills']} durationSeconds={dur} standing={st}")
        try:
            p2 = await prof_of(i["mtype"], i["mid"], "204,1000")
        except Exception as e:  # noqa: BLE001
            print("      <profile 拉取失败>", type(e).__name__, e)
            continue
        print("      1000:", tr_brief(p2))
        print("      204 :", act204(p2))
    print("\nPGCR start period =", target["period"], "| self 1000 start =",
          ((prof.get("profileTransitoryData") or {}).get("data") or {})
          .get("currentActivity", {}).get("startTime"))
    if hasattr(d2, "close"):
        await d2.close()


if __name__ == "__main__":
    asyncio.run(main())
