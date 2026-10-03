"""在 weapons.json 基础上构建 weapons_full.json（DIM 级详情）
需要网络下载 plugset/stat 定义（只跑一次，raw 物品定义会缓存）
"""
import json
import os
import re

import httpx

c = httpx.Client(timeout=180, headers={"X-API-Key": os.environ.get("BUNGIE_API_KEY", "")})
m = c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
paths = m["jsonWorldComponentContentPaths"]["zh-chs"]
base = "https://www.bungie.net"


def fetch(path, cache_name=None):
    if cache_name and os.path.exists(f"manifest_index/{cache_name}"):
        return json.load(open(f"manifest_index/{cache_name}", encoding="utf-8"))
    r = c.get(base + path)
    r.raise_for_status()
    data = json.loads(r.text)
    if cache_name:
        json.dump(data, open(f"manifest_index/{cache_name}", "w", encoding="utf-8"))
    return data


print("加载/缓存物品定义...")
items = fetch(paths["DestinyInventoryItemDefinition"], "raw_items.json")
print("下载 stat 定义...")
stats_def = fetch(paths["DestinyStatDefinition"])
stat_names = {h: s.get("displayProperties", {}).get("name", "") for h, s in stats_def.items()}
json.dump(stat_names, open("manifest_index/stats.json", "w", encoding="utf-8"), ensure_ascii=False)
print("下载 plugset 定义...")
plugsets = fetch(paths["DestinyPlugSetDefinition"], "raw_plugsets.json")
plugset_map = {h: ps.get("reusablePlugItems", []) + ps.get("randomizedPlugItems", [])
               for h, ps in plugsets.items()}

WEAPON_CATS = {1, 2, 3}
# 催化剂的「真实效果」不在催化物品自己的描述里（那只是通用的升级说明），而是挂在它
# perks[].perkHash 指向的 sandbox perk 上，所以这里读 perks.json 做一次解析。
try:
    PERKS = json.load(open("manifest_index/perks.json", encoding="utf-8"))
except Exception:  # noqa: BLE001
    PERKS = {}


def catalyst_effects(h) -> list:
    o = items.get(str(h))
    if not o:
        return []
    out = []
    for p in (o.get("perks") or []):
        rec = PERKS.get(str(p.get("perkHash") or ""))
        if rec and rec.get("desc"):
            out.append({"n": rec.get("name") or "", "d": rec["desc"]})
    return out


# 催化数值/效果的补充来源：
#   ① Starside 异域武器页（中文，含「+20 操控性、+1 弹匣容量（5 → 6）」这类精确写法）
#   ② Clarity 社区数据（英文，Manifest 里没数值的催化只有它有，如「Grants 30 Reload」）
# 两者都是「按 hash / 按武器」查表，构建时一次性载入。
try:
    EXOTIC_ZH = json.load(open("manifest_index/exotic_catalysts_zh.json", encoding="utf-8"))
except Exception:  # noqa: BLE001
    EXOTIC_ZH = {}
try:
    _COMMUNITY = json.load(open("manifest_index/community_clarity.json", encoding="utf-8"))
except Exception:  # noqa: BLE001
    _COMMUNITY = {}


def _flat_desc(e: dict) -> str:
    """社区数据的多段说明 → 一行行纯文本"""
    lines = []
    for blk in (e.get("descriptions") or {}).get("en", []):
        txt = "".join(x.get("text") or "" for x in blk.get("linesContent", [])).strip()
        txt = re.sub(r"\s+", " ", txt)
        if txt:
            lines.append(txt)
    return "\n".join(lines)


COMMUNITY_CAT: dict = {}        # 催化物品 hash → 说明
COMMUNITY_BY_ITEM: dict = {}    # 武器 hash → 说明（部分金枪的催化插件与社区记录的 hash 不同版本）
for _h, _e in _COMMUNITY.items():
    if _e.get("type") != "Weapon Catalyst Exotic":
        continue
    _t = _flat_desc(_e)
    if not _t:
        continue
    COMMUNITY_CAT[str(_h)] = _t
    if _e.get("itemHash"):
        COMMUNITY_BY_ITEM.setdefault(str(_e["itemHash"]), _t)


