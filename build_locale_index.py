"""生成英文 / 繁体中文查询索引 —— manifest_index/name_i18n.json + item_cht.json

背景：weapons_full.json / perks.json / activities.json / armor_sets.json / item_zh.json
全部出自 zh-chs manifest，玩家用英文名（Fatebringer / Hand Cannon / Crota's End）或
台服繁体名（手砲 / 脈衝步槍 / 國王的殞落）来查一律空手而归。这里把同一个 hash 上的
zh-chs / zh-cht / en 三个名字对齐，产出紧凑反查索引：运行时按名字直接命中 hash。

**不做繁简字形转换**：台服叫法常是词形差异（克洛塔/克羅塔、突袭/掠奪），字形转换对不上，
只有 Bungie 官方 zh-cht 名字才靠得住，所以直接采官方三语名做索引。

产出（进 exe 包，紧凑 JSON）：
  name_i18n.json
    wname      {武器hash: [英文名, 繁体名]}    # 卡片展示 + 前缀命中判定
    weapons    {归一化名: [武器hash...]}       # 三语武器名（限 weapons_full 里的）
    perks      {归一化名: [perk hash...]}      # 三语 perk 名（限 perks.json 里的）
    activities {归一化名: [活动ref...]}        # 三语活动名（限 activities.json 里的）
    terms      {归一化词: 简体词}              # 类型/框架/元素/弹药 等词表，/武器筛选 用
    sets       {归一化名: 简体套装名}           # 护甲套装英文/繁体名 → 简体名
    charts     {归一化名: 掉落表 key}           # 副本英文/繁体名 → Saya 图表 key
  item_cht.json  {繁体名小写: [hash...]}       # /仓库 搜索用（与 item_en.json 同构）

中间产物（**不进包**，D2Query.spec 的 _MI_SKIP 里登记）：
  raw_items_cht_lite.json / raw_perks_{cht,en}.json / raw_acts_{cht,en}.json /
  raw_sets_{cht,en}.json / raw_dmg_{chs,cht,en}.json

用法: .venv/Scripts/python.exe build_locale_index.py     （manifest 更新后重跑）
"""
import json
import os
import re
import unicodedata

import httpx

ROOT = os.path.dirname(os.path.abspath(__file__))
MI = os.path.join(ROOT, "manifest_index")


