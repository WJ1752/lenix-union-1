"""生成 manifest_index/item_zh.json + item_en.json —— 配装卡/宗师武器用的紧凑物品索引

背景：raw_items.json（zh manifest 全量，220MB）/ raw_items_en_lite.json（65MB）是构建
中间产物，**不进 exe 包**（见 D2Query.spec _MI_SKIP）。运行时需要「hash→中文名/图标/
类型名/稀有度/插件分类」和「英文武器名→hash」的只有 /队伍配装 与 /轮换 宗师武器，
这里把需要的字段抽成紧凑 JSON（数组形式），体积进包无压力。

用法: .venv/Scripts/python.exe build_item_index.py   （manifest 更新后重跑）

item_zh.json: {hash: [name, icon_path, itemTypeDisplayName, tierType, plugCategoryIdentifier]}
  —— 缺字段存 ""；tierType 是 inventory.tierType（5=传说 6=异域）。
item_en.json: {英文显示名小写: [hash, ...]}（来自 raw_items_en_lite，同名多卷都留着，
  查的时候取第一个能对上武器索引的）。
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
MI = os.path.join(ROOT, "manifest_index")


def main() -> int:
    zh = json.load(open(os.path.join(MI, "raw_items.json"), encoding="utf-8"))
    out_zh = {}
    for h, v in zh.items():
        if not isinstance(v, dict):
            continue
        dp = v.get("displayProperties") or {}
        name = dp.get("name") or ""
        if not name or name == "？":
            continue
        plug = v.get("plug") or {}
        out_zh[h] = [name, dp.get("icon") or "", v.get("itemTypeDisplayName") or "",
                     int((v.get("inventory") or {}).get("tierType") or 0),
                     plug.get("plugCategoryIdentifier") or ""]
    json.dump(out_zh, open(os.path.join(MI, "item_zh.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"item_zh.json: {len(out_zh)} 条")

    en = json.load(open(os.path.join(MI, "raw_items_en_lite.json"), encoding="utf-8"))
    out_en: dict[str, list] = {}
    for h, v in en.items():
        if not isinstance(v, dict):
            continue
        n = ((v.get("displayProperties") or {}).get("name") or "").strip().lower()
        if not n:
            continue
        out_en.setdefault(n, []).append(h)
    json.dump(out_en, open(os.path.join(MI, "item_en.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"item_en.json: {len(out_en)} 条")

    # 子职业纹章：装备槽返回的子职业 def 图标是通用元素菱形（火焰/虚空等），
    # 好看的职业纹章在同名的 itemCategoryHashes 含 3109687656 的 def 上
    # （枪手→左轮纹章，棱镜猎人→圆纹章）。按 zh 名建映射给 /队伍配装 用。
    crest = {}
    for h, v in zh.items():
        if not isinstance(v, dict) or 3109687656 not in (v.get("itemCategoryHashes") or []):
            continue
        n = ((v.get("displayProperties") or {}).get("name") or "").strip()
        ico = (v.get("displayProperties") or {}).get("icon") or ""
        if n and ico:
            crest.setdefault(n, ico)
    json.dump(crest, open(os.path.join(MI, "sub_crest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"sub_crest.json: {len(crest)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
