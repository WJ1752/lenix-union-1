"""本周轮换卡实拍：拿真实数据（宗师/遗失区域走新代码的「换轮识别」路径）渲染成 PNG。

    .venv/Scripts/python.exe _rtest/rot_card_shot.py
    .venv/Scripts/python.exe _rtest/rot_card_shot.py --stale   # 额外出一张「数据源还没换轮」的样子

产物落在 _rtest/_rot_card.png（+ .html），人工核对：突袭/地牢 = 官方周常挑战、
宗师 = 军火交易商·欧洲无人区·首通掉落驱逐引擎、遗失区域 9 区。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bot_cards  # noqa: E402
import card_render  # noqa: E402
import destiny_data as d2  # noqa: E402


def shot(rot, dist, ls, gm, name: str) -> None:
    html = bot_cards.rotation_card(rot, dist, ls, gm)
    out = os.path.join(ROOT, "_rtest", name)
    open(os.path.join(ROOT, "_rtest", name.replace(".png", ".html")), "w",
         encoding="utf-8").write(html)
    open(out, "wb").write(asyncio.run(card_render.html_to_png(html)))
    print("已渲染", out)
    print("   突袭 =", rot.get("raids"), " 地牢 =", rot.get("dungeons"),
          f"（周界 {rot.get('label')}，配对表{'对上' if rot.get('matched') else '没对上'}）")
    print("   宗师 =", gm.get("zh"), "/", gm.get("dest_zh"), "| 首通掉落",
          (gm.get("weapon") or {}).get("zh"), (gm.get("weapon") or {}).get("type"),
          "| stale =", gm.get("stale"))
    print("   遗失区域 =", f"{ls.get('day')}（{ls.get('label')}）",
          [s["dest_zh"] + "·" + s["zh"] for s in (ls.get("sectors") or [])])
    print("   扭曲星球 =", dist.get("dest"), dist.get("range"), "→", dist.get("next_dest"))


def main() -> None:
    rot = asyncio.run(d2.rotation_week())
    ls = asyncio.run(d2.lost_sectors_today())
    gm = asyncio.run(d2.gm_this_week())
    shot(rot, d2.distortion_now(), ls, gm, "_rot_card.png")
    if "--stale" in sys.argv:
        bad = dict(gm, stale=True, warn="lfcarry 页写的还是 10月6日 那个周界（演示）")
        shot(rot, d2.distortion_now(), ls, bad, "_rot_card_stale.png")


if __name__ == "__main__":
    main()
