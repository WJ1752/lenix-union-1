"""构建武器筛选索引 manifest_index/weapon_filter_index.json（离线，读现成缓存）

产出 hash → 精简筛选字段，供 /武器筛选 与图鉴 /catalog 使用：
  n  名称 / t 类型 / a 弹药 / c 分类（动能/能量/威力）
  e  元素（动能/电弧/烈日/虚空/冰影/缚丝）
  f  框架（固有特性名）/ p 可选特性名列表
  r  射速（每分钟发射数）/ cr 可锻造 / i 图标 / w 水印
  en / cht  英文名 / 台服繁体名（build_locale_index.py 的 wname），
            让「/武器筛选 Fatebringer」「图鉴搜 Crota」这类英文/繁体名也能命中
"""
import json

WF = json.load(open("manifest_index/weapons_full.json", encoding="utf-8"))
RAW = json.load(open("manifest_index/raw_items.json", encoding="utf-8"))
GROUPS = json.load(open("manifest_index/pattern_groups.json", encoding="utf-8"))["groups"]
try:  # 英文/繁体武器名（build_locale_index.py 产出；没建过就留空，不影响中文筛选）
    WNAME = json.load(open("manifest_index/name_i18n.json", encoding="utf-8")).get("wname") or {}
except Exception:  # noqa: BLE001
    WNAME = {}

# 可锻造 = 出现在锻造图案分组里；同名的普通版/专家版都算
CRAFT = {}
for gname, lst in GROUPS.items():
    for nm in lst:
        CRAFT.setdefault(nm, gname)

DMG = {1: "动能", 2: "电弧", 3: "烈日", 4: "虚空", 5: "突袭", 6: "冰影", 7: "缚丝"}
# 动能/能量/威能槽（weapons_full 里的 cat 是按 itemCategoryHashes 猜的，那个映射是错的：
# 1/2/3 并不是三个槽位，导致「威力」条数比重武器总数还多。槽位要用 equippingBlock 判）
# 用社区叫法「威能」而不是 Manifest 的「威力」，玩家平时说的就是动能槽/能量槽/威能槽
SLOT = {1498876634: "动能", 2465295065: "能量", 953998645: "威能"}

out = {}
for h, w in WF.items():
    it = RAW.get(h) or {}
    elem = DMG.get(it.get("defaultDamageType") or 0, "")
    plugs = w.get("plugs") or {}

    frame = ""
    ins = plugs.get("intrinsic") or []
    if ins:
        frame = ins[0].get("n") or ""

    perks = []
    for c in plugs.get("cols") or []:
        for x in c.get("items") or []:
            n = x.get("n")
            if n:
                perks.append(n)
    for x in ins + (plugs.get("origins") or []):
        if x.get("n"):
            perks.append(x["n"])
    # 去重保序
    perks = list(dict.fromkeys(perks))

    rpm = 0
    for s in w.get("stats") or []:
        if s.get("n") == "每分钟发射数":
            rpm = int(s.get("v") or 0)

    out[h] = {
        "n": w.get("name", ""),
        "t": w.get("type", ""),
        "a": w.get("ammo", ""),
        "c": SLOT.get((it.get("equippingBlock") or {}).get("equipmentSlotTypeHash"), ""),
        "e": elem,
        "f": frame,
        "p": perks,
        "r": rpm,
        "cr": w.get("name", "") in CRAFT,
        "g": CRAFT.get(w.get("name", ""), ""),
        "x": 1 if (it.get("inventory") or {}).get("tierType") == 6 else 0,
        "i": w.get("icon", ""),
        "w": w.get("watermark", ""),
        "en": (WNAME.get(h) or ["", ""])[0],
        "cht": (WNAME.get(h) or ["", ""])[1],
    }

json.dump(out, open("manifest_index/weapon_filter_index.json", "w", encoding="utf-8"),
          ensure_ascii=False, separators=(",", ":"))
print("筛选索引条目:", len(out))
print("可锻造:", sum(1 for v in out.values() if v["cr"]))
print("无框架:", sum(1 for v in out.values() if not v["f"]))
print("无元素:", sum(1 for v in out.values() if not v["e"]))
