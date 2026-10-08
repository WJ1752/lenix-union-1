"""名片排版（方图 vs 全幅名片）自查：把带 emblem_bg 的卡片渲成 PNG 到 _rtest/

跑法：python _rtest/namecard_fix.py [玩家名] [before|after]
"""
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import bot_cards  # noqa: E402
import card_render  # noqa: E402
import destiny_data as d2  # noqa: E402
import webui  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
TAG = sys.argv[2] if len(sys.argv) > 2 else "before"


async def shot(name: str, html: str) -> None:
    t = time.time()
    try:
        png = await card_render.html_to_png(html)
        open(f"_rtest/{name}.png", "wb").write(png)
        print(f"  OK _rtest/{name}.png {len(png) // 1024}KB · {time.time() - t:.1f}s")
    except Exception:  # noqa: BLE001
        print(f"  FAIL {name}")
        traceback.print_exc()


async def main() -> None:
    # 1. PvP 战绩卡顶部 hero（用户报的那张）
    rep = await d2.mode_report(NAME, 5, career=True)
    html = webui.render_match_card(rep, "PVP 熔炉竞技场战绩", "5")
    await shot(f"nc_{TAG}_hero_pvp", html)

    # 2. 网站总览页（webui.render_card 的角色行）
    data = await d2.full_report(NAME)
    await shot(f"nc_{TAG}_web_all", webui.render_card(data, "all"))

    # 3. /玩家 卡（bot_cards.player_card 的角色行）
    await shot(f"nc_{TAG}_player", bot_cards.player_card(data))

    # 4. /生涯 卡（namebar + 分职业 cbanner）
    try:
        career = await d2.career_report(NAME)
        await shot(f"nc_{TAG}_career", bot_cards.career_card(career))
    except Exception as exc:  # noqa: BLE001
        print("  生涯卡跳过:", exc)
    await card_render.close()


asyncio.run(main())
