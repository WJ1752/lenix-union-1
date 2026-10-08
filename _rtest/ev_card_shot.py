"""光尘商店卡片实拍：拿真实货架（走新代码的时间强关联路径）渲染成 PNG，人工核对时间标注。

    .venv/Scripts/python.exe _rtest/ev_card_shot.py            # 真实数据
    .venv/Scripts/python.exe _rtest/ev_card_shot.py --stale    # 额外出一张「官方延迟」警告条的样子
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


def shot(store: dict, name: str) -> None:
    html = bot_cards.eververse_card(store)
    out = os.path.join(ROOT, "_rtest", name)
    open(os.path.join(ROOT, "_rtest", name.replace(".png", ".html")), "w",
         encoding="utf-8").write(html)
    open(out, "wb").write(asyncio.run(card_render.html_to_png(html)))
    print("已渲染", out)
    for k in ("day", "refresh_at", "next_refresh", "fetched_at", "cycle_days", "stale"):
        v = store.get(k)
        if isinstance(v, float) and v:
            v = f"{d2._ev_dt(v):%Y-%m-%d %H:%M}"
        print(f"   {k} = {v}")
    print("   件数 =", sum(len(s["items"]) for s in store["sections"]),
          {s["name"]: [i["n"] for i in s["items"]] for s in store["sections"]})


def main() -> None:
    store = asyncio.run(d2.eververse_store(force=True))
    shot(store, "_ev_card.png")
    if "--stale" in sys.argv:
        # 造一张「手里还是上一轮」的卡（官方晚切时会走到这个分支）：
        # 归属点/下次刷新整体往前挪一天 → ev_behind=True，时间条上出那句提醒
        bad = dict(store, refresh_at=(store["refresh_at"] or 0) - 86400,
                   next_refresh=(store["next_refresh"] or 0) - 86400,
                   stale=True, day=d2._ev_day((store["refresh_at"] or 0) - 86400))
        shot(bad, "_ev_card_stale.png")


if __name__ == "__main__":
    main()
