"""DIM 板块数据层：背包/仓库、成就、配装。

设计要点（都是实测出来的，别想当然改）：
  * 一次 GetProfile 拉全需要的东西（DIM 也是这么干的），三个功能共用一次请求。
  * 仓库(profileInventory)里的武器/护甲，上报的 bucketHash 一律是「一般」(138197802)，
    所以**分组必须用物品定义里的真实桶**（dim_items.json 第 8 列）。
  * 邮政官不是单独分量，它在 characterInventories 里，桶是「遗失物品」(215593132)。
  * 消耗品/改装模组/任务属于账号级清单，也挂在 profileInventory 里。
  * 游戏内配装(206)只给 nameHash/iconHash/colorHash，名称图标要查 dim_loadouts.json。
  * 「能不能搬」按 DIM 的做法推导（不可转移 + 类型 + 桶位置），最终以 Bungie 报错为准：
    这份 manifest 缓存里的 allowActions 被规范化成了布尔值，拿不到逐项的 transferItem。

写操作只走三个官方接口：TransferItem / EquipItem / EquipItems。
"""
from __future__ import annotations

import json
import os
import time

import destiny_data as d2
import bungie_auth

# ---------- 静态索引 ----------

# 分量编号（Bungie DestinyComponentType，容易记错，列在这）：
#   100=profiles 102=inventories 103=currencies 104=progression
#   300=instances 301=objectives 302=perks 304=stats 305=sockets
#   306=talentGrids 308=plugStates 309=plugObjectives 310=reusablePlugs
# 网格：仓库/角色背包/装备/配装 + 角色 + 实例(光等/是否装备) + 实例属性
GRID_COMPONENTS = "102,103,200,201,205,206,300,304"
# 单件详情：实例 + Perk + 属性 + 插槽(mod)
DETAIL_COMPONENTS = "300,302,304,305"

VAULT_BUCKET = 138197802      # 「一般」——仓库里所有东西上报的桶
POSTMASTER_BUCKET = 215593132  # 「遗失物品」= 邮政官
POSTMASTER_CAP = 21

CLASS_NAMES = {0: "泰坦", 1: "猎人", 2: "术士", 3: "通用"}
TIER_NAMES = {6: "异域", 5: "传说", 4: "稀有", 3: "罕见", 2: "普通", 1: "普通", 0: ""}
DAMAGE_TYPES = {0: "", 1: "动能", 2: "电弧", 3: "灼烧", 4: "虚空", 5: "烈日", 6: "冰影", 7: "缚丝"}
# 能装进角色背包/仓库的桶位置：1=角色背包 2=仓库
_LOC_CHAR, _LOC_VAULT, _LOC_POST = 1, 2, 4

_CACHE: dict = {}


def _load(name: str):
    if name not in _CACHE:
        _CACHE[name] = json.load(open(d2._idx_file(name), encoding="utf-8"))
    return _CACHE[name]


def items() -> dict:
    """物品瘦身表：hash → [名称, itemType, 类型名, 稀有度名, 稀有度, 图标, 水印,
    职业, 背包桶, 装备槽, 不可转移, 可穿戴, 分类, 伤害类型, 破盾, 来源, 属性]"""
    return _load("dim_items.json")


def buckets() -> dict:
    """背包桶：hash → [名称, 分类, 容量, 位置, 图标]"""
    return _load("dim_buckets.json")


def categories() -> dict:
    return _load("dim_categories.json")


def loadout_defs() -> dict:
    """配装外观：{"n": 名称表, "i": 图标表, "c": 底色表}"""
    return _load("dim_loadouts.json")


def idef(h) -> list | None:
    return items().get(str(h))


def bucket(h) -> list:
    return buckets().get(str(h)) or ["", 0, 0, 0, ""]


def stat_names() -> dict:
    """属性名表：hash → 中文名（生命值/超能/装填速度…）"""
    return _load("stats.json")


def iname(h) -> str:
    d = idef(h)
    return d[0] if d else f"#{h}"


