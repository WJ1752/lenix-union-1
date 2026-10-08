"""探针：/队伍 实时态判定 —— 直接 dump 204/1000 原始内容 + 走完整 collect()

用法: python _rtest/ft_live_probe.py [玩家名#编号]
"""
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2  # noqa: E402
import bot_fireteam as ft  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Required#9992"


async def main():
    m = await d2.resolve_member(NAME)
    if not m:
        print("没找到玩家", NAME)
        return
    mtype, mid = m["mtype"], m["mid"]
    print(f"member: {m['display']}#{d2.fmt_code(m['code'])} mtype={mtype} mid={mid}")

    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": "100,200,204,1000"}, no_cache=True)
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        print("profile error:", resp.get("ErrorCode"), resp.get("Message"))
        return
    prof = resp["Response"]

    print("\n=== characterActivities ===")
    print(json.dumps(prof.get("characterActivities", {}).get("data"), ensure_ascii=False, indent=1))
    print("\n=== profileTransitoryData ===")
    tr = (prof.get("profileTransitoryData") or {}).get("data")
    print(json.dumps(tr, ensure_ascii=False, indent=1))
    print("\n=== characters (cid/class/lastPlayed/light) ===")
    for cid, c in ((prof.get("characters") or {}).get("data") or {}).items():
        print(f"  {cid} class={c.get('classType')} light={c.get('light')} "
              f"last={c.get('dateLastPlayed')}")

    cur = ft._current(prof)
    print("\n_current() ->", cur)
    if cur:
        kind = await ft._activity_kind(cur[1])
        print("  kind =", kind, " hash =", cur[1])
        print("  label =", await ft._activity_label(cur[1]))
        d = await ft._entity("DestinyActivityDefinition", cur[1])
        print("  entity name =", ((d.get("displayProperties") or {}).get("name")))
        print("  modeTypes =", d.get("activityModeTypes"), " modeHashes =", d.get("activityModeHashes"))
        print("  activityTypeHash =", d.get("activityTypeHash"),
              " typeName =", ((d.get("activityTypeHash") and
                               (await ft._entity("DestinyActivityTypeDefinition",
                                                 int(d["activityTypeHash"]))) or {})
                              .get("displayProperties") or {}).get("name"))
        print("  bucket =", await ft.activity_bucket(cur[1]))

    print("\n=== collect() ===")
    data = await ft.collect(NAME)
    print("state =", data["state"], "| in_activity =", data["in_activity"],
          "| activity =", data.get("activity"), "| mode =", data.get("mode_name"),
          "| started =", data.get("started_text"), "| dur =", data.get("duration_min"))
    for r_ in data["members"]:
        print(f"  - {r_['name']} {r_['class']} {r_['light']} team={r_.get('team')} "
              f"rows={r_.get('rows')} tiles={len(r_.get('tiles') or [])}")
    await d2.close() if hasattr(d2, "close") else None


if __name__ == "__main__":
    asyncio.run(main())
