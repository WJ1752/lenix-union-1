"""给 weapons_full.json 的插件补充 Clarity 社区数据（带数字的效果说明）
数据源: Database-Clarity/Live-Clarity-Database 的 dim.json（key=插件物品hash）
幂等：重复跑只覆盖 ci 字段。重跑 build_weapon_details.py 后需再跑本脚本。
"""
import json

CI = json.load(open("manifest_index/community_dim.json", encoding="utf-8"))
try:
    ZH = {k: v["text"] for k, v in
          json.load(open("manifest_index/perk_zh.json", encoding="utf-8")).items()}
except Exception:  # noqa: BLE001
    ZH = {}


def ci_text(entry: dict) -> str | None:
    """Clarity 条目 → 纯文本（空行分段）"""
    if not entry or "descriptions" not in entry:
        return None
    blocks = entry["descriptions"].get("en", [])
    out = []
    for b in blocks:
        if "spacer" in (b.get("classNames") or []):
            out.append("")
            continue
        line = "".join(seg.get("text", "") for seg in b.get("linesContent", []))
        if line.strip():
            out.append(line.strip())
    text = "\n".join(out).strip()
    return text or None


wf = json.load(open("manifest_index/weapons_full.json", encoding="utf-8"))
n_plug, n_weap = 0, 0
for w in wf.values():
    hit = False
    plugs = w["plugs"]
    groups = [plugs["intrinsic"], plugs["origins"], plugs["barrels"],
              plugs["magazines"], plugs["stocks"], plugs["catalysts"],
              plugs["fixed"], *plugs["trait_cols"]]
    for lst in groups:
        for p in lst:
            # 中文优先（Starside 全 perk 详解），缺失回退 Clarity 英文
            ci = ZH.get(p["n"]) or ci_text(CI.get(p["hash"]))
            if ci:
                p["ci"] = ci
                n_plug += 1
                hit = True
    n_weap += hit

json.dump(wf, open("manifest_index/weapons_full.json", "w", encoding="utf-8"),
          ensure_ascii=False)
print(f"社区数据覆盖：{n_weap}/{len(wf)} 把武器，{n_plug} 个插件")

# 附：Perk 查询索引（sandbox perk hash → 社区数据/属性参数）
items = json.load(open("manifest_index/raw_items.json", encoding="utf-8"))
stat_names = json.load(open("manifest_index/stats.json", encoding="utf-8"))
perk_ci = {}
for h, it in items.items():
    perks = it.get("perks") or []
    if not perks or not perks[0].get("perkHash"):
        continue
    ph = str(perks[0]["perkHash"])
    cur = perk_ci.setdefault(ph, {"ci": None, "stats": {}})
    ci = ZH.get(it.get("displayProperties", {}).get("name", "")) \
        or ci_text(CI.get(h))
    if ci and not cur["ci"]:
        cur["ci"] = ci
    stats = {}
    for s in it.get("investmentStats", []):
        n = stat_names.get(str(s.get("statTypeHash")), "")
        if n and s.get("value"):
            stats[n] = s["value"]
    if stats and not cur["stats"]:
        cur["stats"] = stats
json.dump(perk_ci, open("manifest_index/perk_ci.json", "w", encoding="utf-8"),
          ensure_ascii=False)
print(f"perk_ci 索引：{sum(1 for v in perk_ci.values() if v['ci'])} / {len(perk_ci)} 个 perk 有社区数据")