def iicon(h) -> str:
    d = idef(h)
    return d[5] if d else ""


def bname(h) -> str:
    return bucket(h)[0] or "其他"


def _icon_url(path: str) -> str:
    """图标路径 → 完整 URL。

    注意：manifest 里绝大多数是 "/common/..." 这种相对路径，但 perks.json 里存的
    是**绝对地址**（个别还写成 "https//www.bungie.net/..."，协议里少个冒号）。
    所以这里必须判一次，否则会拼成 https://www.bungie.nethttps//... 直接 DNS 失败。
    """
    if not path:
        return ""
    p = str(path).strip()
    low = p.lower()
    if "bungie.net" in low:
        return "https://www.bungie.net" + p[low.index(".net") + 4:]
    if low.startswith("//"):
        return "https:" + p
    if low.startswith("http://") or low.startswith("https://"):
        return p
    return "https://www.bungie.net" + p


# ---------- 物品可搬运性（DIM 同款推导）----------


def targets(h, bkt: int) -> tuple[int, int]:
    """这件物品能去哪：(能不能进仓库, 能去的角色位掩码 bit0泰坦/bit1猎人/bit2术士)

    规则跟 DIM 一致，都是从定义推的：最终以后端的报错为准（Bungie 才是裁判）。
    """
    d = idef(h)
    if not d:
        return 0, 0
    if d[10]:                       # nonTransferrable：焊死的
        return 0, 0
    if d[1] in (9, 10, 11):         # 任务/任务步骤进不了仓库
        vault = 0
    else:
        vault = 1
    if d[1] == 2 and d[7] != 3:     # 护甲按职业，武器/其他谁都行
        mask = 1 << d[7] if d[7] in (0, 1, 2) else 0
    else:
        mask = 7
    if bucket(bkt)[3] == _LOC_VAULT:
        vault = 0                   # 已经在仓库里的，就别再「搬进仓库」了
    return vault, mask


def can_transfer(h, bkt: int) -> bool:
    """能不能搬动（不含目标是否放得下）"""
    v, m = targets(h, bkt)
    return bool(v or m)


def can_equip(h, bkt: int, char_class: int) -> bool:
    """能不能装备到某个职业的角色身上"""
    d = idef(h)
    if not d or not d[11]:         # 不可穿戴
        return False
    if d[7] != 3 and d[7] != char_class:   # 职业受限
        return False
    return bucket(bkt)[1] == 3     # 分类 3 = 可装备


def _score(d: dict) -> int:
    """记录的分值（Manifest 里这个键是 PascalCase 的 ScoreValue）"""
    ci = d.get("completionInfo") or {}
    return ci.get("ScoreValue") or ci.get("scoreValue") or 0


# ---------- 读取 ----------


def _power(instances: dict, iid: str, gear: bool = True) -> int:
    """光等。只对可穿戴的东西有意义（飞船/载具没有光等，子职业那个值是脏数据，一律不显示）"""
    if not gear:
        return 0
    inst = (instances or {}).get(iid) or {}
    ps = inst.get("primaryStat") or {}
    v = ps.get("value") or 0
    return v if v > 0 else 0


def _unwrap(x):
    """单件接口(/Item/)的分量包在 {"data": ...} 里，档案接口则各有各的形状"""
    if isinstance(x, dict) and "data" in x:
        return x["data"]
    return x


def _inner(x, key: str):
    """两层包装 {data: {key: [...]}} 里取内容（perks/stats/sockets 都是这个形状）"""
    x = _unwrap(x)
    if isinstance(x, dict) and key in x:
        return x.get(key) or {}
    return x or {}


def _stats(stats: dict, iid: str) -> dict:
    st = ((stats or {}).get(iid) or {}).get("stats") or {}
    out = {}
    for h, v in st.items():
        val = (v or {}).get("value")
        if val:
            out[h] = val
    return out


