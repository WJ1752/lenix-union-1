"""探针：/进度点 全链路验证 —— fetch_checkpoints 真实数据 → checkpoint_card → PNG。"""
import asyncio
import sys

sys.path.insert(0, r"F:\智谱Zcode数据存储\BOT")
import destiny_data as d2  # noqa: E402
import bot_cards  # noqa: E402
import card_render  # noqa: E402


async def main():
    data = await d2.fetch_checkpoints()
    rows = data.get("rows") or []
    print(f"ok={data.get('ok')} rows={len(rows)}")
    for r in rows:
        print(f"  [{r['state']:^7}] {r['kind_cn']} {r['act']} · {r['boss']} "
              f"→ {r['bot']} ({r['players']}/{r['fireteam']})")
    html = bot_cards.checkpoint_card(data)
    for kw in ("进度", "进车", "d2checkpoint.com", "尾王", "第2关", "bungie.net", "众神殿"):
        assert kw in html, f"卡片缺关键字: {kw}"
    assert "万神殿" not in html, "卡片不应再出现自译的万神殿（官方=众神殿）"
    assert "离场" not in html, "卡片不应再显示已离场点位"
    for r in data.get("rows") or []:
        assert r["state"] in ("ready", "full"), f"过滤后仍有不可用点位: {r}"
    for bad in ("Traceback", "undefined", "None</"):
        assert bad not in html, f"卡片出现异常串: {bad}"
    txt = d2.checkpoints_text(data)
    assert "/j CheckpointBot#" in txt, "文字版缺 /j 指令行"
    assert sum(1 for ln in txt.splitlines() if ln.startswith("/j ")) == len(data.get("rows") or []), \
        "文字版 /j 行数与点位数不符"
    assert "离场" not in txt, "文字版不应再显示已离场点位"
    print("--- 文字版 ---")
    print(txt)
    print("--- dom-assert ok ---")
    open(r"_rtest/_cp_card.html", "w", encoding="utf-8").write(html)
    png = await card_render.html_to_png(html, width=900, scale=2)
    open(r"_rtest/_cp_card.png", "wb").write(png)
    print("png bytes:", len(png))
    await card_render.close()


if __name__ == "__main__":
    asyncio.run(main())
