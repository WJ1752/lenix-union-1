# -*- coding: utf-8 -*-
"""生成 manifest_index/weapon_versions.json：武器 hash -> 赛季号/活动标记。

赛季来源：DIM d2ai-module 的 watermark-to-season.json / watermark-to-event.json
（iconWatermark → 赛季号）。season_defs.json（Bungie 官方）提供赛季名。
无水印且无映射 = 首发版本（season 0）。幂等：Bungie manifest 更新后重跑。
"""
import json
import os
import urllib.request
import ssl

ROOT = os.path.dirname(os.path.abspath(__file__))
MI = os.path.join(ROOT, "manifest_index")
CDN = "https://cdn.jsdelivr.net/gh/DestinyItemManager/d2ai-module@master/{}.json"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def _fetch(name: str) -> dict:
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    d = json.load(urllib.request.urlopen(CDN.format(name[:-5]), context=CTX, timeout=60))
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return d


wm_season = _fetch("wm2season.json")
wm_event = _fetch("wm2event.json")

weapons = json.load(open(os.path.join(MI, "weapons_full.json"), encoding="utf-8"))
raw = json.load(open(os.path.join(MI, "raw_items.json"), encoding="utf-8"))

out = {}
unmapped = 0
for h, w in weapons.items():
    it = raw.get(h) or {}
    season, event = 0, False
    for field in ("iconWatermark", "iconWatermarkShelved", "iconWatermarkFeatured"):
        p = it.get(field)
        if not p:
            continue
        if p in wm_season:
            season = max(season, wm_season[p])
        elif p in wm_event:
            event = True
        else:
            unmapped += 1
    out[h] = {"season": season, "event": event}

json.dump(out, open(os.path.join(MI, "weapon_versions.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print("weapons:", len(out), "unmapped watermarks:", unmapped)

# 赛季名速查表（官方英文名 + 起始日期）
defs = json.load(open(os.path.join(MI, "season_defs.json"), encoding="utf-8"))
names = {}
for s in defs.values():
    n = s.get("seasonNumber")
    if n:
        names[str(n)] = {"name": (s.get("displayProperties") or {}).get("name") or "",
                         "start": (s.get("startDate") or s.get("startTimeInSeconds") or "")}
json.dump(names, open(os.path.join(MI, "season_names.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print("season names:", len(names))