def _pack(comp: list, instances: dict, stats: dict, where: str = "") -> list:
    """把一份 itemComponents 条目列表压成前端要的紧凑结构。

    where 是这批物品当前所在的容器（"vault" 或角色 id），前端据此决定菜单里给什么选项；
    vc/cx 是「能进仓库 / 能去哪些角色」的推导结果，前端直接用，不重复实现规则。
    """
    out = []
    for it in comp or []:
        h = it.get("itemHash")
        iid = it.get("itemInstanceId") or ""
        bkt = it.get("bucketHash") or 0
        d = idef(h)
        vc, cx = targets(h, bkt)
        inst = (instances or {}).get(iid) or {}
        st = it.get("state") or 0
        cats = d[12] if d else []
        rec = {
            "i": iid,
            "h": h,
            "tid": str(iid) if iid else "h" + str(h),   # 标签/备注的键（跟 dim_user.tid 一致）
            "b": bkt,
            "w": where,
            "q": it.get("quantity") or 1,
            "s": st,                            # 1=锁定 4=大师之作 8=锻造（Bungie ItemState）
            "mw": 1 if st & 4 else 0,
            "pw": _power(instances, iid, bool(d and d[11])),
            "eq": bool(inst.get("isEquipped")),
            "vc": vc,
            "cx": cx,
            "eqo": 1 if (d and d[11]) else 0,
            "n": d[0] if d else f"#{h}",
            "ic": _icon_url(d[5]) if d else "",
            "wm": _icon_url(d[6]) if d else "",
            "ty": d[2] if d else "",
            "tn": d[3] if d else "",
            "tt": d[4] if d else 0,
            "cls": d[7] if d else 3,
            "db": d[8] if d else bkt,           # 定义里的真实桶（仓库分组靠它）
            "slotName": bname(d[8]) if d else "",
            "cat": cats,
            # 前端筛选/分组要用的粗分类：1=武器 20=护甲（Bungie itemCategoryHashes）
            "kind": "weapon" if 1 in cats else ("armor" if 20 in cats else ""),
            "dmg": d[13] if d else 0,
            "src": d[15] if d else "",
        }
        en = inst.get("energy") or {}
        if en.get("energyCapacity"):
            rec["en"] = en.get("energyCapacity")
        if d and d[11]:                          # 可穿戴的才带属性
            rec["st"] = _stats(stats, iid)
        out.append(rec)
    return out


def _currencies(r: dict) -> list:
    """账户货币（光尘/传说碎片/微光…）"""
    data = ((r.get("profileCurrencies") or {}).get("data") or {}).get("items") or []
    out = []
    for it in data:
        h = it.get("itemHash")
        out.append({"h": h, "n": iname(h), "ic": _icon_url(iicon(h)),
                    "q": it.get("quantity") or 0})
    return out


