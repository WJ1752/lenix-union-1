"""渲染 /pvp /pve /智谋 三张卡到 _rtest/ 供人眼检查（改版后）

跑法：python _rtest/mode_cards_new.py [玩家名]
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

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
# 部署机的 bot_config.json 里 pvp/pve_match_cap = 0（无限制），本地源码跑要一致
d2.match_cap = lambda kind: 0


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
    rep = await d2.mode_report(NAME, 63)
    g = rep.get("gambit") or {}
    print(f"智谋数据 {time.time() - t:.1f}s：窗口 {rep['total']} 场 · 胜点图 {len(rep.get('grid_matches') or [])} 格 "
          f"· 官方生涯 {g.get('activitiesEntered')} 场 / 存入荧光 {g.get('motesDeposited')} "
          f"· 窗口荧光 {rep.get('motes')}")
    await shot("new_gambit", bot_cards.mode_card(rep, "智谋战绩", "63"))

    t = time.time()
    rep2 = await d2.mode_report(NAME, 7, endgame=True)
    life = await d2.lifetime_stats(NAME, "allPvE")
    eg = rep2["endgame"]
    print(f"PVE 数据 {time.time() - t:.1f}s：扫描 {eg['scanned']} 场 · 突袭 {eg['raid']} / 地牢 {eg['dungeon']} "
          f"/ 宗师 {eg['gm']} / 大师 {eg['master_nf']} / 终极征服 {eg['ultimate']}(打过 {eg['ultimate_all']}) "
          f"· 成就分 {rep2.get('triumph_now')}/{rep2.get('triumph')} · 终极纪录 {rep2.get('ultimate')} "
          f"· 截断 {eg['capped']}")
    await shot("new_pve", bot_cards.mode_card(rep2, "PVE 战绩", "7", life, ("precisionKills",)))

    rep3 = await d2.mode_report(NAME, 5, career=True)
    await shot("new_pvp", bot_cards.mode_card(rep3, "PVP 熔炉竞技场战绩", "5"))
    await card_render.close()


asyncio.run(main())
