# -*- coding: utf-8 -*-
"""从 raw_items.json 抽出「局内商人」可能出售的物品，生成紧凑索引 vendor_items.json。

用途：老九（Xûr）每周商品卡片要把 saleItem 的 hash 变成中文名/图标/职业；
raw_items.json 有 210MB 运行时不可能整读，所以裁成一张小表：
    {hash: [中文名, 类型, 稀有度, 图标路径, classType, 大图路径]}
classType: -1 通用 / 0 泰坦 / 1 猎人 / 2 术士（护甲按职业分组用）。

保留范围（比 eververse_items.json 宽）：可装备的金紫蓝装备、印痕、货币、材料、
任务步骤——商人的货单会轮换，不能只预烤一小撮 hash。

用法：
    .venv/Scripts/python.exe build_vendor_items.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_eververse_index import iter_items, RAW  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "manifest_index", "vendor_items.json")

KEEP_TIERS = {"异域", "传说", "稀有"}
KEEP_TYPE_KEYWORDS = ("印痕", "记忆水晶", "货币", "材料", "任务", "可兑换", "阵营奖励")


def build():
    t = time.time()
    out = {}
    for h, blob in iter_items(RAW):
        o = json.loads(blob)
        dp = o.get("displayProperties") or {}
        name = (dp.get("name") or "").strip()
        if not name or not dp.get("hasIcon"):
            continue
        ty = o.get("itemTypeDisplayName") or ""
        tier = (o.get("inventory") or {}).get("tierTypeName") or ""
        # 可装备的金紫蓝 + 印痕/货币/材料等（异域职业臂 equippable=False 也会漏，
        # 所以装备本体只看 tier，不要求 equippable）；异域武器催化 tier/ty 双空，
        # 只能按名字后缀「催化」抓
        if not ((tier in KEEP_TIERS and ty)
                or name.endswith("催化")
                or any(k in ty for k in KEEP_TYPE_KEYWORDS)):
            continue
        out[h] = [name, ty, tier, dp.get("icon") or "",
                  o.get("classType", -1), o.get("screenshot") or ""]
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"写出 {len(out)} 条 → {OUT} ({os.path.getsize(OUT) / 1048576:.2f} MB)，"
          f"用时 {time.time() - t:.1f}s")


if __name__ == "__main__":
    build()
