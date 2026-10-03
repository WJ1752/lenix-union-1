# -*- coding: utf-8 -*-
"""离线补丁：给可塑形异域武器的「催化插槽」（空催化插槽 + N 个 XX改装，游戏内 3~4 选 1）
在 weapons_full.json 的 plugs.cols 里补一列「催化」。
这些插槽的插件被 build_weapon_details 按类目归进了 catalysts 桶（异域催化区），
但它们在游戏里是真正的可选 perk 列（单人合唱的维持生计/失衡弹药/猛攻改装等），
不补列的话武器卡片 perk 区少一列、组合也只能配到固定特性上。
已并入 build 流程的同样逻辑见 build_weapon_details.py（下次全量重建后本脚本可废弃）。"""
import json

items = json.load(open("manifest_index/raw_items.json", encoding="utf-8"))
ps = json.load(open("manifest_index/raw_plugsets.json", encoding="utf-8"))
wfu = json.load(open("manifest_index/weapons_full.json", encoding="utf-8"))


def def_of(h):
    return items.get(str(h)) or {}


def nm(h):
    return (def_of(h).get("displayProperties") or {}).get("name") or ""


patched = []
for h, w in wfu.items():
    it = def_of(h)
    for se in (it.get("sockets", {}).get("socketEntries") or []):
        seth = se.get("reusablePlugSetHash") or se.get("randomizedPlugSetHash")
        if not seth:
            continue
        e = ps.get(str(seth)) or {}
        plug_hashes = [p["plugItemHash"] for p in (e.get("reusablePlugItems") or [])]
        names = [nm(p) for p in plug_hashes]
        if not any(n == "空催化插槽" for n in names):
            continue
        real = [p for p in plug_hashes if nm(p) and not nm(p).startswith("空")]
        if len(real) < 2:
            continue
        col_items = []
        for ph in real:
            d = def_of(ph)
            dp = d.get("displayProperties") or {}
            col_items.append({
                "hash": str(ph),
                "n": dp.get("name") or "",
                "d": d.get("displaySource") or dp.get("description") or "",
                "i": "https://www.bungie.net" + dp.get("icon", "/common/destiny2_content/icons/DestinyPlugSetIcon_0.png") if dp.get("icon") else "",
            })
        col = {"t": "催化", "items": col_items}
        cols = w.setdefault("plugs", {}).setdefault("cols", [])
        if any(c.get("t") == "催化" for c in cols):
            continue
        # 游戏内催化插槽排在特性之后、枪托之前；找不到枪托列就放最后
        idx = len(cols)
        for k, c in enumerate(cols):
            if c.get("t") == "枪托":
                idx = k
                break
        cols.insert(idx, col)
        patched.append((h, w.get("name"), [i["n"] for i in col_items]))

json.dump(wfu, open("manifest_index/weapons_full.json", "w", encoding="utf-8"), ensure_ascii=False)
print("patched:", len(patched))
for h, n, its in patched:
    print(" ", h, n, its)
