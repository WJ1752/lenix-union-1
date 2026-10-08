"""单测 + 渲染自查：「/队伍 名单 = 此刻仍在场的人」。

用用户 2026-10-07 的真实对局当样本（都是 PGCR 里躺着离场者的实例）：
- 17215423347 自定义突袭（6 人位）：7 条 entry —— 白鹿#4533 两次（先 41 秒退出又重进）
- 17215404187 金星破坏（3 人位）：6 条 entry —— 一局里轮换了 3 个离场者
断言：名单只留在场的人、没有重复账号；离场者只计进 left_out。

用法: python _rtest/ft_real_time_test.py
"""
import asyncio
import datetime as dt
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2  # noqa: E402
import bot_fireteam as ft  # noqa: E402
import bot_cards, card_render  # noqa: E402

NAME = "Required#9992"
RAID = ("17215423347", 1397)
DEMO = ("17215404187", 693)


def brief(name, inst, dur, infos, ref):
    """把一份 PGCR 按 _match_brief 的算法过一遍（不联网）：返回 (名单, 离场人数)"""
    period = dt.datetime.fromisoformat(name)
    uniq, left = ft._pgcr_roster(infos, period, ref)
    return [i["name"] for i in uniq], left


async def load(inst, dur):
    pg = await ft._pgcr(inst, fresh=False)
    period = ft._parse_dt(pg.get("period"))
    infos = [i for i in (ft._entry_info(x) for x in (pg.get("entries") or [])) if i["mid"]]
    return period, infos


async def main():
    # ---- ① 6 人位自定义突袭：结束时在场 = 6 人（白鹿只算一次），无离场（他退的 41 秒那条被并掉）
    period, infos = await load(*RAID)
    names, left = brief("2026-10-07T05:27:32+00:00", RAID[0], RAID[1], infos,
                        period + dt.timedelta(seconds=RAID[1]))
    print(f"[突袭 6 人位] 结束时在场 {len(names)} 人 离场 {left} 人")
    for n in names:
        print("   -", n)
    assert len(infos) == 7, f"样本变了：entry 数 {len(infos)}（期望 7）"
    assert len(names) == 6 and left == 0, (names, left)
    assert len(set(names)) == 6, "名单里有重复账号"
    assert infos and names[0] != "", "名单为空"

    # 进行中 t=700s 时刻：还是这 6 个人（白鹿靠"重进那条"在场）
    names_live, left_live = brief("2026-10-07T05:27:32+00:00", RAID[0], RAID[1], infos,
                                  period + dt.timedelta(seconds=700))
    print(f"[突袭 进行中@700s] 在场 {len(names_live)} 人 离场 {left_live} 人")
    assert "白鹿#4533" in names_live and len(names_live) == 6, names_live

    # ---- ② 3 人位「破坏」：6 条 entry，结束时在场只有 3 人（另外 3 个中途走了，其中就有本人）
    period, infos = await load(*DEMO)
    names, left = brief("2026-10-07T05:22:00+00:00", DEMO[0], DEMO[1], infos,
                        period + dt.timedelta(seconds=DEMO[1]))
    print(f"\n[破坏 3 人位] 结束时在场 {len(names)} 人 离场 {left} 人")
    for n in names:
        print("   -", n)
    assert len(infos) == 6, f"样本变了：entry 数 {len(infos)}（期望 6）"
    assert len(names) == 3 and left == 3, (names, left)

    # 进行中 t=200s：仍然 6 人 —— Xinatus 刚走 55 秒，落在 3 分钟宽限内，按"宁多留不误删"保留
    names_live, left_live = brief("2026-10-07T05:22:00+00:00", DEMO[0], DEMO[1], infos,
                                  period + dt.timedelta(seconds=200))
    print(f"[破坏 进行中@200s] 在场 {len(names_live)} 人 离场 {left_live} 人（宽限内，刚走的仍保留）")
    assert "Required#9992" in names_live and len(names_live) == 6, names_live

    # 进行中 t=400s：走的早的人（Xinatus 145s、harmony 116s）已超宽限 → 剔除；
    # 本人 300s 才走（离 400 只差 100s）→ 仍在场
    names_live, left_live = brief("2026-10-07T05:22:00+00:00", DEMO[0], DEMO[1], infos,
                                  period + dt.timedelta(seconds=400))
    print(f"[破坏 进行中@400s] 在场 {len(names_live)} 人 离场 {left_live} 人: {names_live}")
    assert "Required#9992" in names_live, names_live
    assert "Xinatus#3179" not in names_live and "harmony#1797" not in names_live, \
        "早退超宽限的人不该在 t=400s 的名单里"
    assert left_live == 2, left_live

    # ---- ③ 走完整 collect() + 渲染：用「破坏」那一局出已结束卡（带 left_out 提示行）
    m = await d2.resolve_member(NAME)
    mtype, mid = m["mtype"], m["mid"]
    prof = await ft._profile_ex(mtype, mid, "200,204,1000")
    cid = ft._active_cid(prof)
    hist = await ft._hist_rows(mtype, mid, cid, count=12)
    row = next((e for e in hist if e["instance"] == DEMO[0]), None)
    assert row, "历史里找不到「破坏」那一局"
    data = await ft._match_brief(m, mtype, mid, [], row, state="ended")
    print(f"\n[卡片数据] {data['activity']} 名单 {len(data['members'])} 行 "
          f"left_out={data['left_out']} roster={[r['name'] for r in data['roster']]}")
    assert data["left_out"] == 3, data["left_out"]
    html = bot_cards.fireteam_card(data)
    for bad in ("Traceback", "undefined", "None</"):
        assert bad not in html, f"卡里有异常串: {bad}"
    assert "另有 3 人已中途离场" in html, "卡上缺「中途离场」提示行"
    assert "此刻仍在场" in html, "页脚没写实时口径"
    png = await card_render.html_to_png(html, width=1180, scale=2)
    open(r"_rtest/_ft_realtime_ended.png", "wb").write(png)
    print("   -> _rtest/_ft_realtime_ended.png", len(png), "bytes")

    # ---- ④ 进行中但名单没发布（当前态）：卡上要报官方实时人数
    d_now = await ft.collect(NAME)
    print(f"\n[当前态] state={d_now['state']} activity={d_now.get('activity')!r} "
          f"players_now={d_now.get('players_now')} members={len(d_now['members'])}")
    html2 = bot_cards.fireteam_card(d_now)
    png2 = await card_render.html_to_png(html2, width=1180, scale=2)
    open(r"_rtest/_ft_realtime_now.png", "wb").write(png2)
    print("   -> _rtest/_ft_realtime_now.png", len(png2), "bytes")
    if d_now["state"] == "pending":
        assert f"本场实时 {d_now['players_now']} 人在场" in html2, "进行中卡没报实时人数"

    # ---- ⑤ pending（名单未发布）分支的渲染：用构造数据，不受"此刻是否在打"影响
    d_pending = {"name": "Required#9992", "state": "pending", "in_activity": True, "live": False,
                 "activity": "寡妇城: 匹配", "bucket": 7, "mode_name": "PvE",
                 "started_text": "19:21", "duration_min": 35, "players_now": 3,
                 "members": [{"mid": "1", "name": "Required#9992", "is_self": True, "class": "术士",
                              "light": 2010, "rows": [("生涯总时长", "1,000 小时")], "team": None}]}
    html3 = bot_cards.fireteam_card(d_pending)
    assert "本场实时 3 人在场" in html3, "pending 卡没报实时人数"
    assert "Traceback" not in html3 and "undefined" not in html3

    await card_render.close()
    print("\n全部断言通过")


asyncio.run(main())