async def inventory(force: bool = False) -> dict:
    """背包/仓库全量（一次请求）

    带 3 秒缓存：一次页面加载后紧跟着的配装器搜索不必再打一次 Bungie；
    写操作后的对账（前端带 force=1）永远读新数据，避免看到旧位置。
    """
    hit = _CACHE.get("inv")
    if not force and hit and time.time() - hit[0] < 3.0:
        return hit[1]
    mt, mid = await _me()
    r = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/", {"components": GRID_COMPONENTS})
    ic = r.get("itemComponents") or {}
    instances = (ic.get("instances") or {}).get("data") or {}
    stats = (ic.get("stats") or {}).get("data") or {}

    chars = []
    for cid, ch in ((r.get("characters") or {}).get("data") or {}).items():
        cls = ch.get("classType")
        inv = (((r.get("characterInventories") or {}).get("data") or {}).get(cid) or {})
        equ = (((r.get("characterEquipment") or {}).get("data") or {}).get(cid) or {})
        packed = _pack(inv.get("items"), instances, stats, cid)
        # 邮政官混在角色背包里，按桶拆出来单独一栏
        post = [x for x in packed if x["b"] == POSTMASTER_BUCKET]
        bag = [x for x in packed if x["b"] != POSTMASTER_BUCKET]
        chars.append({
            "id": cid,
            "cls": cls,
            "clsName": CLASS_NAMES.get(cls, "?"),
            "light": ch.get("light") or 0,
            "emblem": _icon_url(ch.get("emblemBackgroundPath") or ""),
            "equipped": _pack(equ.get("items"), instances, stats, cid),
            "bag": bag,
            "post": post,
            "postCap": POSTMASTER_CAP,
        })
    # 固定顺序：泰坦/猎人/术士（跟游戏里一致）
    chars.sort(key=lambda c: c["cls"])

    vault = _pack((((r.get("profileInventory") or {}).get("data") or {}).get("items")),
                  instances, stats, "vault")
    vcap = bucket(VAULT_BUCKET)[2] or 0
    vused = sum(1 for x in vault if x["b"] == VAULT_BUCKET)
    out = {
        "chars": chars,
        "vault": vault,
        "vaultCap": vcap,
        "vaultUsed": vused,
        "vaultBucket": VAULT_BUCKET,
        # 空装备槽也要能当拖拽目标（3 武器 + 5 护甲，跟游戏装备页一致）
        "mainSlots": [1498876634, 2465295065, 953998645, 3448274439, 3551918588,
                      14239492, 20886954, 1585787867],
        "currencies": _currencies(r),
        "bucketNames": {str(k): v[0] for k, v in buckets().items()},
        "bucketCat": {str(k): v[1] for k, v in buckets().items()},
        "bucketCap": {str(k): v[2] for k, v in buckets().items()},
        "bucketLoc": {str(k): v[3] for k, v in buckets().items()},
        "categoryNames": categories(),
        "statNames": stat_names(),
    }
    _CACHE["inv"] = (time.time(), out)
    return out


async def _me() -> tuple[int, str]:
    """当前授权账号的 (membershipType, membershipId)。带缓存：换账号（token 里的 id 变了）才重查"""
    tok = bungie_auth._load()
    mid = str(tok.get("membership_id") or "")
    if _CACHE.get("me_key") == mid and _CACHE.get("me"):
        return _CACHE["me"]
    m = await bungie_auth.membership()
    if not m:
        if not tok.get("membership_id"):
            raise RuntimeError("还没授权 Bungie 账号，请到「Bot 面板」完成授权")
        m = {"membership_type": tok.get("membership_type"),
             "membership_id": tok.get("membership_id")}
    _CACHE["me"] = (m["membership_type"], str(m["membership_id"]))
    _CACHE["me_key"] = str(m["membership_id"])
    return _CACHE["me"]


async def item_detail(instance_id: str, item_hash) -> dict:
    """单件详情（插槽/Perk/属性），按需拉，一次只取这一件。

    物品 hash 由前端带过来：单件接口在没请求 300 分量时不回 item，白等一圈不如直接用。"""
    mt, mid = await _me()
    r = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/Item/{instance_id}/",
        {"components": DETAIL_COMPONENTS})
    # 单件接口的返回是「扁平」的：instance/perks/stats/sockets 直接挂在顶层（各自带 data），
    # 跟档案接口的 itemComponents.xxx.data[实例] 完全不是一个形状，别照抄。
    d = idef(item_hash)
    inst = _unwrap(r.get("instance")) or {}
    stats_raw = _inner(r.get("stats"), "stats")
    if isinstance(stats_raw, dict) and "stats" in stats_raw:
        stats_raw = stats_raw.get("stats") or {}
    stat_names = _load("stats.json")

    stats = []
    for sh, sv in stats_raw.items():
        val = (sv or {}).get("value")
        if val:
            stats.append({"n": stat_names.get(str(sh), f"#{sh}"), "v": val})

    # Perk（沙盒 perk：perks.json 里有名字和效果）
    perks_raw = _inner(r.get("perks"), "perks") or []
    pz = _load("perks.json")
    perks = []
    for p in perks_raw:
        ph = p.get("perkHash")
        rec = pz.get(str(ph)) or {}
        if rec.get("name"):
            perks.append({"n": rec.get("name"), "d": rec.get("desc") or "",
                          "ic": _icon_url(rec.get("icon") or "")})

    # 插槽：当前插进去的 mod/枪管/弹匣…（插槽里放的是物品定义，所以名字查 dim_items）
    sockets = []
    for s in _inner(r.get("sockets"), "sockets") or []:
        if s.get("isVisible") is False:
            continue
        ph = s.get("plugHash") or s.get("plugItemHash")
        if not ph:
            continue
        pd = idef(ph)
        if not pd or not pd[0]:
            continue
        sockets.append({"i": s.get("socketIndex"), "h": ph, "n": pd[0], "ty": pd[2],
                        "ic": _icon_url(pd[5]),
                        "en": s.get("isEnabled", True)})
    return {
        "ok": True,
        "name": d[0] if d else "",
        "icon": _icon_url(d[5] if d else ""),
        "typeName": d[2] if d else "",
        "tierName": d[3] if d else "",
        "tier": d[4] if d else 0,
        "power": _power({instance_id: inst}, instance_id, bool(d and d[11])),
        "stats": stats,
        "perks": perks,
        "sockets": sockets,
        "source": d[15] if d else "",
        "class": d[7] if d else 3,
        "dmg": d[13] if d else 0,
    }