# 插件是否是「催化」：真催化的类别就叫 catalysts，也有直接叫 XX催化 的
def _is_cat_plug(ph) -> bool:
    p = items.get(str(ph))
    if not p:
        return False
    cat = ((p.get("plug") or {}).get("plugCategoryIdentifier") or "").lower()
    nm = (p.get("displayProperties") or {}).get("name") or ""
    if any(x in cat for x in ("tracker", "empty", "skins", "shader", "ornament")):
        return False
    return "catalyst" in cat or nm.endswith("催化") or nm.endswith("催化剂")


# 该插槽是不是「催化插槽」：初始插件是空催化插槽（v400.empty.exotic.masterwork），
# 或者里面列出的插件里有催化
def _is_cat_socket(se: dict) -> bool:
    ph = se.get("singleInitialItemHash")
    p = items.get(str(ph)) if ph else None
    if p:
        cat = ((p.get("plug") or {}).get("plugCategoryIdentifier") or "").lower()
        nm = (p.get("displayProperties") or {}).get("name") or ""
        if "exotic" in cat and "masterwork" in cat:
            return True
        if "catalyst" in cat or nm.endswith("催化") or nm.endswith("催化剂"):
            return True
    return any(_is_cat_plug(x) for x in _socket_plugs(se))


def _socket_plugs(se: dict) -> list:
    """插槽里可能出现的所有插件 hash：初始插件 + 候选 + plugset 展开"""
    out = []
    if se.get("singleInitialItemHash"):
        out.append(se["singleInitialItemHash"])
    out += [x.get("plugItemHash") for x in (se.get("reusablePlugItems") or [])]
    for k in ("reusablePlugSetHash", "randomizedPlugSetHash"):
        if se.get(k):
            out += [x.get("plugItemHash") for x in plugset_map.get(str(se[k]), [])]
    return [x for x in out if x]


def catalyst_hashes(it: dict) -> list:
    """武器自己的催化插件。名字匹配靠不住：中文名常对不上（「低语催化」≠「蠕虫低语催化」），
    有的干脆是占位符插件（真相 / 泰拉巴），所以以插槽结构为准。"""
    out = []
    for se in it.get("sockets", {}).get("socketEntries", []):
        if not _is_cat_socket(se):
            continue
        for ph in _socket_plugs(se):
            p = items.get(str(ph))
            if not p:
                continue
            cat = ((p.get("plug") or {}).get("plugCategoryIdentifier") or "").lower()
            if any(x in cat for x in ("tracker", "empty", "skins", "transfusers")):
                continue
            if str(ph) not in out:
                out.append(str(ph))
    return out

STAT_ORDER = ["弹夹容量", "爆炸范围", "充能速度", "稳定性", "操控性", "填装速度",
              "射击速度", "空中效率", "冲击", "射程", "精准度", "挥砍速度",
              "弹匣", "后坐方向", "护盾穿透", "举盾速度", "充能时间", "蓄能时间"]

# 枪械「部件」插槽 → 游戏里的列名
_PART_LABELS = (("tubes", "枪管 / 发射"), ("barrels", "枪管 / 发射"), ("scopes", "瞄具"),
                ("magazines", "弹匣 / 电池"), ("batteries", "电池"),
                ("rails", "导轨"), ("stocks", "枪托"))


def _PART_LABEL(low: str) -> str:
    for key, label in _PART_LABELS:
        if key in low:
            return label
    return "配件"

def _merge_catalysts(infos: list, weapon: str) -> list:
    """同一把武器的多个催化条目去重。

    同一份催化常被两个 hash 描述（原版 / 重制版各挂一个插件），效果文案一样、只是
    名字不同（「焚天者催化」vs「焚天者誓约催化」），并排显示像两条催化。
    真·多催化（引力子尖刺那类「XX改装」四选一）文案各不相同，不会被并掉。
    """
    def rank(x: dict) -> tuple:
        # 有数值 > 名字带武器名（「全面爆发催化」比插件本名「病媒」好认）> 有图标
        return (len(x.get("stats") or {}), weapon in (x.get("n") or ""), bool(x.get("i")))

    def merge(dst: dict, src: dict) -> dict:
        """把 src 里 dst 缺的字段补上（同名催化的两个 hash 常各带一半数据）"""
        for k in ("stats", "fx", "zh", "ci", "i", "d"):
            if k == "stats":
                if isinstance(src.get(k), dict) and isinstance(dst.get(k), dict):
                    dst[k] = {**src[k], **dst[k]}
                elif not dst.get(k) and src.get(k):
                    dst[k] = src[k]
            elif not dst.get(k) and src.get(k):
                dst[k] = src[k]
        return dst

    out: list = []
    by_key: dict = {}
    for info in infos:
        key = info.get("ci") or (info.get("n"), tuple(sorted((info.get("stats") or {}).items())))
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = info
            out.append(info)
        elif rank(info) > rank(prev):
            out[out.index(prev)] = merge(info, prev)
            by_key[key] = info
        else:
            merge(prev, info)
    return out


