# -*- coding: utf-8 -*-
"""构建 manifest_index/rotation_zh.json：/轮换 卡片新增板块的中英映射数据。

数据源与产物：
1. dest  —— DestinyDestinationDefinition (en + zh-chs)：目的地英文名 → 中文名
2. gm    —— en+zh 活动组件按 hash 配对：宗师/日落/征服 系列英副本名 →
            {zh 中文名, 代表 hash（优先 宗师日落 变体，拿它出横图）}
3. ls    —— d2lostsector.report 目录里的遗失区域：活动 hash → 中文名（剥难度后缀）
4. sets  —— 当日首页的护甲套装英名（图标反查 zh 物品名）

重跑时机：新赛季 / 首页出现新套装名时重跑一次；映射缺失时运行时回退英文。
组件缓存在 _rtest/tmp_manifest/，重复跑不重新下载。
"""
import datetime
import json
import os
import re
import zipfile

import httpx

ROOT = os.path.dirname(os.path.abspath(__file__))
sys_path = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, sys_path)
from destiny_data import _idx_file  # noqa: E402

BUNGIE = "https://www.bungie.net"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rotation-build/1.0"}
KEY = ""
for _line in open(os.path.join(ROOT, ".env"), encoding="utf-8-sig", errors="ignore"):
    _m = re.match(r"\s*BUNGIE_API_KEY\s*=\s*(.+)", _line)
    if _m:
        KEY = _m.group(1).strip().strip('"')
API_H = {"X-API-Key": KEY, **UA}
OUT = _idx_file("rotation_zh.json")
CACHE = os.path.join(ROOT, "_rtest", "tmp_manifest")

# zh 侧认这些前缀算「宗师类可轮换活动」；en 侧剥掉这些前后缀拿基础副本名
ZH_TIERS = ("宗师日落", "大师日落", "传说日落", "英雄日落", "日落：", "日落: ",
            "宗师征服", "大师征服", "专家征服", "终极征服")
EN_PREFIX = re.compile(
    r"^(?:the ordeal|nightfall|grandmaster conquest|master conquest|"
    r"expert conquest|ultimate conquest|quest)[:\s]*", re.I)
EN_TIER = re.compile(r"^(?:grandmaster|master|expert|ultimate)[:\s]*", re.I)
EN_SUFFIX = re.compile(r":\s*(?:matchmade|customize|custom|normal|prestige)\s*$", re.I)


def bungie_json(path):
    with httpx.Client(headers=API_H, timeout=120) as c:
        d = c.get(BUNGIE + path).json()
    if isinstance(d, dict) and d.get("ErrorCode") not in (None, 1):
        raise RuntimeError(f"API err {d.get('ErrorCode')}: {d.get('Message')}")
    return d.get("Response", d)


def cached_component(locale, tname):
    """组件下载 + 本地 zip 缓存（重跑不重新下载）"""
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, f"{locale}_{tname}.json.zip")
    if os.path.exists(cache):
        with zipfile.ZipFile(cache) as z:
            return json.loads(z.read(z.namelist()[0]))
    man = bungie_json("/Platform/Destiny2/Manifest/")
    p = man["jsonWorldComponentContentPaths"][locale][tname]
    with httpx.Client(headers=UA, timeout=600) as c:
        r = c.get(BUNGIE + p)
        r.raise_for_status()
        data = r.json()
    with zipfile.ZipFile(cache, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("d.json", json.dumps(data, ensure_ascii=False))
    print(f"  cached {locale}/{tname} -> {cache}")
    return data


def en_base(name: str) -> str:
    n = name.strip()
    n = EN_PREFIX.sub("", n)
    n = EN_TIER.sub("", n)
    n = EN_SUFFIX.sub("", n)
    return n.strip()


def zh_base(name: str) -> str:
    n = name.strip()
    for t in ZH_TIERS:
        if n.startswith(t):
            n = n[len(t):]
            break
    n = re.sub(r"[:：]\s*(自定义|匹配|普通|巅峰|大师|宗师|专家)\s*$", "", n)
    return n.strip("：: ").strip()


def build_dest():
    print("== destinations ==")
    en = cached_component("en", "DestinyDestinationDefinition")
    zh = cached_component("zh-chs", "DestinyDestinationDefinition")
    out = {}
    for h, d in en.items():
        en_n = (d.get("displayProperties") or {}).get("name") or ""
        zh_n = ((zh.get(h) or {}).get("displayProperties") or {}).get("name") or ""
        if en_n and zh_n and en_n != zh_n:
            out[en_n.lower()] = zh_n
    print("  dest map:", len(out))
    return out


def build_acts():
    """en+zh 活动定义按 hash 配对 → [(hash, en_name, zh_name, pgcr)]"""
    en = cached_component("en", "DestinyActivityDefinition")
    zh = cached_component("zh-chs", "DestinyActivityDefinition")
    pairs = []
    for h, d in en.items():
        en_n = (d.get("displayProperties") or {}).get("name") or ""
        if not en_n:
            continue
        zh_n = ((zh.get(h) or {}).get("displayProperties") or {}).get("name") or en_n
        pgcr = BUNGIE + d.get("pgcrImage", "") if d.get("pgcrImage") else ""
        pairs.append((h, en_n, zh_n, pgcr))
    print("  activities paired:", len(pairs))
    return pairs


def build_gm(pairs):
    print("== gm pool ==")
    groups = {}                        # en_base_lower -> {zh, gm_hash, hash, pgcr}
    for h, en_n, zh_n, pgcr in pairs:
        zh_l = zh_n.strip()
        if not any(zh_l.startswith(t) for t in ZH_TIERS):
            continue
        base = en_base(en_n)
        zb = zh_base(zh_n)
        if not base or not zb or len(base) < 3:
            continue
        g = groups.setdefault(base.lower(), {"zh": zb, "gm_hash": "", "hash": "", "pgcr": ""})
        if zh_l.startswith("宗师日落") and not g["gm_hash"]:
            g["gm_hash"] = h
            g["pgcr"] = pgcr
        if not g["hash"]:
            g["hash"] = h
        if not g["pgcr"]:
            g["pgcr"] = pgcr
    out = {}
    for base_l, g in groups.items():
        out[base_l] = {"zh": g["zh"], "hash": str(g["gm_hash"] or g["hash"]), "pgcr": g["pgcr"]}
    print("  gm pool:", len(out))
    return out


def fetch_home_html():
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "lsr_home.html")
    try:
        r = httpx.get("https://d2lostsector.report/", headers=UA,
                      timeout=60, follow_redirects=True)
        r.raise_for_status()
        html = r.text
        open(path, "w", encoding="utf-8").write(html)
    except Exception as e:  # noqa: BLE001
        print("!! 首页在线抓取失败，用本地缓存:", e)
        html = open(path, encoding="utf-8", errors="ignore").read()
    return html


