"""「/队伍配装」数据采集：当前队伍各成员已装备的 超能 / 武器 / 护甲 / 模组

数据源全官方，免授权（走对方库存隐私设置）：
- 名单 = 复用 bot_fireteam.collect 的队伍发现（对局里→本场 PGCR 名单，轨道/在线→
  transitory 实时队伍），它在返回里带 roster: [{mid, mtype, name}]。
- 每人一次 GetProfile components=200,205,300,305：
  200 角色（徽标横幅/职业/光等/六维）+ 205 已装备栏 + 300 实例（护甲光等）
  + 305 插槽实盘插值（perk/模组当前卷）。
- 对方隐私把「库存」设为私有时 205/305 直接缺 → 该成员出「装备不可见」占位行，
  不拖垮整卡。
命名/图标全走本地索引（item_zh.json / plug_meta.json，build_item_index.py 产出，
raw_items.json 220MB 不进 exe 包），除名单发现外每人只发 1 个 API 请求。

插件分类（zh manifest 实测 2026-10，见 _classify）：
- 武器芯片 = itemTypeDisplayName ∈ {固有, 枪管, 弹匣, 特性, 原始特性}
  （自动滤掉 着色器/外观/击杀记录器/塑形/各种空插槽）
- 护甲芯片 = itemTypeDisplayName 以「护甲模组」结尾 或 调谐模组（+属性/-属性）
- 子职业 = 超能技能 → 超能；「星相」→ 分支；「碎片」→ 碎片；其余 → 职业技能
"""
from __future__ import annotations

import asyncio
import json

import destiny_data as d2
import bungie_auth
from bot_fireteam import (_MAX_MEMBERS, _profile_ex, _retry_twice, _row_budget,
                          collect as _fireteam_collect)

# 九个只关心的装备桶（hash 实测 2026-10）
_WEAPON_BUCKETS = {1498876634: "动能", 2465295065: "能量", 953998645: "威能"}
_ARMOR_BUCKETS = {3448274439: "头盔", 3551918588: "臂铠", 14239492: "胸甲",
                  20886954: "腿甲", 1585787867: "职业披风"}
_GHOST_BUCKET = 4023194814
_SUBCLASS_BUCKET = 3284755031
_WANTED_BUCKETS = set(_WEAPON_BUCKETS) | set(_ARMOR_BUCKETS) | \
    {_GHOST_BUCKET, _SUBCLASS_BUCKET}

# 六维 hash → 中文（Edge of Fate 新属性制：Bungie 沿用旧 hash 但含义已换，
# 上限 200+；stats.json 官方 zh 名照录，顺序按游戏内 武器/生命/职业/超能/手雷/近战）
_STAT_ZH = [(2996146975, "武器"), (392767087, "生命"), (1943323491, "职业"),
            (144602215, "超能"), (1735777505, "手雷"), (4244567218, "近战")]

# 武器 perk 芯片白名单（itemTypeDisplayName 子串，zh manifest 实测：
# 强化卷是「强化特征/强化固有特性/强化发射器枪管」这类变体）
_WEAPON_ITD = ("固有", "枪管", "弹匣", "特性", "特征", "原始特性")

_ITEMS: dict | None = None          # item_zh.json 懒加载（build_item_index.py 产出）
_PLUG_META: dict | None = None


