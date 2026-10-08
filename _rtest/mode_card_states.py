"""战绩卡各状态自测：/pve（官方生涯）、/智谋（无生涯块）、空窗口（没打过）

跑法：python _rtest/mode_card_states.py [玩家名]
"""
import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_cards  # noqa: E402
import card_render  # noqa: E402
import destiny_data as d2  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"


async def shot(name: str, html: str) -> None:
    try:
        png = await card_render.html_to_png(html)
        open(f"_rtest/{name}.png", "wb").write(png)
        print(f"  OK _rtest/{name}.png {len(png) // 1024}KB")
    except Exception:  # noqa: BLE001
        print(f"  FAIL {name}")
        traceback.print_exc()


async def main() -> None:
    rep = await d2.mode_report(NAME, 7)
    life = await d2.lifetime_stats(NAME, "allPvE")
    await shot("card_pve", bot_cards.mode_card(rep, "PVE 战绩", "7", life, ("precisionKills",)))

    rep2 = await d2.mode_report(NAME, 63)
    life2 = await d2.lifetime_stats(NAME, "gambit")
    await shot("card_gambit", bot_cards.mode_card(rep2, "智谋战绩", "63", life2))

    # 空窗口：没打过这个模式的玩家（合成一份，只为看空状态排版）
    empty = await d2.mode_report(NAME, 5)
    empty.update({"total": 0, "completed": 0, "wins": 0, "losses": 0, "kills": 0, "deaths": 0,
                  "assists": 0, "hours": 0.0, "matches": [], "breakdown": [], "career": None})
    empty.pop("career", None)
    await shot("card_empty", bot_cards.mode_card(empty, "PVP 熔炉竞技场战绩", "5"))
    await card_render.close()


asyncio.run(main())
