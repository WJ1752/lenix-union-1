"""为 DIM 板块（背包/成就/配装）生成运行时索引。

产出三个文件到 manifest_index/（都很小，会进 exe 打包清单）：

  dim_items.json   物品定义瘦身表
                   hash → [名称, itemType, 类型名, 稀有度名, 稀有度, 图标, 水印,
                           职业, 背包桶, 装备槽, 不可转移, 可穿戴, 分类hash,
                           伤害类型, 破盾类型, 来源, 属性{statHash:值}]
                   源数据 raw_items.json 有 210MB，运行时不可能整读（先例见
                   build_eververse_index.py），所以裁成这张表。
                   注意：这份缓存里的 allowActions 被规范化成了布尔值，逐项的
                   transferItem/equipItem 标志拿不到；「能不能搬」按 DIM 的做法
                   用 不可转移+可穿戴+背包桶+类型 推导，最终以 Bungie 报错为准。
  dim_buckets.json 背包桶定义：hash → [名称, 分类, 容量, 位置, 图标]
                   「容量」就是游戏里背包/仓库那一格的上限（邮政官 21 格等）。
  dim_categories.json 物品分类定义：hash → 名称（护甲/武器/模组…，用于分栏）
  dim_loadouts.json   配装外观定义：游戏内配装只给 nameHash/iconHash/colorHash，
                      名称与图标要从这三张定义表取。
                      {"n": {hash: 名称}, "i": {hash: 图标}, "c": {hash: 底色图}}

用法：
    .venv/Scripts/python.exe build_dim_index.py            # 生成全部索引
    .venv/Scripts/python.exe build_dim_index.py --defs     # 只重建三张小定义表
    .venv/Scripts/python.exe build_dim_index.py --items    # 只重建物品瘦身表（约 25s）
    .venv/Scripts/python.exe build_dim_index.py --diag     # 只看几个 hash 的原始字段
"""
import json
import os
import re
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(ROOT, "manifest_index")
RAW = os.path.join(IDX, "raw_items.json")

KEY_RE = re.compile(r'"(\d+)":\s*\{')

# itemType 里只有这两个不是背包里的东西：4=Message(系统消息)、20=Dummy(占位)。
# 注意 21/22/24/29 看着像占位其实是真物品（飞船/载具/机灵外壳/终结技），别误杀。
SKIP_TYPES = {4, 20}


