"""渲染 /pvp 卡片（改版前/后）与参考卡，出 PNG 到 _rtest/ 供人眼检查

跑法：python _rtest/pvp_card_shot.py [玩家名] [输出后缀]
"""
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_cards  # noqa: E402
import card_render  # noqa: E402
import destiny_data as d2  # noqa: E402
import webui  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
TAG = sys.argv[2] if len(sys.argv) > 2 else "cur"


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
    t = time.time()
    rep = await d2.mode_report(NAME, 5, career=True)
    print(f"数据 {time.time() - t:.1f}s：{rep['display']} 窗口 {rep['total']} 场，"
          f"生涯 {rep['career']['total']} 场（截断={rep['career']['capped']}），"
          f"模式细分 {len(rep['breakdown'])} 行")
    html = bot_cards.mode_card(rep, "PVP 熔炉竞技场战绩", "5")
    open(f"_rtest/pvp_{TAG}.html", "w", encoding="utf-8").write(html)
    await shot(f"pvp_{TAG}", html)

    # 参考：武器查询卡（用户说的"武器查询风格"）
    res = d2.search_weapons_full("秋风", 3)
    if res:
        await shot("ref_weapon", bot_cards.weapon_card(res[0], [w["name"] for w in res[1:3]]))
    await card_render.close()


asyncio.run(main())