def build_ls(pairs, html):
    print("== lost sector hash -> zh ==")
    hash_by_slug = {}
    for m in re.finditer(
            r'for-website/(\d{6,12})/\1\.[a-z]+[^>]*>\s*<div class="card-header[^"]*">'
            r'<a[^>]*href="/sector/([a-z0-9_]+)"', html):
        hash_by_slug[m.group(2)] = m.group(1)
    # 目录 chunk：页面引用的 next.js chunk 里带全量 {id:hash, escapedname:slug}
    chunks = sorted(set(re.findall(r'/_next/static/chunks/[a-zA-Z0-9_-]+\.js', html)))
    for cp in chunks:
        cf = os.path.join(CACHE, cp.replace("/", "_") + ".zip")
        try:
            if os.path.exists(cf):
                with zipfile.ZipFile(cf) as z:
                    txt = z.read(z.namelist()[0]).decode("utf-8", "ignore")
            else:
                txt = httpx.get("https://d2lostsector.report" + cp,
                                headers=UA, timeout=60, follow_redirects=True).text
                with zipfile.ZipFile(cf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("c.js", txt)
            for m in re.finditer(r'\{id:"(\d{6,12})",escapedname:"([a-z0-9_]+)"', txt):
                hash_by_slug.setdefault(m.group(2), m.group(1))
        except Exception as e:  # noqa: BLE001  单个 chunk 失败不阻塞
            print("  !! chunk fail", cp, e)
    print("  chunks:", len(chunks), "目录条目:", len(hash_by_slug))
    zh_by_hash = {str(h): zh_base(zh_n) for h, en_n, zh_n, _pg in pairs}
    out = {}
    for slug, h in hash_by_slug.items():
        zh = zh_by_hash.get(str(h))
        if zh:
            out[str(h)] = {"zh": zh, "slug": slug}
    print("  映射:", len(out), "/", len(hash_by_slug))
    return out


def today_sets(html):
    pairs, seen = [], set()
    for m in re.finditer(r'alt="([^"]+?) set"', html):
        name = m.group(1)
        if name.lower() in seen:
            continue
        seg = html[max(0, m.start() - 400):m.start()]
        im = re.findall(r'icons/([a-f0-9]{32}\.(?:jpg|png))', seg)
        seen.add(name.lower())
        pairs.append((name, im[-1] if im else ""))
    return pairs


def build_sets(html):
    print("== armor set zh ==")
    pairs = today_sets(html)
    print("  今日套装:", [p[0] for p in pairs])
    icons = {fn: name for name, fn in pairs if fn}
    items = cached_component("zh-chs", "DestinyInventoryItemDefinition")
    out = {}
    for it in items.values():
        dp = it.get("displayProperties") or {}
        icon = (dp.get("icon") or "").rsplit("/", 1)[-1]
        if icon in icons and dp.get("name"):
            out[icons[icon].lower()] = dp["name"]
    for name, fn in pairs:
        print(f"  {name:24s} -> {out.get(name.lower()) or '(未映射,回退英文)'}")
    return out


def main():
    dest = build_dest()
    pairs = build_acts()
    gm = build_gm(pairs)
    html = fetch_home_html()
    ls = build_ls(pairs, html)
    try:
        sets = build_sets(html)
    except Exception as e:  # noqa: BLE001
        print("!! sets build failed:", e)
        sets = {}
    out = {"ver": 2, "built": datetime.date.today().isoformat(),
           "dest": dest, "gm": gm, "ls": ls, "sets": sets}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
