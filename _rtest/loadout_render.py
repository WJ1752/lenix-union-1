"""探针：/队伍配装 卡渲染自查（去机灵 + 武器 perk 栏与护甲列等高）。"""
import asyncio, sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data as d2, bot_loadout, bot_cards, card_render

NAME = sys.argv[1] if len(sys.argv) > 1 else "Required#9992"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"_rtest/_loadout_new.png"

async def main():
    data = await bot_loadout.collect(NAME)
    print("members:", len(data["members"]), "| state:", data.get("state"), data.get("mode_name"))
    for m in data["members"]:
        w = [x["slot"] for x in (m.get("weapons") or [])]
        a = [x["slot"] for x in (m.get("armor") or [])]
        print(f"  {m['name']} hidden={m['hidden']} weapons={w} armor={a}")
        for x in (m.get("weapons") or []):
            print(f"     {x['slot']} {x['name']} perks={[c['name'] for c in x['chips']]}")
    html = bot_cards.loadout_card(data)
    assert "机灵" not in html, "卡片不应再出现机灵"
    for bad in ("Traceback", "undefined", "None</"):
        assert bad not in html, f"卡片出现异常串: {bad}"
    if any(m.get("weapons") for m in data["members"] if not m["hidden"]):
        assert "lo-perks" in html, "武器列缺少 perk 栏"
    open(r"_rtest/_loadout_new.html", "w", encoding="utf-8").write(html)
    png = await card_render.html_to_png(html, width=1180, scale=2)
    open(OUT, "wb").write(png)
    print("png bytes:", len(png), OUT)
    await card_render.close()

asyncio.run(main())
