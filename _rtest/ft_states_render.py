"""探针：/队伍 四种状态的卡片渲染自查 + 「刚打完」判定单测。

- 单测：把 now 拨回那场突袭结束 2 秒后，_newest_finished 必须认出它（用户 2026-10-07 13:50:51 报的场景）
- 渲染：①刚结束（ended，名单/指标砖来自该场 PGCR）②轨道+上一场行 ③世界/社交 ④在打（pending）
"""
import asyncio, sys
from datetime import datetime, timedelta, timezone
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2, bot_fireteam as ft, bot_cards, card_render

NAME = "Required#9992"

async def main():
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": "200,204,1000"}, no_cache=True)
    prof = __import__("json").loads(r.content.decode("utf-8-sig"))["Response"]
    cid = ft._active_cid(prof)
    hist = await ft._hist_rows(mtype, mid, cid, count=12)
    print("真对局历史（前 6 条）:")
    for e in hist[:6]:
        print(f"  {e['period']} {e['name']} dur={e['duration']} end={ft._end_of(e)}")
    assert hist, "历史为空，无法验证"

    # 单测：把 now 拨到最新那场结束 +2 秒
    newest = hist[0]
    end = ft._end_of(newest)
    now = end + timedelta(seconds=2)
    got = ft._newest_finished(hist, now)
    print(f"\n[单测] now={now}（= 最新一场结束 +2s）→ {got['name']}")
    assert got is newest, "刚打完没被认出来（用户报的场景复现）"
    assert ft._newest_finished(hist, end + timedelta(minutes=11)) is None, "宽限窗口外不该认"
    last = ft._last_of(got)
    print("       上一场行 =", last)
    assert last["minutes"] == int(newest["duration"] // 60) and last["span"]

    # ① 刚结束 / 刚打完的「已结束卡」：拿最新一场**突袭**（指标砖只在突袭/地牢出，
    #    别拿日常 3 人活动当样本，否则断言"缺指标砖"其实是样本选错）
    raid = next((e for e in hist if 4 in (e.get("modes") or [])), None)
    assert raid, "最近 12 场里没有突袭，无法验证指标砖"
    outs = []
    d_ended = await ft._match_brief(m, mtype, mid, [], raid, state="ended")
    print(f"\n[刚结束] activity={d_ended['activity']} mode={d_ended['mode_name']} "
          f"dur={d_ended['duration_min']} members={len(d_ended['members'])} "
          f"left_out={d_ended['left_out']}")
    outs.append((bot_cards.fireteam_card(d_ended), "_ft_ended.png"))

    # ② 轨道 + 上一场行
    d_orbit = await ft._career_brief(m, mtype, mid, prof, [], state="orbit",
                                     mode_name="轨道待机", last=last)
    outs.append((bot_cards.fireteam_card(d_orbit), "_ft_orbit.png"))

    # ③ 世界/社交（真实当前态）
    d_now = await ft.collect(NAME)
    print(f"[当前态] state={d_now['state']} activity={d_now.get('activity')!r} last={d_now.get('last')}")
    outs.append((bot_cards.fireteam_card(d_now), "_ft_now.png"))

    for html, fn in outs:
        for bad in ("Traceback", "undefined", "None</"):
            assert bad not in html, f"{fn} 出现异常串: {bad}"
    assert "上一场" in outs[1][0], "轨道卡缺「上一场」行"
    assert raid["name"] in outs[0][0] and "ft-tile" in outs[0][0], "刚结束卡缺活动名/指标砖"
    print("\nDOM 断言通过")
    for html, fn in outs:
        png = await card_render.html_to_png(html, width=1180, scale=2)
        open(f"_rtest/{fn}", "wb").write(png)
        print("  ", fn, len(png), "bytes")
    await card_render.close()

asyncio.run(main())