def iter_items(path):
    """流式遍历 {"hash": {obj}} —— 逐个对象 json.loads，避免整读 210MB"""
    data = open(path, encoding="utf-8").read()
    n = len(data)
    i = 0
    while True:
        m = KEY_RE.search(data, i)
        if not m:
            return
        h = m.group(1)
        j = m.end() - 1
        depth = 0
        instr = False
        esc = False
        k = j
        while k < n:
            c = data[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        yield h, data[j:k + 1]
        i = k + 1


def _manifest():
    key = os.environ.get("BUNGIE_API_KEY", "")
    if not key:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if line.startswith("BUNGIE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    c = httpx.Client(timeout=120, headers={"X-API-Key": key})
    m = c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
    paths = m["jsonWorldComponentContentPaths"]["zh-chs"]
    base = "https://www.bungie.net"

    def fetch(name):
        return json.loads(c.get(base + paths[name]).text)
    return fetch


def _num(v):
    return v if isinstance(v, (int, float)) else (1 if v else 0)


def build_items():
    t = time.time()
    out = {}
    skipped = 0
    for h, blob in iter_items(RAW):
        o = json.loads(blob)
        dp = o.get("displayProperties") or {}
        name = (dp.get("name") or "").strip()
        if not name or not dp.get("hasIcon") or o.get("redacted"):
            skipped += 1
            continue
        ty = o.get("itemType")
        if ty in SKIP_TYPES:
            skipped += 1
            continue
        # 少数条目这几个字段是 bool/None 而不是对象（Manifest 里存在这种脏数据），统一挡掉
        inv = o.get("inventory") if isinstance(o.get("inventory"), dict) else {}
        eq = o.get("equippingBlock") if isinstance(o.get("equippingBlock"), dict) else {}
        tier = inv.get("tierType")
        stats = {}
        for s in o.get("investmentStats") or []:
            v = s.get("value") or 0
            if v:
                stats[str(s.get("statTypeHash"))] = v
        out[h] = [
            name,                                  # 0
            ty,                                    # 1
            o.get("itemTypeDisplayName") or "",    # 2
            inv.get("tierTypeName") or "",         # 3
            tier if isinstance(tier, int) else 0,  # 4
            dp.get("icon") or "",                  # 5
            o.get("iconWatermark") or "",          # 6
            o.get("classType") if isinstance(o.get("classType"), int) else 3,  # 7
            inv.get("bucketTypeHash") or 0,        # 8
            eq.get("equipmentSlotTypeHash") or 0,  # 9
            _num(o.get("nonTransferrable")),       # 10
            _num(o.get("equippable")),             # 11
            o.get("itemCategoryHashes") or [],     # 12
            o.get("defaultDamageType") or 0,       # 13
            o.get("breakerType") or 0,             # 14
            (o.get("displaySource") or "")[:120],  # 15
            stats,                                 # 16
        ]
    path = os.path.join(IDX, "dim_items.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"物品索引 {len(out)} 条（跳过 {skipped}）→ {os.path.getsize(path)/1048576:.2f} MB，"
          f"{time.time()-t:.1f}s")


def build_defs():
    fetch = _manifest()
    t = time.time()
    b = fetch("DestinyInventoryBucketDefinition")
    out = {}
    for h, v in b.items():
        out[h] = [v.get("displayProperties", {}).get("name") or "",
                  v.get("category") or 0,
                  v.get("itemCount") or 0,
                  v.get("location") or 0,
                  v.get("displayProperties", {}).get("icon") or ""]
    path = os.path.join(IDX, "dim_buckets.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"背包桶 {len(out)} 条 → {os.path.getsize(path)/1024:.1f} KB")

    c = fetch("DestinyItemCategoryDefinition")
    out2 = {h: (v.get("displayProperties", {}).get("name") or "")
            for h, v in c.items()}
    path2 = os.path.join(IDX, "dim_categories.json")
    json.dump(out2, open(path2, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"物品分类 {len(out2)} 条 → {os.path.getsize(path2)/1024:.1f} KB，"
          f"{time.time()-t:.1f}s")


def build_loadout_defs():
    """游戏内配装的外观三张表（名称/图标/底色），都只有 20 来条"""
    fetch = _manifest()
    names = {h: (v.get("name") or "") for h, v in fetch("DestinyLoadoutNameDefinition").items()}
    icons = {h: (v.get("iconImagePath") or "")
             for h, v in fetch("DestinyLoadoutIconDefinition").items()}
    colors = {h: (v.get("colorImagePath") or "")
              for h, v in fetch("DestinyLoadoutColorDefinition").items()}
    path = os.path.join(IDX, "dim_loadouts.json")
    json.dump({"n": names, "i": icons, "c": colors},
              open(path, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"配装外观 {len(names)}/{len(icons)}/{len(colors)} 条 → "
          f"{os.path.getsize(path)/1024:.1f} KB")


def diag():
    """看某几个物品的原始字段（排查索引里字段取错时用）"""
    targets = set(sys.argv[2:]) or set()
    if not targets:
        print("用法: build_dim_index.py --diag <hash> [hash...]")
        return
    for h, blob in iter_items(RAW):
        if h in targets:
            o = json.loads(blob)
            print(h, "|", (o.get("displayProperties") or {}).get("name"),
                  "| type", o.get("itemType"), "|", o.get("itemTypeDisplayName"),
                  "| tier", (o.get("inventory") or {}).get("tierType"),
                  (o.get("inventory") or {}).get("tierTypeName"),
                  "| bucket", (o.get("inventory") or {}).get("bucketTypeHash"),
                  "| slot", (o.get("equippingBlock") or {}).get("equipmentSlotTypeHash"),
                  "| nonT", o.get("nonTransferrable"),
                  "| act", o.get("allowActions"))


if __name__ == "__main__":
    if "--diag" in sys.argv:
        diag()
    elif "--defs" in sys.argv:
        build_defs()
        build_loadout_defs()
    elif "--items" in sys.argv:
        build_items()
    else:
        build_items()
        build_defs()
        build_loadout_defs()
