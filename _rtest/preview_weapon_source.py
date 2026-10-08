"""武器卡「获取方式」行预览：每种来源类型渲染一张 PNG，供定稿看效果。

用法：.venv/Scripts/python _rtest/preview_weapon_source.py
产物：_rtest/weapon_src_preview/<名>.png
"""
from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(HERE, "weapon_src_preview")
os.makedirs(OUT, exist_ok=True)

# (展示名, 查询名) —— 覆盖：冲刺+版本条 / 突袭来源 / 地牢来源 / 公共事件来源 / 无来源
SAMPLES = [
    ("crucible_versions", "秋风"),
    ("raid_exotic", "千语"),
    ("dungeon", "棘蛇"),
    ("public_event", "虚假承诺"),
    ("no_source", "超级坏蛋"),
]


async def main() -> None:
    import bot_cards as bc
    import card_render
    import destiny_data as d2

    for tag, name in SAMPLES:
        try:
            res = d2.search_weapons_full(name, 12)
            if not res:
                print(f"  SKIP {tag}: 没搜到 {name}")
                continue
            w = res[0]
            vers = d2.weapon_versions_by_name(w["name"])
            tags = [d2.season_tag(v["season"]) for v in vers]
            names = [d2.season_name(v["season"]) for v in vers]
            html = bc.weapon_card(w, [x["name"] for x in res[1:4]],
                                  tags or None, len(tags) or 0, names or None)
            png = await card_render.html_to_png(html)
            open(os.path.join(OUT, tag + ".png"), "wb").write(png)
            src = bc._wsrc(w["name"]) or "(无来源行,应整行不显示)"
            print(f"  OK {tag}.png  来源={src[:40]}")
        except Exception:  # noqa: BLE001
            import traceback
            print(f"  FAIL {tag}")
            traceback.print_exc()
    await card_render.close()


if __name__ == "__main__":
    asyncio.run(main())
