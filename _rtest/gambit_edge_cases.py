"""智谋卡的边界：另一个账号（goldenmidi）+ 空窗口（没打过智谋）"""
import asyncio, os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import bot_cards, card_render, destiny_data as d2
d2.match_cap = lambda kind: 0

async def shot(name, html):
    png = await card_render.html_to_png(html)
    open(f"_rtest/{name}.png", "wb").write(png)
    print(f"  OK _rtest/{name}.png {len(png)//1024}KB")

async def main():
    rep = await d2.mode_report("goldenmidi#0582", 63)
    g = rep.get("gambit") or {}
    print(f"goldenmidi：窗口 {rep['total']} · 官方 {g.get('activitiesEntered')} · 格子 {len(rep.get('grid_matches') or [])}")
    await shot("edge_gambit_gm", bot_cards.mode_card(rep, "智谋战绩", "63"))

    # 空窗口：把智谋数据清成"没打过"，看排版崩不崩（含官方块仍在的情况）
    rep2 = await d2.mode_report("Wj#8984", 63)
    rep2.update({"total": 0, "completed": 0, "wins": 0, "losses": 0, "kills": 0, "deaths": 0,
                 "assists": 0, "hours": 0.0, "matches": [], "grid_matches": [], "breakdown": [],
                 "motes": 0})
    await shot("edge_gambit_empty", bot_cards.mode_card(rep2, "智谋战绩", "63"))
    await card_render.close()

asyncio.run(main())
