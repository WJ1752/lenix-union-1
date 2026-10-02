"""卡片渲染自测：把各指令的卡片渲成 PNG 到 _rtest/（不发 QQ）"""
import asyncio
import sys
import traceback

import bot_cards
import card_render
import destiny_data as d2

WEAPON = sys.argv[1] if len(sys.argv) > 1 else "秋风"
PERK = sys.argv[2] if len(sys.argv) > 2 else "热力四射"
PLAYER = sys.argv[3] if len(sys.argv) > 3 else ""


async def shot(name, html):
    try:
        png = await card_render.html_to_png(html)
        open(f"_rtest/{name}.png", "wb").write(png)
        print(f"  OK {name}.png {len(png)} bytes")
    except Exception:
        print(f"  FAIL {name}"); traceback.print_exc()


async def main():
    print("渲染卡片…")
    await shot("notice", bot_cards.notice("没找到玩家", ["确认名字和 <code>#编号</code> 后重试：xx#0000"], "warn"))

    res = d2.search_weapons_full(WEAPON, 12)
    print(f"  武器「{WEAPON}」匹配 {len(res)}")
    if res:
        await shot("weapon", bot_cards.weapon_card(res[0], [w["name"] for w in res[1:4]]))
        await shot("weapon_list", bot_cards.weapons_list_card(res, WEAPON))

    # 金枪（带催化/固定配件）单独看一眼
    for h, w in d2._weapons_full.items():
        if w["plugs"].get("catalysts"):
            await shot("weapon_exotic", bot_cards.weapon_card(w))
            break

    perks = d2.search_perks(PERK, 3)
    print(f"  perk「{PERK}」匹配 {len(perks)}")
    if perks:
        await shot("perk", bot_cards.perk_card(perks, PERK))

    if PLAYER:
        try:
            data = await d2.full_report(PLAYER)
            await shot("player", bot_cards.player_card(data))
            print("  玩家卡数据:", data["display"], data["max_light"])
            career = await d2.career_report(PLAYER)
            await shot("career", bot_cards.career_card(career))
            print("  生涯卡数据:", career["display"], "赛季", len(career["seasons"]),
                  "角色", len(career["chars"]))
        except Exception as exc:  # noqa: BLE001
            print("  玩家卡跳过（Bungie 接口）:", exc)
    await card_render.close()


asyncio.run(main())