# ---------- 写操作 ----------

async def _post(path: str, body: dict) -> dict:
    import httpx
    tok = await bungie_auth.access_token()
    if not tok:
        raise RuntimeError("还没授权 Bungie 账号")
    headers = {"X-API-Key": bungie_auth._env("BUNGIE_API_KEY"),
               "Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(bungie_auth.BASE + path, json=body, headers=headers)
        d = r.json()
    if d.get("ErrorCode") != 1:
        raise RuntimeError(err_text(d))
    return d.get("Response") or {}


# Bungie 的错误码 → 人话。写操作最常见的几个，其余原样带出 ErrorStatus。
ERR_TEXT = {
    "DestinyItemNotFound": "物品不存在（可能已被移动或删除）",
    "DestinyNoRoomInDestination": "目标位置放不下（背包/仓库已满）",
    "DestinyItemNotTransferrable": "这件物品不能转移",
    "DestinyItemAlreadyInInventory": "物品已经在目标位置了",
    "DestinyCannotEquipItemToCharacter": "这个角色不能装备它",
    "DestinyCannotEquipItemInActivity": "角色正在活动中，不能换装",
    "DestinyItemIsEquipped": "物品正装备在身上，先换下再移动",
    "DestinyItemUniqueEquipRestricted": "已装备同名的异域物品",
    "DestinyItemNotEquipped": "物品没有装备在身上",
    "DestinyNoInventorySpace": "背包空间不足",
    "DestinyCharacterNotFound": "角色不存在（请刷新页面）",
    "DestinyItemNotTransferableToVault": "这件物品不能放进仓库",
    "DestinyInvalidRequest": "请求不合法（该物品/位置不支持这个操作）",
    "PrivacyRestriction": "隐私设置不允许",
    "InsufficientPrivileges": "授权不足，请重新授权 Bungie 账号",
}


def err_text(d: dict) -> str:
    st = d.get("ErrorStatus") or ""
    if st in ERR_TEXT:
        return ERR_TEXT[st]
    msg = d.get("Message") or ""
    return f"{st}（{msg}）" if st else f"未知错误 {d.get('ErrorCode')}"


async def transfer(instance_id: str, item_hash, to_vault: bool, char_id: str = "") -> dict:
    """在仓库与角色之间搬一件物品。

    注意：写接口的请求体必须带 membershipType（读接口的路径里带就够了，写接口不带会回
    DestinyInvalidMembershipType「You require a linked @membershipType account」）。
    """
    mt, _ = await _me()
    body = {"itemReferenceHash": int(item_hash), "stackSize": 1,
            "transferToVault": bool(to_vault), "itemId": str(instance_id),
            "characterId": str(char_id), "membershipType": mt}
    return await _post("/Platform/Destiny2/Actions/Items/TransferItem/", body)


async def equip(instance_id: str, item_hash, char_id: str) -> dict:
    mt, _ = await _me()
    body = {"itemId": str(instance_id), "characterId": str(char_id), "membershipType": mt}
    return await _post("/Platform/Destiny2/Actions/Items/EquipItem/", body)


async def equip_any(instance_id: str, item_hash, char_id: str, frm: str = "") -> dict:
    """装备一件物品。物品在仓库里时先搬过去再装 —— 直接 EquipItem 会失败
    （Bungie 不允许装备不在角色身上的东西），DIM 也是「先搬后装」。"""
    if frm == "vault":
        try:
            await transfer(instance_id, item_hash, False, char_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"先搬到角色失败：{exc}")
    return await equip(instance_id, item_hash, char_id)


async def equip_many(item_ids: list, char_id: str) -> dict:
    """批量装备。接口收的是实例 id 列表（itemIds），不是「实例+hash」"""
    mt, _ = await _me()
    body = {"itemIds": [str(i) for i in item_ids], "characterId": str(char_id),
            "membershipType": mt}
    return await _post("/Platform/Destiny2/Actions/Items/EquipItems/", body)


async def pull_from_postmaster(instance_id: str, item_hash, char_id: str) -> dict:
    """从邮政官取回到角色背包（TransferItem 的 characterId 指向该角色、transferToVault=false）"""
    return await transfer(instance_id, item_hash, False, char_id)


async def lock(instance_id: str, on: bool) -> dict:
    """锁定/解锁（Bungie SetItemLockState；DIM 的锁图标同源）"""
    mt, _ = await _me()
    return await _post("/Platform/Destiny2/Actions/Items/SetItemLockState/",
                       {"itemId": str(instance_id), "characterId": "", "membershipType": mt,
                        "state": bool(on)})


async def apply_items(char_id: str, items: list, inv: dict | None = None) -> dict:
    """把一组配装物品装到角色身上（DIM 的「应用配装」）。

    items = [{"h": 物品hash, "i": 实例id}, ...]；只有 hash 没有实例的（未拥有的参考件）跳过。
    inv 给的是 inventory() 的快照，用来判断哪些已经在身上，省掉注定失败的搬运请求。
    逐件装备而不是 EquipItems 批量：批量一失败整批失败，分不清是哪件的问题。
    """
    on_char = set()
    if inv:
        for c in inv.get("chars") or []:
            if c.get("id") == char_id:
                on_char = {x["i"] for x in (c.get("equipped") or []) if x.get("i")}
    equipped, moved, skipped, errors = [], [], [], []
    for row in items:
        h = row.get("h") if isinstance(row, dict) else row[1]
        iid = str((row.get("i") if isinstance(row, dict) else row[0]) or "")
        if not iid:
            skipped.append(iname(h))
            continue
        if iid not in on_char:
            try:
                await transfer(iid, h, False, char_id)
                moved.append(iid)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{iname(h)}：搬运失败（{exc}）")
                continue
        try:
            await equip(iid, h, char_id)
            equipped.append(iid)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{iname(h)}：装备失败（{exc}）")
    return {"ok": not errors, "equipped": len(equipped), "moved": len(moved),
            "skipped": skipped, "errors": errors}


async def move(instance_id: str, item_hash, to: str, frm: str = "") -> dict:
    """搬运的统一入口。to = "vault" 或角色 id；frm = 物品当前所在容器（"vault" / 角色 id）。

    Bungie 的 TransferItem 要的是「当前持有者」的 characterId：
      放进仓库 → characterId = 物品现在所在的那个角色，transferToVault=true
      取到角色 → characterId = 目标角色，transferToVault=false（仓库和邮政官都走这条）
    """
    d = idef(item_hash)
    if d and d[10]:
        raise RuntimeError(f"{d[0]} 不能转移（游戏里禁止搬运这类物品）")
    if d and d[1] in (9, 10, 11) and to == "vault":
        raise RuntimeError(f"{d[0]} 是任务类物品，不能放进仓库")
    if to == "vault":
        if frm in ("", "vault"):
            raise RuntimeError("物品已经不在角色身上了，请刷新后重试")
        return await transfer(instance_id, item_hash, True, frm)
    if frm == to:
        raise RuntimeError("物品已经在这个角色身上了")
    return await transfer(instance_id, item_hash, False, to)


# ---------- 配装 ----------


async def loadouts() -> dict:
    """游戏内配装（每角色 20 套，含名称/图标/清单）"""
    mt, mid = await _me()
    r = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/",
        {"components": "200,201,205,206,300"})
    ic = r.get("itemComponents") or {}
    instances = (ic.get("instances") or {}).get("data") or {}
    defs = loadout_defs()
    # instanceId → (itemHash, 所在角色, bucket)，配装里只给了实例 id，要回查这是什么
    owner: dict[str, tuple] = {}
    containers = [("char", cid, (((r.get("characterInventories") or {}).get("data") or {}).get(cid) or {}).get("items"))
                  for cid in ((r.get("characters") or {}).get("data") or {})]
    containers += [("char", cid, (((r.get("characterEquipment") or {}).get("data") or {}).get(cid) or {}).get("items"))
                   for cid in ((r.get("characters") or {}).get("data") or {})]
    containers.append(("vault", "", (((r.get("profileInventory") or {}).get("data") or {}).get("items"))))
    for where, cid, lst in containers:
        for it in lst or []:
            iid = it.get("itemInstanceId")
            if iid:
                owner[iid] = (it.get("itemHash"), where, cid, it.get("bucketHash"))

    chars = []
    for cid, ch in ((r.get("characters") or {}).get("data") or {}).items():
        cls = ch.get("classType")
        ls = (((r.get("characterLoadouts") or {}).get("data") or {}).get(cid) or {}).get("loadouts") or []
        out = []
        for idx, lo in enumerate(ls):
            slots = []
            for entry in lo.get("items") or []:
                iid = entry.get("itemInstanceId") or ""
                h = (owner.get(iid) or [None])[0]
                if not h and iid:
                    continue
                d = idef(h) if h else None
                slots.append({
                    "i": iid, "h": h, "n": d[0] if d else "", "ic": _icon_url(d[5]) if d else "",
                    "wm": _icon_url(d[6]) if d else "", "ty": d[2] if d else "",
                    "tt": d[4] if d else 0, "b": (owner.get(iid) or [None, None, None, 0])[3],
                    "pw": _power(instances, iid, bool(d and d[11])),
                    "here": (owner.get(iid) or [None, None, cid])[2] == cid,
                })
            out.append({
                "n": "", "idx": idx,
                "name": defs["n"].get(str(lo.get("nameHash") or "")) or f"配装 {idx + 1}",
                "icon": _icon_url(defs["i"].get(str(lo.get("iconHash") or "")) or ""),
                "color": _icon_url(defs["c"].get(str(lo.get("colorHash") or "")) or ""),
                "items": slots,
            })
        chars.append({"id": cid, "cls": cls, "clsName": CLASS_NAMES.get(cls, "?"),
                      "light": ch.get("light") or 0,
                      "emblem": _icon_url(ch.get("emblemBackgroundPath") or ""),
                      "loadouts": out})
    chars.sort(key=lambda c: c["cls"])
    return {"chars": chars}


