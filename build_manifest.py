"""下载 Bungie Manifest 并建立中文索引（只跑一次，以后重跑更新）
产出 manifest_index/ 目录：
  weapons.json   hash → {name,type,icon,ammo,desc}
  perks.json     hash → {name,desc,icon}          (SandboxPerk)
  activities.json hash → {name,icon,pgcrImage}    (Activity)
  modes.json     modeType → {name,cat,parents,order,agg,key}  (ActivityMode，见 build_modes.py)
  seasons.json   [{number,name,start,end,bg,prog}]           (Season，见 build_seasons.py)
"""
import json
import os
import re

import httpx

from build_modes import build_modes
from build_seasons import build_seasons

os.makedirs("manifest_index", exist_ok=True)
c = httpx.Client(timeout=120, headers={"X-API-Key": os.environ.get("BUNGIE_API_KEY", "")})

m = c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
paths = m["jsonWorldComponentContentPaths"]["zh-chs"]
base = "https://www.bungie.net"


def fetch(path):
    r = c.get(base + path)
    r.raise_for_status()
    return json.loads(r.text)


# ---------- 武器 ----------
print("下载物品定义（最大，请耐心）...")
items = fetch(paths["DestinyInventoryItemDefinition"])
WEAPON_CATS = {1: "动能", 2: "能量", 3: "威力"}
weapons = {}
for h, it in items.items():
    cats = it.get("itemCategoryHashes", [])
    if not any(x in cats for x in (1, 2, 3)):
        continue
    if it.get("itemType") in (20, 21, 22):  # 跳过护甲类（有武器分类重叠的情况）
        continue
    dp = it.get("displayProperties", {})
    ammo = {1: "主武器", 2: "特殊", 3: "重武器"}.get(it.get("equippingBlock", {}).get("ammoType", 0), "")
    weapons[h] = {
        "name": dp.get("name", ""),
        "type": it.get("itemTypeDisplayName", ""),
        "icon": base + dp.get("icon", "") if dp.get("icon") else "",
        "ammo": ammo,
        "cat": next((WEAPON_CATS[x] for x in cats if x in WEAPON_CATS), ""),
        "desc": re.sub(r"\n+", " ", dp.get("description", ""))[:200],
    }
json.dump(weapons, open("manifest_index/weapons.json", "w", encoding="utf-8"), ensure_ascii=False)
print("武器条目:", len(weapons))

# ---------- Perk ----------
print("下载 perk 定义...")
perks_raw = fetch(paths["DestinySandboxPerkDefinition"])
perks = {}
for h, p in perks_raw.items():
    dp = p.get("displayProperties", {})
    if not dp.get("name"):
        continue
    perks[h] = {
        "name": dp["name"],
        "desc": re.sub(r"\n+", " ", dp.get("description", "")),
        "icon": base + dp.get("icon", "") if dp.get("icon") else "",
    }
json.dump(perks, open("manifest_index/perks.json", "w", encoding="utf-8"), ensure_ascii=False)
print("perk 条目:", len(perks))

# ---------- 活动/地图 ----------
print("下载活动定义...")
acts_raw = fetch(paths["DestinyActivityDefinition"])
acts = {}
for h, a in acts_raw.items():
    dp = a.get("displayProperties", {})
    if not dp.get("name"):
        continue
    acts[int(h)] = {
        "name": dp["name"],
        "icon": base + dp.get("icon", "") if dp.get("icon") else "",
        "pgcr": base + a.get("pgcrImage", "") if a.get("pgcrImage") else "",
    }
json.dump(acts, open("manifest_index/activities.json", "w", encoding="utf-8"), ensure_ascii=False)
print("活动条目:", len(acts))

# ---------- 活动模式 ----------
print("下载活动模式定义...")
modes = build_modes(c, base, paths)
print("模式条目:", len(modes))

# ---------- 赛季 ----------
print("下载赛季定义...")
seasons = build_seasons(c, base, paths)
print("赛季条目:", len(seasons))
print("完成")
