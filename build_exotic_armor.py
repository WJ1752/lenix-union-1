"""构建异域护甲索引 manifest_index/exotic_armor.json（需要网络，raw 物品定义走本地缓存）

数据源与分工：
  ① 本地 Manifest 缓存 raw_items.json（zh）——hash / 中文名 / 图标 / 槽位 / 职业 / flavor，
     两源冲突时以它为准（与 build_weapon_details.py 同一套缓存）
  ② raw_items_en_lite.json（en）——英文名。Lite 定义只有名字图标等轻字段（58MB，全量 items 的 1/4），
     首次运行自动从 Bungie 下载并缓存
  ③ raw_plugsets.json——职业金之灵池 / 永劫学派池 / 护甲六维随机 roll 池
  ④ https://starside.work/exotic-armor/index.html——异域特性详版文案（含精确数值，Manifest 里没有）、
     职业金 36 个之灵分列、永劫三学派机制、社区评测。抓取成功后缓存 HTML，断网时可复用

输出契约（下游 bot_cards.py 按此渲染）：
  {"updated": "...", "items": [...]}
  每件至少 hash/name/en/slot/class/icon/perk_name/perk_desc/aliases；
  职业金（slot=职业）另有 perk_cols=[[{name,desc,icon}...],[...]] 两列特性池；
  永劫护甲另有 perk_choices=[{name,desc,icon}...]（学派三选一）。
  六维 stats：EoF 起护甲为随机 roll，Manifest 无固定值，存的是「可能区间」[低, 高]，仅供参考。
幂等：重复跑覆盖。不 git 提交。
"""
import json
import os
import re
from datetime import date
from html import unescape

import httpx

STARSIDE_URL = "https://starside.work/exotic-armor/index.html"
STARSIDE_CACHE = "manifest_index/starside_exotic_armor.html"
OUT = "manifest_index/exotic_armor.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0")

TAG = re.compile(r"<[^>]+>")
BUCKET_SLOT = {3448274439: "头", 14239492: "胸", 3551918588: "臂", 20886954: "腿", 1585787867: "职业"}
CLASS_NAME = {0: "泰坦", 1: "猎人", 2: "术士", 3: "通用"}
SIX_STATS = {2996146975, 392767087, 1943323491, 1735777505, 144602215, 4244567218}

# 社区外号表：宁缺毋滥，只收广为流传、确实通用的叫法（中文社区 NGA/B站/群里常见）。
# 「职业金」是异域职业件的通用俗称；「永劫教派」取自物品 flavor 原文，可直接当检索词。
ALIASES = {
    "刺客风帽": ["腚眼甲"],
    "星界折跃": ["滑板鞋"],
    "相对主义": ["猎人职业金", "职业金"],
    "唯我主义": ["术士职业金", "职业金"],
    "坚忍克己": ["泰坦职业金", "职业金"],
    # 带符号名常被用户打成无符号纯文本（归一化兜底之外再补直达别名）
    "阿尔法·鲁皮之脊": ["阿尔法鲁皮之脊", "鲁皮之脊"],
}

# starside 侧的全称槽位 → 短槽位（与 BUCKET_SLOT 对齐，用于交叉校验）
SLOT_ALIAS = {"头盔": "头", "胸甲": "胸", "胸部护甲": "胸", "臂铠": "臂", "臂甲": "臂",
              "腿部护甲": "腿", "腿甲": "腿"}


_FOOTER_MARKS = ("\n更新 20", "\n数据源：", "\n© ", "\nICP备案", "\nStarside 为非官方")


def _strip_footer(s: str) -> str:
    """starside 页脚/站点说明会混进页面末尾条目的段落里，按标记截断"""
    for mk in _FOOTER_MARKS:
        i = s.find(mk)
        if i != -1:
            s = s[:i]
    return s.rstrip()


def _text(html: str) -> str:
    """HTML 片段 → 纯文本（<br> 转换行；其余标签去掉）——同 scrape_starside_exotic.py"""
    s = re.sub(r"<br\s*/?>", "\n", html)
    s = re.sub(r"</span>\s*<span", "</span> <span", s)  # 高亮词相接处补回空格
    s = TAG.sub("", s)
    s = unescape(s)
    s = re.sub(r"[ \t\u3000]+", " ", s)
    return "\n".join(x.strip() for x in s.split("\n") if x.strip())


def _paras(fragment: str) -> list:
    """一段 HTML 里的所有 <p> → 纯文本行块（主描述在前，data-e 变体附后，保序）"""
    out = []
    for p in re.findall(r"<p\b[^>]*>(.*?)</p>", fragment, re.S):
        t = _text(p).strip()
        if t and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- starside 抓取