def _items() -> dict:
    """item_zh.json：hash → [名, 图标路径, 类型名, tierType, 插件分类]。

    索引文件 exe 包里带（raw_items.json 220MB 不进包，见 spec _MI_SKIP）。"""
    global _ITEMS
    if _ITEMS is None:
        try:
            _ITEMS = json.load(open(d2._idx_file("item_zh.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001  旧部署没索引时全部退英文名，不至于炸
            _ITEMS = {}
    return _ITEMS


def _plug_meta() -> dict:
    global _PLUG_META
    if _PLUG_META is None:
        try:
            _PLUG_META = json.load(open(d2._idx_file("plug_meta.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _PLUG_META = {}
    return _PLUG_META


def _def(hash_: int) -> dict:
    v = _items().get(str(hash_))
    if not v:
        return {}
    return {"name": v[0], "icon": v[1], "itd": v[2], "tier": v[3], "cat": v[4]}


def _name(hash_: int) -> str:
    return _def(hash_).get("name") or ""


def _icon(hash_: int) -> str:
    """插件图标：plug_meta（全量 URL）优先，缺了退索引里的相对路径。"""
    u = (_plug_meta().get(str(hash_)) or {}).get("icon")
    if not u:
        p = _def(hash_).get("icon")
        u = d2.BASE + p if p else ""
    return u


def _tier(hash_: int) -> str:
    """稀有度中文名（异域/传说/…），用于武器名着色。"""
    return {2: "普通", 3: "稀有", 4: "史诗", 5: "传说", 6: "异域"}.get(
        _def(hash_).get("tier") or 0, "")


def _classify_subclass(plug_hashes: list[int]) -> dict:
    """子职业插槽插值 → 超能 / 职业技能 / 分支 / 碎片 四组（保持槽位顺序）。"""
    out = {"super": [], "abilities": [], "aspects": [], "fragments": []}
    for h in plug_hashes:
        d = _def(h)
        itd = d.get("itd") or ""
        if not d.get("name"):
            continue
        chip = {"name": d["name"], "icon": _icon(h)}
        if "超能" in itd:
            out["super"].append(chip)
        elif "碎片" in itd:
            out["fragments"].append(chip)
        elif "星相" in itd:
            out["aspects"].append(chip)
        else:
            out["abilities"].append(chip)
    return out


def _weapon_chips(sockets: list[dict]) -> list[dict]:
    chips = []
    for s in sockets:
        h = s.get("plugHash")
        if not h or s.get("isVisible") is False:
            continue
        itd = _def(h).get("itd") or ""
        if any(k in itd for k in _WEAPON_ITD):
            chips.append({"name": _name(h), "icon": _icon(h)})
    return chips[:7]


def _armor_chips(sockets: list[dict], ghost: bool = False) -> list[dict]:
    chips = []
    for s in sockets:
        h = s.get("plugHash")
        if not h or s.get("isVisible") is False:
            continue
        d = _def(h)
        itd = d.get("itd") or ""
        cat = d.get("cat") or ""
        if (itd.endswith("护甲模组") or ".tuning.mods" in cat
                or (ghost and itd.endswith("机灵模组"))):
            chips.append({"name": _name(h), "icon": _icon(h)})
    return chips[:7]


_CREST: dict | None = None


def _crest_icon(name: str, item_hash: int) -> str:
    """子职业纹章：装备槽 def 的图标是通用元素菱形，好看的纹章在同名
    （itemCategory 3109687656）def 上（sub_crest.json，build_item_index.py 产出）。
    冰影/缠绕没有纹章 def，回退自带菱形图标。"""
    global _CREST
    if _CREST is None:
        try:
            _CREST = json.load(open(d2._idx_file("sub_crest.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _CREST = {}
    p = _CREST.get(name)
    return d2.BASE + p if p else _icon(item_hash)


async def _member_loadout(m: dict, is_self: bool) -> dict:
    """单个成员的配装块：名字/职业/光等/横幅 + 六维 + 子职业 + 武器/护甲。"""
    mid, mtype = m["mid"], m["mtype"]
    row = {"mid": mid, "name": m.get("name") or f"…{str(mid)[-6:]}", "class": "",
           "light": 0, "emblem": "", "emblem_bg": "", "is_self": is_self,
           "hidden": True, "stats": [], "subclass": None, "weapons": [], "armor": []}
    try:
        prof = await _profile_ex(mtype, mid, "100,200,205,300,305")
        ui = ((prof.get("profile") or {}).get("data") or {}).get("userInfo") or {}
        nm = ui.get("bungieGlobalDisplayName") or ui.get("displayName")
        if nm and not m.get("name"):
            row["name"] = f"{nm}#{d2.fmt_code(ui.get('bungieGlobalDisplayNameCode'))}"
        chars = (prof.get("characters") or {}).get("data") or {}
        if not chars:
            return row
        cid, c = max(chars.items(), key=lambda kv: kv[1].get("dateLastPlayed", ""))
        row["class"] = d2.CLASS_NAMES.get(c.get("classType"), "")
        row["light"] = int(c.get("light") or 0)
        if c.get("emblemPath"):
            row["emblem"] = d2.BASE + c["emblemPath"]      # 96×96 纹章方块
        if c.get("emblemBackgroundPath"):
            row["emblem_bg"] = d2.BASE + c["emblemBackgroundPath"]  # 474×96 宽幅底图
        row["stats"] = [{"zh": zh, "v": max(0, int((c.get("stats") or {}).get(str(h)) or 0))}
                        for h, zh in _STAT_ZH]

        eq = ((prof.get("characterEquipment") or {}).get("data") or {}).get(cid) or {}
        inst = ((prof.get("itemComponents") or {}).get("instances") or {}).get("data") or {}
        sockets_all = ((prof.get("itemComponents") or {}).get("sockets") or {}).get("data") or {}
        by_bucket: dict[int, dict] = {}
        for it in eq.get("items") or []:
            b = it.get("bucketHash")
            if b in _WANTED_BUCKETS:
                by_bucket[b] = it
        if not by_bucket:
            return row                  # 库存隐私：205 缺失
        row["hidden"] = False

        # 子职业：插槽顺序即 超能/技能/分支/碎片 混排，分类后各归各位
        sub = by_bucket.get(_SUBCLASS_BUCKET)
        if sub:
            sh = sub.get("itemHash")
            so = (sockets_all.get(sub.get("itemInstanceId") or "") or {}).get("sockets") or []
            phs = [s["plugHash"] for s in so
                   if s.get("plugHash") and s.get("isEnabled") is not False]
            row["subclass"] = {"name": _name(sh), "icon": _crest_icon(_name(sh), sh),
                               **_classify_subclass(phs)}
        for b, slot in _WEAPON_BUCKETS.items():
            it = by_bucket.get(b)
            if not it:
                continue
            iid = it.get("itemInstanceId") or ""
            so = (sockets_all.get(iid) or {}).get("sockets") or []
            pw = ((inst.get(iid) or {}).get("primaryStat") or {}).get("value")
            row["weapons"].append({
                "slot": slot, "name": _name(it.get("itemHash")),
                "tier": _tier(it.get("itemHash")), "icon": _icon(it.get("itemHash")),
                "power": pw, "chips": _weapon_chips(so)})
        for b, slot in _ARMOR_BUCKETS.items():
            it = by_bucket.get(b)
            if not it:
                continue
            iid = it.get("itemInstanceId") or ""
            so = (sockets_all.get(iid) or {}).get("sockets") or []
            pw = ((inst.get(iid) or {}).get("primaryStat") or {}).get("value")
            row["armor"].append({
                "slot": slot, "name": _name(it.get("itemHash")),
                "tier": _tier(it.get("itemHash")),
                "icon": _icon(it.get("itemHash")), "power": pw,
                "chips": _armor_chips(so)})
        gh = by_bucket.get(_GHOST_BUCKET)
        if gh:
            so = (sockets_all.get(gh.get("itemInstanceId") or "") or {}).get("sockets") or []
            row["weapons"].append({
                "slot": "机灵", "name": _name(gh.get("itemHash")),
                "tier": _tier(gh.get("itemHash")), "icon": _icon(gh.get("itemHash")),
                "power": None, "chips": _armor_chips(so, ghost=True)})
    except Exception:  # noqa: BLE001  单人失败只丢这一块
        pass
    return row


async def collect(name: str) -> dict:
    """配装卡数据：队伍名单（复用 bot_fireteam）→ 每人已装备栏结构化。

    玩家不存在抛 LookupError。"""
    data = await _retry_twice(_fireteam_collect, name)
    roster = (data.get("roster") or [])[:_MAX_MEMBERS]
    main = roster[0] if roster else None
    members = list(await asyncio.gather(*(
        _row_budget(_member_loadout(r, r["mid"] == (main or {}).get("mid")),
                    r["mid"], r["mid"] == (main or {}).get("mid"), r.get("name"))
        for r in roster)))
    return {"name": data.get("name") or name, "state": data.get("state") or "",
            "mode_name": data.get("mode_name") or "", "members": members}


# ---------- 游戏内配装（/配装 数字） 与 仓库搜索（/仓库），走发起者自己的授权 ----------
# 官方隐私：这两样只对 token 本人可见（DIM 同款限制），所以每个 QQ 用户先 /登录。

_UNSET_PLUG = 2166136261   # 游戏内配装里「空插槽」的占位 hash（DIM 口径）


def _loadout_chips(plugs: list[int], kind: str) -> list[dict]:
    """游戏内配装的 plugItemHashes → 芯片行（与实装卡同一套过滤口径）。"""
    chips = []
    for h in plugs:
        nm = _name(h)
        if not nm or nm.startswith("空"):
            continue
        d = _def(h)
        itd, cat = d.get("itd") or "", d.get("cat") or ""
        ok = ((kind == "weapon" and any(k in itd for k in _WEAPON_ITD))
              or (kind in ("armor", "ghost")
                  and (itd.endswith("护甲模组" if kind == "armor" else "机灵模组")
                       or ".tuning.mods" in cat)))
        if ok:
            chips.append({"name": nm, "icon": _icon(h)})
    return chips[:7]


async def collect_ingame(qq: str, slot: int) -> dict:
    """读取发起者第 slot(1-based) 套游戏内配装（官方组件 206，仅 token 本人可见）。

    未授权 / 序号越界 / 组件私有 抛 RuntimeError，指令层出提示卡。"""
    st = bungie_auth.user_status(qq)
    if not st.get("authorized"):
        raise RuntimeError(
            "游戏内配装是官方仅本人可见的数据：先发 /登录 完成 Bungie 授权，"
            "授权后回群再发 /配装 数字（1-20）")
    mtype, mid = st.get("membership_type"), st.get("membership_id")
    if not mid:
        raise RuntimeError("授权信息缺成员号，请重新 /登录")
    resp = await bungie_auth.authorized_get_as(
        qq, f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
        # 注意 itemComponents(300/305) 只对「随请求一起拉了 102/201/205 的物品」生成，
        # 只给 206 时 instances 是空的——配装里的金库件就拿不到 hash/光等了
        {"components": "200,206,102,201,205,300,305"})
    chars = (resp.get("characters") or {}).get("data") or {}
    if not chars:
        raise RuntimeError("拿不到角色信息（隐私/跨存档），请重新 /登录")
    cid, c = max(chars.items(), key=lambda kv: kv[1].get("dateLastPlayed", ""))
    entry = (((resp.get("characterLoadouts") or {}).get("data") or {}).get(cid)) or {}
    ls = entry.get("loadouts") or []
    if not ls:
        raise RuntimeError("读不到游戏内配装：可能在游戏里还没存过配装，"
                           "或授权时没拿到 ReadUserData 权限——重新 /登录 一次再试")
    if not 1 <= slot <= len(ls):
        raise RuntimeError(f"配装序号是 1-{len(ls)}（该角色共 {len(ls)} 套）")
    lo = ls[slot - 1]
    inst = ((resp.get("itemComponents") or {}).get("instances") or {}).get("data") or {}
    sockets_all = ((resp.get("itemComponents") or {}).get("sockets") or {}).get("data") or {}
    # 配装件只有 itemInstanceId（没有 hash/桶位）：instances 组件也不带 itemHash，
    # 从 102/201/205 的物品清单按 instanceId 建反查表
    by_iid: dict[str, dict] = {}
    for lst in (((resp.get("profileInventory") or {}).get("data") or {}).get("items") or [],
                *[x.get("items") or [] for x in
                  (((resp.get("characterInventories") or {}).get("data")) or {}).values()],
                *[x.get("items") or [] for x in
                  (((resp.get("characterEquipment") or {}).get("data")) or {}).values()]):
        for it in lst:
            iid = str(it.get("itemInstanceId") or "")
            if iid:
                by_iid[iid] = it

    row = {"mid": mid, "name": st.get("display_name") or f"…{str(mid)[-6:]}",
           "class": d2.CLASS_NAMES.get(c.get("classType"), ""),
           "light": int(c.get("light") or 0),
           "emblem": d2.BASE + c["emblemPath"] if c.get("emblemPath") else "",
           "emblem_bg": d2.BASE + c["emblemBackgroundPath"] if c.get("emblemBackgroundPath") else "",
           "is_self": True, "hidden": False, "stats": [],
           "subclass": None, "weapons": [], "armor": [],
           "slot_label": f"配装 {slot}"}
    for it in lo.get("items") or []:
        iid = str(it.get("itemInstanceId") or "")
        base = by_iid.get(iid) or {}
        rec = inst.get(iid) or {}
        h = base.get("itemHash") or rec.get("itemHash")
        if not h:
            continue                      # 占位（instance 0 = 没存实体的槽）
        bucket = base.get("bucketHash") or rec.get("bucketHash")
        plugs = [p for p in (it.get("plugItemHashes") or [])
                 if p and p != _UNSET_PLUG]
        name, icon, tier = _name(h), _icon(h), _tier(h)
        if bucket == _SUBCLASS_BUCKET:
            row["subclass"] = {"name": name, "icon": _crest_icon(name, h),
                               **_classify_subclass(plugs)}
        elif bucket in _WEAPON_BUCKETS:
            row["weapons"].append({
                "slot": _WEAPON_BUCKETS[bucket], "name": name, "tier": tier,
                "icon": icon,
                "power": ((rec.get("primaryStat") or {}).get("value")),
                "chips": _loadout_chips(plugs, "weapon")})
        elif bucket in _ARMOR_BUCKETS:
            row["armor"].append({
                "slot": _ARMOR_BUCKETS[bucket], "name": name, "tier": tier,
                "icon": icon,
                "power": (rec.get("primaryStat") or {}).get("value"),
                "chips": _loadout_chips(plugs, "armor")})
        elif bucket == _GHOST_BUCKET:
            row["weapons"].append({
                "slot": "机灵", "name": name, "tier": tier, "icon": icon,
                "power": None, "chips": _loadout_chips(plugs, "ghost")})
    return {"name": row["name"], "slot": slot, "members": [row]}


_VAULT_BUCKET = 138197802


async def vault_search(qq: str, kw: str) -> dict:
    """在发起者自己的 仓库/背包/已装备 里按中文名搜物品（官方 102/201/205，仅本人可见）。"""
    st = bungie_auth.user_status(qq)
    if not st.get("authorized"):
        raise RuntimeError(
            "仓库是官方仅本人可见的数据：先发 /登录 完成 Bungie 授权，"
            "授权后回群再发 /仓库 关键词")
    mtype, mid = st.get("membership_type"), st.get("membership_id")
    if not mid:
        raise RuntimeError("授权信息缺成员号，请重新 /登录")
    resp = await bungie_auth.authorized_get_as(
        qq, f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
        {"components": "100,102,200,201,205,300"})
    inst = ((resp.get("itemComponents") or {}).get("instances") or {}).get("data") or {}
    chars = (resp.get("characters") or {}).get("data") or {}
    nk = d2.norm_key(kw)

    def hit(h: int) -> bool:
        n = _name(h)
        return bool(n) and (kw.lower() in n.lower() or (nk and nk in d2.norm_key(n)))

    out = []
    seen: set[str] = set()

    def add(item: dict, loc: str):
        h, iid = item.get("itemHash"), str(item.get("itemInstanceId") or "")
        if not hit(h) or iid in seen:
            return
        seen.add(iid)
        rec = inst.get(iid) or {}
        out.append({
            "name": _name(h), "tier": _tier(h), "icon": _icon(h),
            "type": _def(h).get("itd") or "",
            "power": (rec.get("primaryStat") or {}).get("value"),
            "quantity": int(item.get("quantity") or 1), "loc": loc})

    for it in ((resp.get("profileInventory") or {}).get("data") or {}).get("items") or []:
        add(it, "仓库" if it.get("bucketHash") == _VAULT_BUCKET else "账号")
    for cid, inv in (((resp.get("characterInventories") or {}).get("data")) or {}).items():
        cls = d2.CLASS_NAMES.get((chars.get(cid) or {}).get("classType"), "")
        for it in inv.get("items") or []:
            add(it, f"背包·{cls}" if cls else "背包")
    for cid, eq in (((resp.get("characterEquipment") or {}).get("data")) or {}).items():
        cls = d2.CLASS_NAMES.get((chars.get(cid) or {}).get("classType"), "")
        for it in eq.get("items") or []:
            add(it, f"已装备·{cls}" if cls else "已装备")
    out.sort(key=lambda x: (-(x.get("power") or 0), x["name"]))
    return {"name": st.get("display_name") or f"…{str(mid)[-6:]}",
            "kw": kw, "total": len(out), "items": out[:30]}