def norm(s: str) -> str:
    """与 destiny_data.norm_key 同规则：NFKC → 小写 → 剔除所有非文字字符"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^\w]+", "", s)


def api_key() -> str:
    if os.getenv("BUNGIE_API_KEY"):
        return os.environ["BUNGIE_API_KEY"]
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        if line.startswith("BUNGIE_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("缺 BUNGIE_API_KEY（.env）")


C = httpx.Client(timeout=300, headers={"X-API-Key": api_key()}, follow_redirects=True)
PATHS = C.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"][
    "jsonWorldComponentContentPaths"]
BASE = "https://www.bungie.net"


def fetch(locale: str, comp: str, cache: str):
    """下载 manifest 组件（带本地缓存，重跑不再下载）"""
    p = os.path.join(MI, cache)
    if os.path.exists(p):
        print(f"  缓存 {cache}")
        return json.load(open(p, encoding="utf-8"))
    print(f"  下载 {locale}/{comp} …")
    r = C.get(BASE + PATHS[locale][comp])
    r.raise_for_status()
    data = json.loads(r.text)
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  已存 {cache}（{os.path.getsize(p) / 1e6:.1f}MB）")
    return data


def dp_name(rec: dict) -> str:
    return ((rec or {}).get("displayProperties") or {}).get("name", "") or ""


def itd(rec: dict) -> str:
    return (rec or {}).get("itemTypeDisplayName", "") or ""


def _base_name(name: str) -> str:
    """活动名的基础名：去掉「: 标准 / ：大师」这类难度后缀（manifest 里副本活动都带）"""
    return re.split(r"[:：]", str(name or ""))[0].strip()


def add(idx: dict, name: str, key) -> None:
    """归一化名字 → key 倒排（同 hash 的多语名共用一个桶，空名/占位名跳过）"""
    n = norm(name)
    if not n or name.strip() in {"？", "?"}:
        return
    bucket = idx.setdefault(n, [])
    if key not in bucket:
        bucket.append(key)


def main() -> int:
    print("加载本地索引 …")
    zh_items = json.load(open(os.path.join(MI, "item_zh.json"), encoding="utf-8"))
    wf = json.load(open(os.path.join(MI, "weapons_full.json"), encoding="utf-8"))
    perks_zh = json.load(open(os.path.join(MI, "perks.json"), encoding="utf-8"))
    acts_zh = json.load(open(os.path.join(MI, "activities.json"), encoding="utf-8"))
    armor_sets = json.load(open(os.path.join(MI, "armor_sets.json"), encoding="utf-8"))
    # 锻造来源词表（= 筛选索引 g 字段的取值）直接读分组表，不依赖 weapon_filter_index.json，
    # 这样构建顺序能保持线性：本脚本 → build_weapon_filter_index.py（它反过来读名字）
    groups = json.load(open(os.path.join(MI, "pattern_groups.json"), encoding="utf-8"))["groups"]
    en_items = json.load(open(os.path.join(MI, "raw_items_en_lite.json"), encoding="utf-8"))

    print("取 manifest 三语定义 …")
    cht_items = fetch("zh-cht", "DestinyInventoryItemLiteDefinition", "raw_items_cht_lite.json")
    perks_cht = fetch("zh-cht", "DestinySandboxPerkDefinition", "raw_perks_cht.json")
    perks_en = fetch("en", "DestinySandboxPerkDefinition", "raw_perks_en.json")
    acts_cht = fetch("zh-cht", "DestinyActivityDefinition", "raw_acts_cht.json")
    acts_en = fetch("en", "DestinyActivityDefinition", "raw_acts_en.json")
    sets_chs = fetch("zh-chs", "DestinyEquipableItemSetDefinition", "raw_sets_chs.json")
    sets_cht = fetch("zh-cht", "DestinyEquipableItemSetDefinition", "raw_sets_cht.json")
    sets_en = fetch("en", "DestinyEquipableItemSetDefinition", "raw_sets_en.json")
    dmg = {loc: fetch(loc, "DestinyDamageTypeDefinition", f"raw_dmg_{loc}.json")
           for loc in ("zh-chs", "zh-cht", "en")}

    wi, pi, ai = {}, {}, {}          # 武器 / perk / 活动 三语名倒排
    wname: dict[str, list[str]] = {}  # 武器 hash → [英文名, 繁体名]

    # ---------- 武器 ----------
    for h, w in wf.items():
        en, cht = dp_name(en_items.get(h)), dp_name(cht_items.get(h))
        wname[h] = [en, cht]
        for nm in (w.get("name"), en, cht):
            add(wi, nm, h)

    # ---------- perk（SandboxPerk 是独立 hash 空间，不在物品表里） ----------
    for h, p in perks_zh.items():
        en = dp_name(perks_en.get(h))
        cht = dp_name(perks_cht.get(h))
        for nm in (p.get("name"), en, cht):
            add(pi, nm, h)

    # ---------- 活动 ----------
    for ref, a in acts_zh.items():
        en = dp_name(acts_en.get(ref))
        cht = dp_name(acts_cht.get(ref))
        for nm in (a.get("name"), en, cht):
            add(ai, nm, ref)

    # ---------- 词表 terms：/武器筛选 与图鉴要认的外来词 ----------
    # 只收「筛选索引真正会出现的词」，且按 hash 对齐同一条目，避免物品名/类型名互相污染
    # （早先按名字全量对齐出过「脈衝步槍 → 枪匠命令」这种错配）。
    terms: dict[str, str] = {}

    def pair(terms_map: dict, key_zh: str, *foreign: str) -> None:
        """同一个 hash 上的外语名 → 简体名（简体本身不写进表，避免和 SYNONYM 抢词）"""
        if not key_zh:
            return
        for nm in foreign:
            if nm and norm(nm) != norm(key_zh):
                terms_map[norm(nm)] = key_zh.strip()

    # 1) 武器类型名：Hand Cannon / 手持加農砲 → 手炮
    for h in wf:
        chs_type = (zh_items.get(h) or ["", "", ""])[2]
        pair(terms, chs_type, itd(en_items.get(h)), itd(cht_items.get(h)))

    # 2) 固有框架与特性插件（f / p 字段的词）：只取 weapons_full 真正用到的 plug hash
    plug_h: set[str] = set()
    for w in wf.values():
        p = w.get("plugs") or {}
        for k in ("intrinsic", "origins", "barrels", "magazines", "stocks",
                  "catalysts", "fixed", "masterworks", "mods"):
            for x in p.get(k) or []:
                if x.get("hash"):
                    plug_h.add(str(x["hash"]))
        for c in p.get("cols") or []:
            for x in c.get("items") or []:
                if x.get("hash"):
                    plug_h.add(str(x["hash"]))
    for ph in plug_h:
        chs = (zh_items.get(ph) or [""])[0]
        pair(terms, chs, dp_name(en_items.get(ph)), dp_name(cht_items.get(ph)))

    # 3) 锻造来源（g 字段）里的副本名：只在分组表出现过的活动名才收
    gvals = set(groups)
    for ref, a in acts_zh.items():
        base = _base_name(a.get("name"))
        if base and base in gvals:
            pair(terms, base, _base_name(dp_name(acts_en.get(ref))),
                 _base_name(dp_name(acts_cht.get(ref))))

    # 4) 元素：manifest 里叫「烈日伤害 / 灼燒傷害」，词表用筛选索引的写法
    ELEM = {"动能", "电弧", "烈日", "虚空", "冰影", "缚丝"}
    ELEM_H = {h: w for h, d in dmg["zh-chs"].items()
              for w in [re.sub(r"(伤害|傷害)$", "", dp_name(d))] if w in ELEM}
    for h, zh_word in ELEM_H.items():
        pair(terms, zh_word, dp_name(dmg["en"].get(h)), dp_name(dmg["zh-cht"].get(h)))

    # 5) 弹药 / 槽位 / 布尔词（不来自 manifest，两岸与社区叫法都收）
    terms.update({
        "primary": "主武器", "primaryammo": "主武器", "primaryweapon": "主武器",
        "主要武器": "主武器", "主要彈藥": "主武器", "主要弹药": "主武器",
        "special": "特殊", "specialammo": "特殊", "specialweapon": "特殊",
        "特殊彈藥": "特殊", "特殊武器": "特殊",
        "heavy": "重武器", "heavyammo": "重武器", "heavyweapon": "重武器", "powerweapon": "重武器",
        "重型武器": "重武器", "重型彈藥": "重武器",
        "kinetic": "动能", "kineticslot": "动能", "kineticweapon": "动能",
        "energy": "能量", "energyslot": "能量", "energyweapon": "能量",
        "power": "威能", "powerslot": "威能",
        # 筛选布尔词（SPECIAL 表的中文写法）
        "craft": "锻造", "crafted": "锻造", "craftable": "锻造", "pattern": "锻造",
        "patterns": "锻造", "deepsight": "锻造", "redborder": "锻造",
        "exotic": "异域", "exotics": "异域", "金槍": "异域", "異域": "异域",
    })

    # ---------- 护甲套装：EquipableItemSet 三语名对齐到 armor_sets.json 的简体名 ----------
    set_names = {str(s.get("name") or "").strip() for s in armor_sets}
    sets_map: dict[str, str] = {}
    miss: list[str] = []
    for h, sd in sets_chs.items():
        nm = dp_name(sd).strip()
        if not nm:
            continue
        if nm in set_names:
            for src in (sets_en.get(h), sets_cht.get(h)):
                s = dp_name(src)
                if s and norm(s) != norm(nm):
                    sets_map[norm(s)] = nm
        elif nm not in set_names and norm(nm) not in {norm(x) for x in set_names}:
            miss.append(nm)
    matched = len(set_names) - len(miss)
    print(f"  护甲套装：armor_sets {len(set_names)} 套，对上 manifest 套装名 {matched} 套")

    # ---------- 掉落表：Saya 图表显示名 → 三语活动名 ----------
    # 活动名在 manifest 里带难度后缀（「克洛塔的末日: 标准」），社区图只写副本名，
    # 所以按「去后缀的基础名」对齐；再不行按包含关系；最后一张按英文名兜底
    # （玻璃穹顶/Vault of Glass、守护者尖塔/Spire of the Watcher 这类两岸译名不同的）。
    import raid_loot
    act_base: dict[str, list[str]] = {}
    for ref, a in acts_zh.items():
        act_base.setdefault(_base_name(a.get("name")), []).append(ref)
    CHART_EN = {"vog": "vault of glass", "spire": "spire of the watcher",
                "salvation_edge": "salvation's edge"}
    charts: dict[str, str] = {}
    chart_miss: list[str] = []
    for key, (disp, _aliases, _src) in raid_loot.CHARTS.items():
        bases = [_base_name(disp), _base_name(re.sub(r"[（(].*?[)）]", "", disp))]
        ref = next((r for b in bases for r in act_base.get(b, []) if b), None)
        if ref is None:  # 包含关系（两岸译名差一两个字时兜底）
            ref = next((r for b in bases if b
                        for ab, refs in act_base.items()
                        if ab and (b in ab or ab in b) for r in refs[:1]), None)
        if ref is None and key in CHART_EN:  # 英文名定位（中文译名差太多）
            want = CHART_EN[key]
            ref = next((r for r, a in acts_en.items()
                        if _base_name(dp_name(a)).lower() == want), None)
        if ref is None:
            chart_miss.append(disp)
            continue
        for src in (acts_en.get(ref), acts_cht.get(ref)):
            s = _base_name(dp_name(src))
            if s and norm(s) != norm(bases[0]):
                charts[norm(s)] = key
    print(f"  掉落表：{len(raid_loot.CHARTS)} 张图，对上活动定义 "
          f"{len(raid_loot.CHARTS) - len(chart_miss)} 张"
          + (f"，未对上 {chart_miss}" if chart_miss else ""))

    out = {"ver": 1, "written": "build_locale_index.py",
           "wname": wname, "weapons": wi, "perks": pi, "activities": ai,
           "terms": terms, "sets": sets_map, "charts": charts}
    json.dump(out, open(os.path.join(MI, "name_i18n.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"name_i18n.json：武器 {len(wi)} 键 / perk {len(pi)} / 活动 {len(ai)} / "
          f"词表 {len(terms)} / 套装 {len(sets_map)} / 掉落 {len(charts)}")

    cht: dict[str, list] = {}
    for h, v in cht_items.items():
        n = dp_name(v).strip().lower()
        if n and n not in ("？", "?"):
            cht.setdefault(n, []).append(h)
    json.dump(cht, open(os.path.join(MI, "item_cht.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"item_cht.json：{len(cht)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
