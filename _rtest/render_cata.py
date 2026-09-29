import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot_cards, card_render, destiny_data as d2

async def main():
    res = d2.search_weapons_full("伊邪那岐的重担", 3)
    w = res[0]
    print("weapon:", w["name"], w["type"])
    cats = (w.get("plugs") or {}).get("catalysts") or []
    for c in cats:
        print("  cat:", c.get("n"), "| zh:", repr((c.get("zh") or ""))[:80], "| ci:", bool(c.get("ci")))
    png = await card_render.html_to_png(bot_cards.weapon_card(w))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cata_izn.png")
    open(out, "wb").write(png)
    print("->", out, len(png))

asyncio.run(main())
