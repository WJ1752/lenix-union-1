"""英文/繁体名查询的武器卡渲染自测（含 alt_name 副标题那一行）"""
import asyncio
import sys

sys.path.insert(0, ".")
import bot_cards
import card_render
import destiny_data as d2
import name_i18n


async def main():
    for q in ("Fatebringer", "龍之氣息"):
        res = d2.search_weapons_full(q, 12)
        top = dict(res[0], alt_name=name_i18n.matched_name(res[0]["hash"], q))
        html = bot_cards.weapon_card(top, [w["name"] for w in res[1:4]])
        png = await card_render.html_to_png(html)
        p = f"_rtest/weapon_{'en' if q.isascii() else 'cht'}.png"
        open(p, "wb").write(png)
        print(f"{q} → {top['name']} / alt_name={top['alt_name']} → {p} {len(png)}B")
    await card_render.close()


asyncio.run(main())
