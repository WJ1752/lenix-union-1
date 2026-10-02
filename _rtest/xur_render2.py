# -*- coding: utf-8 -*-
"""渲染验证2：Kyber 周货真数据 → 分节/芯片抽查 + xur_card.png。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main():
    import destiny_data as d2
    import bot_cards
    import card_render

    ky = await d2._kyber_xur()
    print("kyber:", (ky or {}).get("generatedAt"), "| arrival", (ky or {}).get("arrival"))
    stock = await d2.xur_stock(force=True)
    print("source:", stock.get("source"), "| present:", stock["present"],
          "| items:", len(stock["items"]))
    secs = {}
    for it in stock["items"]:
        secs.setdefault(it["sec"], []).append(it)
    for sec, its in secs.items():
        names = [f"{it['n']}({it['cls'] or '—'}{',随机卷' if it.get('rolled') else ''})"
                 for it in its]
        print(f"  [{sec}] {len(its)}: {' / '.join(names)}")
    for it in stock["items"]:
        if it["sec"] in ("异域武器", "传说武器"):
            chips = ([it["intr"]["name"]] if it.get("intr") else []) \
                + [p["name"] for p in (it.get("plugs") or [])]
            print(f"  芯片 {it['n']}: {chips}")

    png = await card_render.html_to_png(bot_cards.xur_card(stock), width=900, scale=2)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xur_card2.png")
    open(p, "wb").write(png)
    print("写出:", p, f"{len(png) // 1024} KB")


asyncio.run(main())