async def apply_loadout(char_id: str, triples: list) -> dict:
    """一键应用配装：逐件装到目标角色身上。

    triples = [[实例id, 物品hash, 是否已在该角色身上], ...]（loadouts() 里已经算好了 here）。
    不在身上的先 TransferItem 搬到该角色 —— Bungie 不允许装备不在身上的东西，
    DIM 也是「先搬后装」。逐件装备而不是用 EquipItems 批量：批量一失败就整批失败，
    分不清是哪件的问题。
    """
    equipped, moved, errors = [], [], []
    for row in triples:
        iid, h = str(row[0]), row[1]
        here = bool(row[2]) if len(row) > 2 else False
        if not here:
            try:
                await transfer(iid, h, False, char_id)
                moved.append(iid)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{iname(h)}：搬运失败（{exc}）")
                continue
        try:
            await equip(iid, h, char_id)
            equipped.append(iid)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{iname(h)}：装备失败（{exc}）")
    return {"ok": not errors, "equipped": len(equipped), "moved": len(moved),
            "errors": errors}


# ---------- 成就 ----------

TRIUMPH_ROOTS = (("1163735237", "成就"), ("3215903653", "传承成就"))


def _rec_done(pr: dict, rh) -> bool:
    st = (pr.get(str(rh)) or {}).get("state")
    return bool(st is not None and st & 1)


