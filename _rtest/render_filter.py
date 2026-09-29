import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot_cards, card_render, weapon_filter as wf
Q = sys.argv[1] if len(sys.argv) > 1 else "白弹 爆破专家"
async def main():
    res = wf.filter_weapons(Q)
    t = time.time()
    png = await card_render.html_to_png(bot_cards.weapon_filter_card(res, Q))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wf_big.png")
    open(out, "wb").write(png)
    print(f"{Q} -> {len(png)}B, {res['total']} 展示, 渲染 {time.time()-t:.1f}s")
asyncio.run(main())