full = {}
# 异域催化剂索引：物品类别59，名字 = 武器名 + 催化(剂)
catalyst_by_name = {}
for h2, it2 in items.items():
    if 59 not in (it2.get("itemCategoryHashes") or []):
        continue
    n2 = it2.get("displayProperties", {}).get("name", "")
    if n2.endswith("催化") or n2.endswith("催化剂"):
        catalyst_by_name[n2] = h2

for h, it in items.items():
    cats = it.get("itemCategoryHashes", [])
    if not any(x in cats for x in WEAPON_CATS) or it.get("itemType") in (20, 21, 22):
        continue
    dp = it.get("displayProperties", {})
    if not dp.get("name"):
        continue

    # 属性条
    stats = []
    for s in it.get("stats", {}).get("stats", {}).values():
        sid = s.get("statHash")
        name = stat_names.get(str(sid) if str(sid) in stat_names else sid, "")
        val = s.get("value", 0)
        if name and val > 0:
            stats.append({"n": name, "v": val})
    # 保序：常见属性优先，其他按原序
    stats.sort(key=lambda x: (STAT_ORDER.index(x["n"]) if x["n"] in STAT_ORDER else 99,))

    # 插件分类：特性/枪管/弹匣/模组/大师/异域公约
    def plug_info(ph):
        key = str(ph)
        p = items.get(key)
        if not p:
            return None
        pdp = p.get("displayProperties", {})
        name = pdp.get("name", "")
        cat = p.get("plug", {}).get("plugCategoryIdentifier", "").lower()
        # 过滤：着色器/皮肤/战斗特效/空插槽/装饰/装备阶级升级/制作阶位插件
        if any(x in cat for x in ("shader", "skins", "ornament", "kill_vfx",
                                  "empty", "transfusers", "enhancers", "tiering")):
            return None
        import re as _re
        if _re.match(r"^\d+阶", name):  # 大师杰作 1-9 阶过渡插件
            return None
        if not name:
            return None
        stats = {}
        for s in p.get("investmentStats", []):
            n = stat_names.get(str(s.get("statTypeHash")), "")
            if n and s.get("value"):
                stats[n] = s["value"]
        return {
            "hash": key, "n": pdp["name"],
            "d": pdp.get("description", " ").replace("\n", " ")[:300],
            "i": base + pdp["icon"] if pdp.get("icon") else "",
            "cat": p.get("plug", {}).get("plugCategoryIdentifier", ""),
            "stats": stats,
        }

    buckets = {"trait_cols": [], "barrels": [], "magazines": [], "intrinsic": [],
               "origins": [], "masterworks": [], "mods": [], "stocks": [],
               "catalysts": [], "fixed": []}
    # cols：按 socketEntries 原始顺序排好的列，渲染直接用这个。
    # 旧写法把每个插件按类别丢进各自的桶，桶之间没有先后关系，于是输出时只能硬编码
    # 「枪管→弹匣→特性→起源→枪托」——剑类（握把在特性之前）、异域（固定配件在特性之前）
    # 就会错位，这是「Perk 乱序 / 剑出现枪托列」的根因。
    cols = []
    seen = set()
    is_exotic = it.get("inventory", {}).get("tierType") == 6
    trait_no = 0
    fixed_col = None
    for se in it.get("sockets", {}).get("socketEntries", []):
        hashes = []
        if se.get("singleInitialItemHash"):
            hashes = [se["singleInitialItemHash"]]
        for ps in ("reusablePlugSetHash", "randomizedPlugSetHash"):
            if se.get(ps):
                k = str(se[ps]) if str(se[ps]) in plugset_map else se[ps]
                hashes += [p.get("plugItemHash") for p in plugset_map.get(k, [])]
        sock, label = [], ""
        for ph in hashes:
            if str(ph) in seen:
                continue
            seen.add(str(ph))
            info = plug_info(ph)
            if not info:
                continue
            low = info.pop("cat").lower()
            # 真催化的 plugCategoryIdentifier 就叫 catalysts；masterworks 下面还有一批
            # 非 stat 的插件（先锋/熔炉大师杰作、重铸武器…），旧写法把它们也算进催化，
            # 会让「异域催化」区块冒出一堆跟催化剂无关的东西。
            if "catalyst" in low:
                buckets["catalysts"].append(info)
            elif "masterwork" in low or "trackers" in low:
                buckets["masterworks"].append(info)
            elif "intrinsics" in low:
                buckets["intrinsic"].append(info)
            # 枪械「部件」类插槽：异域武器上这些是固定不可选的，合成一列「固定配件」
            elif ("tubes" in low or "barrels" in low or "scopes" in low
                  or "magazines" in low or "batteries" in low or "rails" in low
                  or "stocks" in low):
                label = "固定配件" if is_exotic else _PART_LABEL(low)
                sock.append(info)
            # 其余是真正可选的插槽，各自成列（用游戏里的叫法）
            elif "blades" in low:
                label = "剑刃"
                sock.append(info)
            elif "guards" in low:
                label = "护手"
                sock.append(info)
            elif "bowstrings" in low:
                label = "弓弦"
                sock.append(info)
            elif "arrows" in low:
                label = "箭矢"
                sock.append(info)
            elif "hafts" in low:
                label = "弓柄"
                sock.append(info)
            elif "grips" in low:
                label = "握把"
                sock.append(info)
            elif "bolts" in low:
                label = "弩箭"
                sock.append(info)
            elif "forms" in low:
                label = "形态"
                sock.append(info)
            elif "perk_upgrades" in low:
                label = "核心强化"
                sock.append(info)
            elif "origins" in low:
                label = "起源特性"
                sock.append(info)
            elif "frames" in low:
                # 异域武器的框架插槽可选（如故我在的波形/涡流/铸造者框架），
                # 按游戏内叫法单独立列，别混进「特性 N」
                label = "框架" if is_exotic else ""
                sock.append(info)
            elif "traits" in low:
                sock.append(info)
            else:
                buckets["mods"].append(info)
        if not sock:
            continue
        if label == "固定配件":
            # 异域的枪管/弹匣/枪托都不可选，合成一列，位置取它第一次出现的地方
            if fixed_col is None:
                fixed_col = {"t": label, "items": []}
                cols.append(fixed_col)
            fixed_col["items"] += sock
        else:
            if not label:
                trait_no += 1
                label = f"特性 {trait_no}"
            cols.append({"t": label, "items": sock})

    # 金枪：催化。优先按武器自己的催化插槽定位（可靠、与语言无关），找不到再按
    # 「同名 XX催化」兜底。数值加成来自催化插件的 investmentStats；效果优先取
    # 中文（Sandbox perk 说明 / Starside），再退回社区英文原文（那里常有精确数值，
    # 如「Grants 30 Reload and 20 Stability」，而 Manifest 里这些字段是空的）。
    if is_exotic:
        cat_hashes = catalyst_hashes(it)
        if not cat_hashes:
            for cn, ch in catalyst_by_name.items():
                if re.fullmatch(re.escape(dp["name"]) + r"催化(剂)?", cn):
                    cat_hashes = [ch]
                    break
        infos = []
        for ch in cat_hashes:
            cinfo = plug_info(ch)
            if not cinfo:
                continue
            cinfo.pop("cat", None)
            fx = catalyst_effects(ch)
            name = cinfo["n"] or ""
            generic = name.startswith("[PLACEHOLDER]") or name in ("升级大师杰作", "大师杰作升级")
            perk_named = (not any(k in name for k in ("催化", "改装"))
                          and fx and name == (fx[0].get("n") or ""))
            if generic or perk_named:
                # 真相 / 泰拉巴 / 重制过的低语：插件名是占位符串或通用的「升级大师杰作」；
                # 另有几把金枪的催化物品直接拿「它给的那个 Perk」当名字（全面爆发=病媒、
                # 亡者传说=暗铸扳机、蠕虫低语=轻声呼吸…），列在「异域催化」里认不出是谁的
                cinfo["n"] = dp["name"] + "催化"
                cinfo["d"] = ""
            if fx:
                cinfo["fx"] = fx
            zh = EXOTIC_ZH.get(dp["name"])
            if zh:
                cinfo["zh"] = zh
            ci = COMMUNITY_CAT.get(str(ch)) or COMMUNITY_BY_ITEM.get(str(h))
            if ci:
                cinfo["ci"] = ci
            infos.append(cinfo)
        infos = _merge_catalysts(infos, dp["name"])
        if infos:
            buckets["catalysts"] = infos

    # 塑形异域的「催化插槽」（空催化插槽 + N 个 XX改装，游戏内可选）：这些插件的类目
    # 带 catalysts 被收进了催化桶，但它们同时是真正的可选 perk 列（单人合唱等），
    # 不补列的话 perk 区少一列、社区组合也只能配到固定特性上
    if is_exotic and not any(c.get("t") == "催化" for c in cols):
        for se in it.get("sockets", {}).get("socketEntries", []):
            seth = se.get("reusablePlugSetHash") or se.get("randomizedPlugSetHash")
            ps = plugset_map.get(str(seth) if str(seth) in plugset_map else seth) or []
            hashes = [p.get("plugItemHash") for p in ps]
            names = [(plug_info(ph) or {}).get("n") or "" for ph in hashes]
            if not any(n == "空催化插槽" for n in names):
                continue
            cat_items = []
            for ph in hashes:
                info = plug_info(ph)
                if not info or (info.get("n") or "").startswith("空"):
                    continue
                info.pop("cat", None)
                cat_items.append(info)
            if len(cat_items) < 2:
                continue
            col = {"t": "催化", "items": cat_items}
            idx = next((i for i, c in enumerate(cols) if c.get("t") == "枪托"), len(cols))
            cols.insert(idx, col)
            break

    # 去重：列内按名字去重，内容完全相同的列只留第一列
    def dedupe(lst):
        out2, s2 = [], set()
        for x in lst:
            if x["n"] in s2:
                continue
            s2.add(x["n"]); out2.append(x)
        return out2

    seen_cols, cols2 = set(), []
    for c in cols:
        c["items"] = dedupe(c["items"])
        if not c["items"]:
            continue
        key = ",".join(x["n"] for x in c["items"])
        if key in seen_cols:
            continue
        seen_cols.add(key); cols2.append(c)
    cols = cols2

    # 扁平桶由有序列反推，两套数据永远一致（webui 仍按这些键取数据）
    buckets["trait_cols"] = [c["items"] for c in cols if c["t"].startswith("特性")]
    for c in cols:
        t = c["t"]
        if t.startswith("枪管") or t == "瞄具":
            buckets["barrels"] = c["items"]
        elif t.startswith("弹匣") or t == "电池":
            buckets["magazines"] = c["items"]
        elif t == "枪托":
            buckets["stocks"] = c["items"]
        elif t == "起源特性":
            buckets["origins"] = c["items"]
        elif t == "固定配件":
            buckets["fixed"] = c["items"]
    for bk, lst in buckets.items():
        if bk != "trait_cols":
            buckets[bk] = dedupe(lst)
    buckets["cols"] = cols

    flavor = it.get("flavorText", "")
    full[h] = {
        "name": dp["name"],
        "type": it.get("itemTypeDisplayName", ""),
        "ammo": {1: "主武器", 2: "特殊", 3: "重武器"}.get(it.get("equippingBlock", {}).get("ammoType", 0), ""),
        "cat": next(("动能" if x == 1 else "能量" if x == 2 else "威力" for x in cats if x in WEAPON_CATS), ""),
        "icon": base + dp.get("icon", "") if dp.get("icon") else "",
        "watermark": base + it["iconWatermark"] if it.get("iconWatermark") else "",
        "screenshot": base + ("/" + it["screenshot"].lstrip("/")) if it.get("screenshot") else "",
        "flavor": flavor,
        "desc": dp.get("description", "")[:300],
        "stats": stats,
        "plugs": buckets,
    }

json.dump(full, open("manifest_index/weapons_full.json", "w", encoding="utf-8"), ensure_ascii=False)
print("武器详情条目:", len(full))