def _rec_progress(pr: dict, rh) -> tuple[int, int]:
    for o in ((pr.get(str(rh)) or {}).get("objectives") or []):
        cv, pg = o.get("completionValue") or 0, o.get("progress") or 0
        if cv > 0:
            return min(pg, cv), cv
    return (1, 1) if _rec_done(pr, rh) else (0, 0)


def _merged_records(prof: dict) -> dict:
    """profileRecords + 各角色 characterRecords（突袭/地牢凯旋只出现在角色里）"""
    pr = dict(((prof.get("profileRecords") or {}).get("data") or {}).get("records") or {})
    for ch in (((prof.get("characterRecords") or {}).get("data") or {}) or {}).values():
        pr.update((ch or {}).get("records") or {})
    return pr


def _walk_node(h: str, pr: dict, pnodes: dict, recs: dict, depth: int = 0) -> dict | None:
    nd = pnodes.get(str(h))
    if not nd:
        return None
    ch = nd.get("children") or {}
    node = {"h": str(h),
            "n": (nd.get("displayProperties") or {}).get("name") or "",
            "ic": _icon_url((nd.get("displayProperties") or {}).get("icon") or ""),
            "kids": [], "recs": [], "done": 0, "total": 0}
    for rc in ch.get("records") or []:
        rh = str(rc.get("recordHash"))
        d = recs.get(rh)
        if not d:
            continue
        st = (pr.get(rh) or {}).get("state")
        if st is not None and st & 16:      # 隐藏记录（未解锁的赛季内容）不显示
            continue
        pg, cv = _rec_progress(pr, rh)
        done = _rec_done(pr, rh)
        dp = d.get("displayProperties") or {}
        node["recs"].append({
            "h": rh, "n": dp.get("name") or "", "d": dp.get("description") or "",
            "ic": _icon_url(dp.get("icon") or ""),
            "done": done, "p": pg, "t": cv,
            "score": _score(d),
        })
    for c in ch.get("presentationNodes") or []:
        kid = _walk_node(str(c.get("presentationNodeHash")), pr, pnodes, recs, depth + 1)
        if kid:
            node["kids"].append(kid)
    node["done"] = sum((1 if r["done"] else 0) for r in node["recs"]) + \
        sum(k["done"] for k in node["kids"])
    node["total"] = len(node["recs"]) + sum(k["total"] for k in node["kids"])
    # 空节点（没有记录也没有子节点）不显示，避免一堆空分类
    if not node["recs"] and not node["kids"]:
        return None
    return node


async def triumphs() -> dict:
    """成就树 + 凯旋分"""
    mt, mid = await _me()
    r = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/", {"components": "900"})
    pr = _merged_records(r)
    prof = (r.get("profileRecords") or {}).get("data") or {}
    pnodes = _load("presentation_nodes.json")
    recs = _load("records.json")

    cats = []
    total_score = 0
    for root_h, root_name in TRIUMPH_ROOTS:
        root = _walk_node(root_h, pr, pnodes, recs)
        if root:
            root["n"] = root["n"] or root_name
            root["rootName"] = root_name
            cats.append(root)
    for rh, st in pr.items():
        d = recs.get(rh)
        if d and _rec_done(pr, rh):
            total_score += _score(d)
    return {
        "ok": True,
        "score": prof.get("score") or 0,
        "scoreEarned": total_score,
        "scoreTotal": prof.get("activeScore") or 0,
        "records": len(pr),
        "cats": cats,
    }