def fetch_starside() -> str:
    """抓异域护甲页；成功后缓存；失败时退回上次缓存（返回空串 = 完全不可用）"""
    try:
        r = httpx.get(STARSIDE_URL, timeout=60, follow_redirects=True,
                      headers={"User-Agent": UA})
        r.raise_for_status()
        html = r.text
        if html.count('<article class="rec r-exotic has-au">') < 100:
            raise ValueError("页面条目数异常")
        with open(STARSIDE_CACHE, "w", encoding="utf-8") as f:
            f.write(html)
        print("starside: 在线抓取成功", len(html) // 1024, "KB")
        return html
    except Exception as e:  # noqa: BLE001
        if os.path.exists(STARSIDE_CACHE):
            print(f"starside: 在线抓取失败({e})，改用本地缓存 {STARSIDE_CACHE}")
            return open(STARSIDE_CACHE, encoding="utf-8").read()
        print(f"starside: 不可达且无缓存({e})，将纯 Manifest 构建")
        return ""


def parse_starside(html: str) -> dict:
    """→ {
      armor: {名字: {slot, season, perk_name, perk_paras, reviewer, review}},
      schools: {学派名: paras},          # 永劫教派 3 学派
      spirit_cols: [[名...], [名...]],   # 职业金之灵，18 行 × 左右两列
      spirit_paras: {名: paras},
    }"""
    out = {"armor": {}, "schools": {}, "spirit_cols": [[], []], "spirit_paras": {}}
    if not html:
        return out

    # 普通异域护甲：135 篇 <article class="rec r-exotic has-au">
    for art in html.split('<article class="rec r-exotic has-au">')[1:]:
        nm = re.search(r'<div class="r-nm exo">(.*?)</div>', art, re.S)
        if not nm:
            continue
        name = _text(nm.group(1))
        sub = re.search(r'<div class="r-sub">(.*?)</div>', art, re.S)
        subs = [_text(x) for x in TAG.sub(" ", sub.group(1)).split("  ") if x.strip()] if sub else []
        slot = subs[0] if subs else ""
        season = subs[1] if len(subs) > 1 else ""
        per = re.search(r'<div class="r-cell r-txt r-perk">(.*?)(?=<div class="r-cell|</article>)',
                        art, re.S)
        scope = per.group(1) if per else art
        xp = re.search(r'<span class="xp-n">(.*?)</span>', scope, re.S)
        who = re.search(r'<div class="x-au-name">(.*?)</div>', art, re.S)
        rev = re.search(r'<div class="r-cell r-txt x-au-body">(.*?)(?=<div class="r-cell|</article>)',
                        art, re.S)
        out["armor"][name] = {
            "slot": slot, "season": season,
            "perk_name": _text(xp.group(1)) if xp else "",
            "perk_paras": _paras(scope),
            "reviewer": _text(who.group(1)) if who else "",
            "review": "\n".join(_paras(rev.group(1))) if rev else "",
        }

    # 永劫教派：3 篇 <article class="rec r-plain r-c0">（力量/洞察/活力学派机制）
    for art in html.split('<article class="rec r-plain r-c0">')[1:]:
        nm = re.search(r'<div class="r-nm ">(.*?)</div>', art, re.S)
        if nm:
            out["schools"][_text(nm.group(1))] = _paras(art)

    # 职业金：18 行 <article class="sp-row rec">，每行左右两个之灵（= 特性列 Ⅰ / Ⅱ）
    for row in html.split('<article class="sp-row rec">')[1:]:
        name = None
        for chunk in re.split(r'(?=<div class="r-cell )', row):
            if chunk.startswith('<div class="r-cell r-id"'):
                m = re.search(r'<div class="r-nm exo">(.*?)</div>', chunk, re.S)
                name = _text(m.group(1)) if m else None
            elif chunk.startswith('<div class="r-cell r-txt"') and name:
                idx = 0 if len(out["spirit_cols"][0]) == len(out["spirit_cols"][1]) else 1
                out["spirit_cols"][idx].append(name)
                out["spirit_paras"][name] = _paras(chunk)
                name = None
    return out


# ---------------------------------------------------------------- manifest 加载

def load_manifest() -> tuple:
    c = httpx.Client(timeout=300, headers={"User-Agent": UA})
    print("加载本地物品定义 raw_items.json ...")
    items = json.load(open("manifest_index/raw_items.json", encoding="utf-8"))
    plugsets = json.load(open("manifest_index/raw_plugsets.json", encoding="utf-8"))
    try:
        stat_names = json.load(open("manifest_index/stats.json", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        stat_names = {}

    if os.path.exists("manifest_index/raw_items_en_lite.json"):
        lite = json.load(open("manifest_index/raw_items_en_lite.json", encoding="utf-8"))
    else:
        print("下载 EN Lite 物品定义（58MB，只取英文名）...")
        m = c.get("https://www.bungie.net/Platform/Destiny2/Manifest/", timeout=120).json()["Response"]
        p = m["jsonWorldComponentContentPaths"]["en"]["DestinyInventoryItemLiteDefinition"]
        lite = c.get("https://www.bungie.net" + p, timeout=600).json()
        json.dump(lite, open("manifest_index/raw_items_en_lite.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
    en_names = {h: (it.get("displayProperties") or {}).get("name", "")
                for h, it in lite.items() if isinstance(it, dict)}
    return items, plugsets, en_names, stat_names


# ---------------------------------------------------------------- 护甲构建辅助

def pick_exotic_perk(items, it: dict):
    """普通异域护甲的异域特性插件：intrinsics 类、有名字、非学派（学派是永劫的选择池）"""
    for se in (it.get("sockets") or {}).get("socketEntries") or []:
        p = items.get(str(se.get("singleInitialItemHash") or ""))
        if not p or not p.get("plug"):
            continue
        if (p["plug"].get("plugCategoryIdentifier") or "") != "intrinsics":
            continue
        nm = (p.get("displayProperties") or {}).get("name") or ""
        if nm and not nm.endswith("学派"):
            return nm, (p["displayProperties"].get("description") or ""), p
    return "", "", None


def pick_school_choices(items, it: dict, plugsets) -> list:
    """永劫护甲：学派选择池（reusable/randomized plugset 里全是 XX学派 的插槽）"""
    for se in (it.get("sockets") or {}).get("socketEntries") or []:
        k = str(se.get("reusablePlugSetHash") or se.get("randomizedPlugSetHash") or "")
        if not k:
            continue
        lst = (plugsets.get(k) or {}).get("reusablePlugItems", []) + \
              (plugsets.get(k) or {}).get("randomizedPlugItems", [])
        plugs = [items.get(str(x.get("plugItemHash"))) for x in lst]
        plugs = [p for p in plugs if p and ((p.get("displayProperties") or {}).get("name") or "").endswith("学派")]
        if plugs and len(plugs) == len(lst):
            return plugs
    return []


def armor_stats(items, plugsets, it: dict, stat_names) -> dict:
    """六维（EoF 新六维：武器/生命值/职业/手雷/超能/近战）。

    护甲 3.0 起六维是随机 roll：数值挂在无名的数值插件上，一列里同一 plugset 可能被
    两个插槽共用（各抽一条）。固定池取准确值，随机池取 [k×最低, k×最高] 区间。
    """
    fixed: dict = {}
    ranged: dict = {}   # plugsetHash -> [candidates, sockets 用到它的插槽数]
    for se in (it.get("sockets") or {}).get("socketEntries") or []:
        k = str(se.get("reusablePlugSetHash") or se.get("randomizedPlugSetHash") or "")
        init = se.get("singleInitialItemHash")
        cands = []
        if k:
            lst = (plugsets.get(k) or {}).get("reusablePlugItems", []) + \
                  (plugsets.get(k) or {}).get("randomizedPlugItems", [])
            cands = [items.get(str(x.get("plugItemHash"))) for x in lst]
        elif init:
            cands = [items.get(str(init))]
        cands = [p for p in cands if p]
        cands = [p for p in cands
                 if not ((p.get("displayProperties") or {}).get("name") or "")
                 and all(s.get("statTypeHash") in SIX_STATS for s in (p.get("investmentStats") or []))
                 and (p.get("investmentStats") or [])]
        if not cands:
            continue
        if k:
            g = ranged.setdefault(k, [{}, 0])
            g[1] += 1
            for p in cands:
                g[0][str(p.get("hash") or p["displayProperties"].get("icon"))] = p
        else:
            for p in cands:
                for s in p["investmentStats"]:
                    fixed[s["statTypeHash"]] = fixed.get(s["statTypeHash"], 0) + s["value"]
    out = {}
    for h, v in fixed.items():
        if v:
            out[stat_names.get(str(h), str(h))] = v
    for cands, k in ranged.values():
        for h in SIX_STATS:
            vals = [s["value"] for p in cands.values()
                    for s in (p.get("investmentStats") or []) if s["statTypeHash"] == h]
            if not vals:
                continue
            lo, hi = k * min(vals), k * max(vals)
            name = stat_names.get(str(h), str(h))
            if name in out:      # 固定 + 随机混挂时取并集
                lo = min(lo, out[name] if isinstance(out[name], int) else out[name][0])
                hi = max(hi, out[name] if isinstance(out[name], int) else out[name][1])
            out[name] = hi if lo == hi else [lo, hi]
    return out


# ---------------------------------------------------------------- 主流程

def main():
    items, plugsets, en_names, stat_names = load_manifest()
    ss = parse_starside(fetch_starside())
    base = "https://www.bungie.net"
    problems = []

    # --- ① Manifest 侧：异域护甲按名字分组合并（同件多 hash：旧版/2.0 重发，取插槽最全的）
    groups: dict = {}
    for h, it in items.items():
        inv = it.get("inventory") or {}
        if inv.get("tierType") != 6:
            continue
        slot = BUCKET_SLOT.get(inv.get("bucketTypeHash"))
        if not slot:
            continue
        nm = (it.get("displayProperties") or {}).get("name")
        if not nm:
            continue
        groups.setdefault(nm, []).append((int(h), it))
    chosen = {nm: max(lst, key=lambda t: (len((t[1].get("sockets") or {}).get("socketEntries") or []), t[0]))
              for nm, lst in groups.items()}
    print(f"Manifest 异域护甲：{sum(len(v) for v in groups.values())} 个 hash → {len(chosen)} 件")

    # --- ② 之灵池（职业金特性插件，intrinsics 类、名字以「之灵」结尾）
    spirit_by_name = {}
    for h, it in items.items():
        if (it.get("plug") or {}).get("plugCategoryIdentifier") != "intrinsics":
            continue
        nm = (it.get("displayProperties") or {}).get("name") or ""
        if nm.endswith("之灵"):
            spirit_by_name[nm] = (int(h), it)
    print(f"Manifest 之灵插件：{len(spirit_by_name)} 个")

    # --- ③ 职业金 perk_cols（starside 左右列 = 游戏特性列 Ⅰ/Ⅱ；文案用 starside 详版，图标/名字对回 Manifest）
    spirit_cols = [[], []]
    if ss["spirit_cols"][0]:
        cols = [[], []]
        for ci, names in enumerate(ss["spirit_cols"]):
            for nm in names:
                h, it = spirit_by_name.get(nm, (None, None))
                if h is None:
                    problems.append(f"之灵「{nm}」在 Manifest 中不存在")
                    continue
                dp = it["displayProperties"]
                cols[ci].append({
                    "hash": str(h), "name": nm,
                    "en": en_names.get(str(h), ""),
                    "desc": _strip_footer("\n".join(ss["spirit_paras"].get(nm) or [dp.get("description") or ""])),
                    "icon": base + dp["icon"] if dp.get("icon") else "",
                })
        spirit_cols = cols
        n1, n2 = len(cols[0]), len(cols[1])
        col_names = [{p["name"] for p in c} for c in cols]
        print(f"职业金特性池：列Ⅰ {n1} 个 / 列Ⅱ {n2} 个（324 种组合，组合可自由推导故不落盘）")
        if col_names[0] & col_names[1]:
            problems.append("特性列 Ⅰ/Ⅱ 出现重复之灵")
        if set(spirit_by_name) - (col_names[0] | col_names[1]):
            problems.append(f"之灵池未全覆盖：{set(spirit_by_name) - (col_names[0] | col_names[1])}")
    else:
        problems.append("starside 不可用：职业金只能按 Manifest 全池降级（两列无法区分）")
        cols = [[{"hash": str(h), "name": nm,
                  "en": en_names.get(str(h), ""),
                  "desc": it["displayProperties"].get("description") or "",
                  "icon": base + it["displayProperties"]["icon"] if it["displayProperties"].get("icon") else ""}
                 for nm, (h, it) in sorted(spirit_by_name.items())], []]
        spirit_cols = cols

    # 校验：职业金默认特性应分属两列
    for nm, (h, it) in chosen.items():
        if it.get("inventory", {}).get("bucketTypeHash") != 1585787867:
            continue
        ses = (it.get("sockets") or {}).get("socketEntries") or []
        for si, col in ((10, 0), (11, 1)):
            if si < len(ses):
                p = items.get(str(ses[si].get("singleInitialItemHash")))
                nm2 = (p["displayProperties"]["name"] if p else "")
                colnames = [x["name"] for x in spirit_cols[col]]
                if nm2 and nm2 not in colnames:
                    problems.append(f"{nm} 默认特性「{nm2}」不在列{'ⅠⅡ'[col]}")

    # --- ④ 逐件组装
    out_items = []
    n_ss = 0
    for nm, (h, it) in sorted(chosen.items()):
        dp = it["displayProperties"]
        slot = BUCKET_SLOT[it["inventory"]["bucketTypeHash"]]
        cls = CLASS_NAME[it.get("classType", 3)]
        perk_nm, perk_desc_manifest, perk_plug = pick_exotic_perk(items, it)
        en = en_names.get(str(h), "")
        src = "manifest"
        rec = {
            "hash": str(h), "name": nm, "en": en, "slot": slot, "class": cls,
            "icon": base + dp["icon"] if dp.get("icon") else "",
            "perk_name": perk_nm, "perk_desc": perk_desc_manifest,
            "flavor": it.get("flavorText") or "",
            "aliases": list(ALIASES.get(nm, [])),
            "source": src,
        }
        if slot == "职业":
            # pick_exotic_perk 找到的是「示例默认灵」（如巨龙之灵），并非固定特性；
            # 职业金的特性 = 两列随机组合，横条固定写组合说明，具体池在 perk_cols
            rec["perk_name"] = "双异域特性（随机组合）"
            rec["perk_desc"] = (f"两列异域特性池各随机取其一：列Ⅰ {len(spirit_cols[0])} 个、"
                                f"列Ⅱ {len(spirit_cols[1])} 个之灵，"
                                f"共 {len(spirit_cols[0]) * max(len(spirit_cols[1]), 1)} 种组合。")
            rec["perk_cols"] = spirit_cols
            rec["source"] = "manifest+starside" if ss["spirit_cols"][0] else "manifest"
        else:
            sa = ss["armor"].get(nm) or {}
            if sa:
                n_ss += 1
                # 槽位/职业交叉校验（starside 的分节即职业，槽位写在 r-sub）
                if sa.get("slot") and SLOT_ALIAS.get(sa["slot"], sa["slot"]) != slot:
                    problems.append(f"{nm}: starside 槽位「{sa['slot']}」≠ Manifest 槽位「{slot}」")
                rec["season"] = sa.get("season") or ""
                if sa.get("perk_name") and perk_nm and sa["perk_name"] != perk_nm:
                    problems.append(f"{nm}: 特性名 starside「{sa['perk_name']}」≠ Manifest「{perk_nm}」")
                if sa["perk_paras"]:
                    rec["perk_desc"] = _strip_footer("\n".join(sa["perk_paras"]))
                    if perk_desc_manifest and perk_desc_manifest != rec["perk_desc"]:
                        rec["perk_desc_game"] = perk_desc_manifest
                    rec["source"] = "manifest+starside"
                if sa.get("review"):
                    rec["review"] = _strip_footer(sa["review"])
                    rec["reviewer"] = sa.get("reviewer") or ""
            # 永劫教派：starside 用「学派」机制页描述（不在护甲列表里）
            schools = pick_school_choices(items, it, plugsets)
            if schools:
                rec["perk_name"] = "永劫学派（三选一）"
                rec["perk_desc"] = "装备时在三个学派中选择其一，为队伍提供不同方向的增益。"
                rec["perk_choices"] = [{
                    "name": (p["displayProperties"]["name"]),
                    "desc": _strip_footer("\n".join(ss["schools"].get(p["displayProperties"]["name"])
                                                    or [(p["displayProperties"].get("description") or "")])),
                    "icon": base + p["displayProperties"]["icon"] if p["displayProperties"].get("icon") else "",
                } for p in schools]
                rec["aliases"] = rec["aliases"] + (["永劫教派"] if "永劫教派" not in rec["aliases"] else [])
                rec["source"] = "manifest+starside" if ss["schools"] else "manifest"
        st = armor_stats(items, plugsets, it, stat_names)
        if st:
            rec["stats"] = st
        out_items.append(rec)

    print(f"starside 命中：{n_ss}/{len(chosen)} 件；学派文案 {len(ss['schools'])} 组")
    # 反向校验：starside 有而 Manifest 无名字的护甲条目
    orphan = sorted(set(ss["armor"]) - set(chosen))
    if orphan:
        problems.append(f"starside 有而 Manifest 无：{orphan}")

    # --- ⑤ 排序与落盘（猎人→泰坦→术士→通用，头胸臂腿职业）
    out_items.sort(key=lambda r: ({"猎人": 0, "泰坦": 1, "术士": 2}.get(r["class"], 3),
                                  {"头": 0, "胸": 1, "臂": 2, "腿": 3, "职业": 4}.get(r["slot"], 9),
                                  r["name"]))
    json.dump({"updated": date.today().isoformat(), "items": out_items},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"输出 {OUT}：{len(out_items)} 件")
    for p in problems:
        print("⚠", p)


if __name__ == "__main__":
    main()
