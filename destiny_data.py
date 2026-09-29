"""Bungie API 数据层：查询玩家档案与历史统计"""
import asyncio
import datetime
import json
import os
import re
import sys
import time
import weakref

import httpx


def _idx_file(name: str) -> str:
    """manifest_index 数据文件定位：源码目录 → PyInstaller 打包资源 → 当前目录"""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest_index", name)
    if os.path.exists(here):
        return here
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "manifest_index", name)
    if os.path.exists(bundled):
        return bundled
    return os.path.join("manifest_index", name)


if not os.getenv("BUNGIE_API_KEY"):
    for _base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
                  os.path.dirname(sys.executable)):
        _env = os.path.join(_base, ".env")
        if os.path.exists(_env):
            for line in open(_env, encoding="utf-8"):
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.strip().partition("=")
                    os.environ.setdefault(k, v)
            break

API_KEY = os.getenv("BUNGIE_API_KEY", "")
BASE = "https://www.bungie.net"
HEADERS = {"X-API-Key": API_KEY}
# retries：Bungie/网络偶发断连时自动重试（此前一次抖动就会把整页打成 HTTP 500）
#
# 注意：exe 里同时在跑三个事件循环（主界面 8900 / HTTPS 回跳 8902 / QQ bot 8901），
# 而 httpx 的连接池会在建连时把 asyncio 原语（Event 等）绑到当时那个循环上。模块级
# 共用一个 AsyncClient 的话，第二个循环再取用池里的连接就会炸
# 「Event object ... is bound to a different event loop」——表现就是整页报错
# （实测 /rotation「本周轮换获取失败」）。所以按事件循环各持一个客户端。
_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = \
    weakref.WeakKeyDictionary()


def client() -> httpx.AsyncClient:
    """当前事件循环专用的 httpx 客户端（同一循环内复用连接池）"""
    loop = asyncio.get_running_loop()
    c = _CLIENTS.get(loop)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=15, follow_redirects=True,
                              transport=httpx.AsyncHTTPTransport(retries=2))
        _CLIENTS[loop] = c
    return c

CLASS_NAMES = {0: "泰坦", 1: "猎人", 2: "术士", 3: "守卫者"}
RACE_NAMES = {0: "人类", 1: "觉醒者", 2: "EXO"}


def fmt_code(code) -> str:
    """Bungie 名称编号统一补零到 4 位（202 → 0202）。

    接口返回的编号是整数，直接插进 f-string 会把 0202 显示成 202，
    绑定卡片写的是「木白#0202」、进度条却显示「木白#202」，两处对不上。
    统一走这个函数，卡片 / 进度条 / 绑定文件 / 联想框处处一致。
    """
    try:
        return f"{int(code):04d}"
    except (TypeError, ValueError):
        return str(code or "")

# 赛季定义（build 时从 Manifest DestinySeasonDefinition 拉取缓存）
try:
    SEASONS = json.load(open(_idx_file("seasons.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    SEASONS = []


def season_of(date_str: str) -> dict | None:
    """日期(YYYY-MM-DD) → 赛季定义"""
    for s in SEASONS:
        if s["start"][:10] <= date_str <= (s["end"] or "2999")[:10]:
            return s
    return None


def current_season() -> dict | None:
    import datetime
    today = datetime.date.today().isoformat()
    cur = [s for s in SEASONS if s["start"][:10] <= today]
    return cur[-1] if cur else None


async def resolve_member(name: str):
    """玩家名#编号 → dict(mtype, mid, display, code)；精确查不到时走模糊搜索兜底"""
    if "#" not in name:
        return None
    fname, _, code = name.partition("#")
    code = int(code)
    # 精确查询（对大小写敏感）
    r = await client().post(
        "/Platform/Destiny2/SearchDestinyPlayerByBungieName/-1/",
        json={"displayName": fname, "displayNameCode": code},
    )
    resp = _parse(r)
    cands = resp.get("Response") or []
    if not cands:
        # 模糊搜索兜底（大小写不敏感），再按编号精确匹配
        r = await client().get(f"/Platform/Destiny2/SearchDestinyPlayers/-1/{fname}/")
        resp = _parse(r)
        for p in resp.get("Response") or []:
            if p.get("bungieGlobalDisplayNameCode") == code:
                cands = [p]
                break
    # 跨存档玩家：主平台(crossSaveOverride)那条才是有效数据；无跨存档取第一条
    best = next((p for p in cands
                 if p.get("crossSaveOverride") and p["membershipType"] == p["crossSaveOverride"]),
                cands[0] if cands else None)
    if best:
        return {"mtype": best.get("crossSaveOverride") or best["membershipType"],
                "mid": best["membershipId"], "display": best["bungieGlobalDisplayName"],
                "code": best["bungieGlobalDisplayNameCode"],
                "icon": BASE + best["iconPath"] if best.get("iconPath") else ""}
    return None


def _parse(r) -> dict:
    return json.loads(r.content.decode("utf-8-sig"))


async def get_profile(mtype: int, mid: str) -> dict:
    r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/", params={"components": "100,200"})
    resp = r.json()
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "Bungie API 错误"))
    return resp["Response"]


async def char_stats(mtype: int, mid: str, char_id: str, groups: str) -> dict:
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{char_id}/Stats/",
        params={"groups": groups},
    )
    resp = r.json()
    if resp.get("ErrorCode") != 1:
        return {}
    return resp["Response"]


def _sum(stats_list: list[dict], mode: str) -> dict:
    """把多个角色的同一模式统计合并求和"""
    keys = ("kills", "deaths", "assists", "activitiesEntered", "activitiesWon",
            "killsDeathsRatio", "killsDeathsAssists", "precisionKills", "winRate")
    out = {k: 0.0 for k in keys}
    for s in stats_list:
        at = s.get(mode, {}).get("allTime", {})
        for k in keys:
            if k in at:
                v = at[k]["basic"]["value"]
                out[k] = v if k in ("killsDeathsRatio", "killsDeathsAssists", "winRate") else out[k] + v
    if out["deaths"]:
        out["kd"] = out["kills"] / out["deaths"]
    else:
        out["kd"] = out.get("killsDeathsRatio", 0.0)
    return out


async def full_report(name: str) -> dict:
    """玩家全量数据：档案 + 各角色 PVP/PVE/智谋 合并统计"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    if not chars:
        raise LookupError(f"{member['display']} 档案下没有角色")

    chars_meta, pvp_list, pve_list, gmb_list = [], [], [], []
    for cid, c in chars.items():
        chars_meta.append({
            "id": cid, "class": CLASS_NAMES.get(c["classType"], "?"),
            "race": RACE_NAMES.get(c["raceType"], "?"), "light": c["light"],
            "playtime_min": int(c.get("minutesPlayedTotal", 0)),
            "last_played": c["dateLastPlayed"][:16].replace("T", " "),
            "emblem": BASE + c.get("emblemPath", ""),
            "emblem_bg": BASE + c.get("emblemBackgroundPath", ""),
        })
        st = await char_stats(mtype, mid, cid, "101,103,104")
        pvp_list.append(st); pve_list.append(st); gmb_list.append(st)

    # 智谋：官方聚合接口已下线，从对局历史聚合（跨角色，去重）
    gkilled = gdeaths = gcount = gwins = 0
    seen_g = set()
    for cid in chars:
        for m in await activity_history(mtype, mid, cid, 63, count=100):
            key = m["instance"] or f"{m['ref']}{m['period']}"
            if key in seen_g or not m["completed"]:
                continue
            seen_g.add(key)
            gkilled += m["kills"]; gdeaths += m["deaths"]; gcount += 1
            gwins += 1 if m["win"] else 0
    gambit = {"kills": gkilled, "deaths": gdeaths, "kd": (gkilled / gdeaths) if gdeaths else 0.0,
              "activitiesEntered": gcount, "activitiesWon": gwins,
              "winRate": (gwins / gcount * 100) if gcount else 0.0}

    chars_meta.sort(key=lambda c: -c["light"])
    total_pt = sum(c["playtime_min"] for c in chars_meta)
    return {
        "display": f"{member['display']}#{fmt_code(member['code'])}",
        "max_light": chars_meta[0]["light"],
        "total_playtime": total_pt,
        "chars": chars_meta,
        "pvp": _sum(pvp_list, "allPvP"),
        "pve": _sum(pve_list, "allPvE"),
        "gambit": gambit,
    }


def fmt_hours(minutes: float) -> str:
    return f"{minutes / 60:,.0f} 小时"


def esc_err(exc: BaseException) -> str:
    """把异常整理成一行可读文本（错误页用，不暴露内部堆栈）"""
    txt = f"{type(exc).__name__}: {exc}".replace("<", "&lt;").replace(">", "&gt;")
    return txt[:200]


async def lifetime_stats(name: str, group: str = "allPvP") -> dict:
    """Bungie 官方**生涯**统计（跨角色求和）；group: allPvP / allPvE / gambit"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    lst = [await char_stats(mtype, mid, cid, "101,103,104") for cid in chars]
    return _sum(lst, group)


# ---------- Manifest 索引 ----------
_weapons = json.load(open(_idx_file("weapons.json"), encoding="utf-8"))
_weapons_full = json.load(open(_idx_file("weapons_full.json"), encoding="utf-8"))
_perks = json.load(open(_idx_file("perks.json"), encoding="utf-8"))
try:
    _perk_ci = json.load(open(_idx_file("perk_ci.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    _perk_ci = {}
_activities = json.load(open(_idx_file("activities.json"), encoding="utf-8"))
_records = json.load(open(_idx_file("records.json"), encoding="utf-8"))
_pnodes = json.load(open(_idx_file("presentation_nodes.json"), encoding="utf-8"))
try:
    _modes = json.load(open(_idx_file("modes.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001  没建过索引时不至于炸掉整站
    _modes = {}
try:  # 锻造图案的分组表（组名/顺序/归属 1:1 对照小日向锻造页），由 build_pattern_groups.py 生成
    _pat_groups = json.load(open(_idx_file("pattern_groups.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001  没有就退回按槽位分组
    _pat_groups = {}

# modeType → {name, cat(1=PvE 2=PvP 3=智谋), parents[], order, agg}
MODES: dict[int, dict] = {int(k): v for k, v in _modes.items()}
COMPETITIVE_CATS = (2, 3)
NON_COMPLETABLE = {6}  # 探索/巡逻：没有"完成"一说

_MODE_ANC: dict[int, frozenset] = {}


def mode_ancestors(mt: int) -> frozenset:
    """某模式的所有祖先模式（用于判断谁更具体）"""
    if mt not in _MODE_ANC:
        out, stack = set(), list(MODES.get(mt, {}).get("parents") or [])
        while stack:
            p = stack.pop()
            if p in out:
                continue
            out.add(p)
            stack += list(MODES.get(p, {}).get("parents") or [])
        _MODE_ANC[mt] = frozenset(out)
    return _MODE_ANC[mt]


def mode_of(modes: list) -> int | None:
    """从 activityDetails.modes 里挑最具体的模式。

    先剔除"是列表里其他模式祖先"的聚合项（如 熔炉竞技场→奥斯里斯试炼），
    再让具体玩法压过聚合玩法（如 生存模式 压过 多人竞技PvP）。
    """
    cand = [m for m in (modes or []) if m in MODES]
    if not cand:
        return None
    anc: set = set()
    for m in cand:
        anc |= mode_ancestors(m)
    leaf = [m for m in cand if m not in anc] or cand
    nonagg = [m for m in leaf if not MODES[m]["agg"]]
    return max(nonagg or leaf, key=lambda m: MODES[m]["order"])


def _match_mode(m: dict) -> dict:
    mt = mode_of(m.get("modes"))
    info = MODES.get(mt) if mt is not None else None
    return {"mode": mt,
            "mode_name": info["name"] if info else "",
            "mode_cat": info["cat"] if info else 0}


def mode_tags(modes: list) -> str:
    return ", ".join(MODES[m]["name"] for m in (modes or []) if m in MODES)


def suggest_weapons(q: str, limit: int = 8) -> list[dict]:
    q = q.lower().strip()
    out = [w for w in _weapons_full.values() if q in w["name"].lower()]
    out.sort(key=lambda w: (not w["name"].lower().startswith(q), w["name"]))
    return out[:limit]


def search_weapons_full(q: str, limit: int = 24) -> list[dict]:
    q = q.lower().strip()
    out = [dict(w, hash=h) for h, w in _weapons_full.items() if q in w["name"].lower()]
    out.sort(key=lambda w: (not w["name"].lower().startswith(q), w["name"]))
    return out[:limit]


def search_weapons(q: str, limit: int = 12) -> list[dict]:
    q = q.lower().strip()
    out = []
    for h, w in _weapons.items():
        n = w["name"].lower()
        if n == q:
            out.insert(0, {"hash": h, **w})
        elif q in n:
            out.append({"hash": h, **w})
    return out[:limit]


def weapon_detail(hash: str) -> dict | None:
    return _weapons_full.get(str(hash))


def search_perks(q: str, limit: int = 20) -> list[dict]:
    q = q.lower().strip()
    exact, part = [], []
    for h, p in _perks.items():
        n = p["name"].lower()
        if n == q:
            exact.append({"hash": h, **p})
        elif q in n:
            part.append({"hash": h, **p})
    out = (exact + part)[:limit]
    for p in out:  # 附上社区数值/说明
        pc = _perk_ci.get(p["hash"])
        if pc:
            if pc.get("ci"):
                p["ci"] = pc["ci"]
            if pc.get("stats"):
                p["stats"] = pc["stats"]
    return out


def activity_name(ref_id) -> dict:
    return _activities.get(str(ref_id), {"name": f"未知活动 {ref_id}", "icon": "", "pgcr": ""})


async def activity_history(mtype: int, mid: str, cid: str, mode: int, count: int = 200, page: int = 0) -> list[dict]:
    """对局历史（mode: 5=所有PVP 63=智谋 7=所有PVE 0=全部活动）"""
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/Activities/",
        params={"mode": mode, "count": count, "page": page} if mode else
               {"count": count, "page": page},
    )
    resp = r.json()
    acts = (resp.get("Response") or {}).get("activities") or []
    out = []
    for a in acts:
        v = a.get("values", {})
        ad = a.get("activityDetails", {})
        ref = ad.get("referenceId", 0)
        info = activity_name(ref)
        completed = int(v.get("completed", {}).get("basic", {}).get("value", 0)) == 1
        # 只有 PvP/智谋这类"竞技"活动才有胜负概念；PvE 用"通关/未通关"
        mi = _match_mode(ad)
        competitive = mi["mode_cat"] in COMPETITIVE_CATS
        out.append({
            "ref": ref,
            "instance": ad.get("instanceId", ""),
            "name": info["name"],
            "pgcr": info["pgcr"],
            "modes": ad.get("modes", []),
            "mode": mi["mode"], "mode_name": mi["mode_name"],
            "mode_cat": mi["mode_cat"], "competitive": competitive,
            "period": a.get("period", "")[:16].replace("T", " "),
            "kills": int(v.get("kills", {}).get("basic", {}).get("value", 0)),
            "deaths": int(v.get("deaths", {}).get("basic", {}).get("value", 0)),
            "assists": int(v.get("assists", {}).get("basic", {}).get("value", 0)),
            "kd": v.get("killsDeathsRatio", {}).get("basic", {}).get("value", 0.0),
            "duration": int(v.get("activityDurationSeconds", {}).get("basic", {}).get("value", 0)),
            "completed": completed,
            "win": competitive and completed and v.get("standing", {}).get("basic", {}).get("value", 1) == 0,
            "score": int(v.get("score", {}).get("basic", {}).get("value", 0)),
            "eff": v.get("efficiency", {}).get("basic", {}).get("value", 0.0),
            "team_score": int(v.get("teamScore", {}).get("basic", {}).get("value", 0)),
            "player_count": int(v.get("playerCount", {}).get("basic", {}).get("value", 0)),
        })
    return out


def weapon_usage(entry: dict, limit: int = 8) -> list[dict]:
    """PGCR 单玩家的武器使用明细"""
    ext = entry.get("extended", {}) or {}
    out = []
    for w in ext.get("weapons", []):
        h = str(w.get("referenceId", ""))
        wd = _weapons_full.get(h) or _weapons.get(h) or {}
        kills = int(w.get("values", {}).get("uniqueWeaponKills", {}).get("basic", {}).get("value", 0))
        if not wd and kills == 0:
            continue
        out.append({"name": wd.get("name", "未知武器"), "icon": wd.get("icon", ""),
                    "hash": h, "kills": kills})
    out.sort(key=lambda x: -x["kills"])
    return out[:limit]


async def player_suggest(q: str, limit: int = 6) -> list[dict]:
    """Bungie 已关闭免鉴权的部分搜索：只支持 名字#编号 精确联想"""
    q = q.strip()
    if "#" not in q:
        return []
    try:
        member = await resolve_member(q)
    except Exception:
        return []
    if not member:
        return []
    # 徽标优先级：角色徽章 > Bungie 头像 > 平台图标（member.icon，如 steamLogo.png）
    icon = member.get("icon", "")
    try:
        r = await client().get(f"/Platform/Destiny2/{member['mtype']}/Profile/{member['mid']}/",
                             params={"components": "100,200"})
        resp = _parse(r)["Response"]
        chars = (resp.get("characters") or {}).get("data") or {}
        member_icon = (resp.get("profile") or {}).get("data", {}).get("userInfo", {}).get("iconPath") or ""
        best = None
        for c in chars.values():
            if c.get("emblemPath") and (best is None or c.get("dateLastPlayed", "") > best.get("dateLastPlayed", "")):
                best = c
        if best:
            icon = BASE + best["emblemPath"]
        elif member_icon:
            icon = BASE + member_icon
    except Exception:  # noqa: BLE001
        pass
    return [{"n": member["display"], "code": fmt_code(member["code"]), "icon": icon}]


async def get_pgcr(instance_id: str) -> dict:
    """对局详情：全场玩家数据"""
    r = await client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{instance_id}/")
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        return {}
    d = resp["Response"]
    entries = []
    for e in d.get("entries", []):
        p = e.get("player", {})
        info = p.get("destinyUserInfo", {})
        v = e.get("values", {})
        entries.append({
            "name": f"{info.get('bungieGlobalDisplayName', info.get('displayName', '?'))}#{fmt_code(info.get('bungieGlobalDisplayNameCode', ''))}",
            "mid": info.get("membershipId", ""),
            "class": {"Titan": "泰坦", "Hunter": "猎人", "Warlock": "术士"}.get(p.get("characterClass"), "未知"),
            "light": p.get("lightLevel", 0),
            "emblem": BASE + info.get("iconPath", "") if info.get("iconPath") else "",
            "kills": int(v.get("kills", {}).get("basic", {}).get("value", 0)),
            "deaths": int(v.get("deaths", {}).get("basic", {}).get("value", 0)),
            "assists": int(v.get("assists", {}).get("basic", {}).get("value", 0)),
            "kd": v.get("killsDeathsRatio", {}).get("basic", {}).get("value", 0.0),
            "score": int(v.get("score", {}).get("basic", {}).get("value", 0)),
            "completed": v.get("completed", {}).get("basic", {}).get("value", 0) == 1,
            "team": v.get("team", {}).get("basic", {}).get("displayValue", ""),
            "standing_text": {"Victory": "胜利", "Defeat": "战败"}.get(
                v.get("standing", {}).get("basic", {}).get("displayValue", ""), ""),
            "weapons": weapon_usage(e),
        })
    entries.sort(key=lambda x: -x["score"])
    total_kills = sum(e["kills"] for e in entries) or 1
    for e in entries:
        e["kill_share"] = e["kills"] / total_kills * 100
    ad = d.get("activityDetails", {}) or {}
    return {
        "name": activity_name(ad.get("referenceId", 0))["name"],
        "period": (d.get("period") or "")[:16].replace("T", " "),
        "entries": entries,
        "instance": instance_id,
    }


_DIFF_TAGS = {"标准", "普通", "大师", "永恒", "自定义", "传说", "专家", "高级",
              "宗师", "英雄", "巅峰", "竞赛", "私人", "得心应手", "猛攻"}


def split_activity(name: str) -> tuple[str, str]:
    """'晚星之主: 大师' → ('晚星之主', '大师')；非难度后缀原样返回，diff 为空串"""
    if ":" in name:
        base, _, tag = name.rpartition(":")
        tag = tag.strip()
        if tag in _DIFF_TAGS and base.strip():
            return base.strip(), tag
    return name, ""


async def raid_report(name: str, mode: int) -> dict:
    """Raid(4)/地牢(82) 报告：跨角色合并对局，按副本分组统计（标准与大师各成一组）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})

    seen, matches = set(), []
    for cid in chars:
        # 翻页拿全：只取最近 100 场会让"去年打的大师"排不进窗口，看起来像记录缺失
        page = 0
        while page < 3:
            acts = await activity_history(mtype, mid, cid, mode, count=250, page=page)
            for m in acts:
                key = m["instance"] or f"{m['ref']}{m['period']}"
                if key not in seen:
                    seen.add(key)
                    matches.append(m)
            if len(acts) < 250:
                break
            page += 1
    matches.sort(key=lambda m: m["period"], reverse=True)

    # 大师单独成组：标准/普通与大师的 无暇/单人/双人/三人 口径不该混在一起算
    groups: dict[tuple[str, bool], dict] = {}
    for m in matches:
        base, diff = split_activity(m["name"])
        m["base"], m["diff"] = base, diff
        is_master = diff == "大师"
        g = groups.setdefault((base, is_master), {
            "name": base, "master_mode": is_master, "ref": m["ref"], "pgcr": m["pgcr"],
            "plays": 0, "clears": 0, "best": None, "last": "",
            "flawless": 0, "solo": 0, "duo": 0, "trio": 0,
            "solo_fl": 0, "duo_fl": 0, "trio_fl": 0, "master": 0, "diffs": [],
        })
        if diff and diff not in g["diffs"]:
            g["diffs"].append(diff)
        g["plays"] += 1  # 参与次数：含没打完的（中途退、卡机制、只打到一半）
        if m["completed"]:
            g["clears"] += 1
            pc = m["player_count"]
            fl = m["deaths"] == 0
            if fl:
                g["flawless"] += 1
            if pc:
                if pc == 1:
                    g["solo"] += 1
                if pc <= 2:
                    g["duo"] += 1
                if pc <= 3:
                    g["trio"] += 1
                if fl:
                    if pc == 1:
                        g["solo_fl"] += 1
                    if pc <= 2:
                        g["duo_fl"] += 1
                    if pc <= 3:
                        g["trio_fl"] += 1
            if is_master:
                g["master"] += 1
            if g["best"] is None or m["duration"] < g["best"]:
                g["best"] = m["duration"]
            g["last"] = m["period"]
    done = [m for m in matches if m["completed"]]
    std = sorted((g for g in groups.values() if not g["master_mode"]),
                 key=lambda g: (-g["clears"], -g["plays"]))
    mst = sorted((g for g in groups.values() if g["master_mode"]),
                 key=lambda g: (-g["clears"], -g["plays"]))
    return {
        "display": f"{member['display']}#{fmt_code(member['code'])}",
        "total_clears": len(done),
        "total_plays": len(matches),
        "flawless": sum(1 for m in done if m["deaths"] == 0),
        "solo_fl": sum(1 for m in done if m["deaths"] == 0 and m["player_count"] == 1),
        "duo_fl": sum(1 for m in done if m["deaths"] == 0 and m["player_count"] == 2),
        "trio_fl": sum(1 for m in done if m["deaths"] == 0 and 0 < m["player_count"] <= 3),
        "master": sum(1 for m in done if m["diff"] == "大师"),
        "matches": matches,
        "raids": std,
        "raids_master": mst,
    }


async def history_report(name: str, per_char: int = 50) -> dict:
    """全模式最近对局流（合并所有角色）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    matches = []
    for cid in chars:
        matches += await activity_history(mtype, mid, cid, 0, count=per_char)
    matches.sort(key=lambda m: m["period"], reverse=True)
    return {"display": f"{member['display']}#{fmt_code(member['code'])}", "matches": matches}


def filter_matches(matches: list[dict], month: str = "", base: str = "",
                   diff: str = "") -> list[dict]:
    out = matches
    if base:
        out = [m for m in out if m.get("base") == base]
    if diff:
        out = [m for m in out if m.get("diff") == diff]
    if month:
        out = [m for m in out if m["period"].startswith(month)]
    return out


# ---------- 后台任务（PVP 生涯武器，带进度） ----------
JOBS: dict[str, dict] = {}
_JOB_KEEP = 80


def _prune_jobs():
    """任务只增不减会一直吃内存；留最近 _JOB_KEEP 条，已完成/失败的优先清"""
    # 去重映射指向的任务已经没了就一起清，否则这个 dict 会随「一共查过多少人」一直涨
    for k, v in list(_JOB_DEDUP.items()):
        if v not in JOBS:
            _JOB_DEDUP.pop(k, None)
    if len(JOBS) <= _JOB_KEEP:
        return
    for k in list(JOBS):
        if len(JOBS) <= _JOB_KEEP:
            break
        if JOBS[k].get("status") in ("done", "error"):
            JOBS.pop(k, None)


def job_snapshot() -> list:
    """给面板用的后台任务进度快照；运行中 → 排队中 → 已结束（新任务在前）"""
    order = {"running": 0, "queued": 1}
    now = time.time()
    out = []
    for jid, j in JOBS.items():
        total = j.get("total") or 0
        done = j.get("done") or 0
        status = j.get("status") or "?"
        ts = j.get("ts") or 0
        out.append({"id": jid, "label": j.get("label") or "任务",
                    "kind": j.get("kind") or "", "who": j.get("who") or "网页",
                    "name": j.get("name") or "", "status": status,
                    "done": done, "total": total,
                    "queue_pos": queue_position(jid) if status == "queued" else 0,
                    "pct": int(done * 100 / total) if total else 0,
                    # 面板显示发起时刻与已耗时（请求时间一目了然）
                    "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
                    "date": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
                    "reused": j.get("reused") or 0,
                    "reused_by": j.get("reused_by") or [],
                    "elapsed": int(now - ts) if ts and status in ("running", "queued") else 0})
    # 同一状态内按发起时间倒序（最新的排最上面），面板翻页时先看到刚发的
    out.sort(key=lambda x: (order.get(x["status"], 9), -_job_ts(x["id"])))
    return out


def _job_ts(jid: str) -> float:
    return float((JOBS.get(jid) or {}).get("ts") or 0)


# 生涯武器 / 热力图都要逐场拉 PGCR，多个一起跑会被 Bungie 限流拖慢，整体反而更慢，
# 所以排成一条队逐个跑；排队中的任务能查到自己是第几位。
_JOB_QUEUE: list[tuple] = []  # [(jid, factory), ...] 等待中（不含正在跑的）
_JOB_RUNNING: str | None = None


def queue_position(jid: str) -> int:
    """0 = 正在跑 / 已结束；>0 = 在等待队列里的位次（1 表示下一个就轮到）"""
    if jid == _JOB_RUNNING:
        return 0
    for i, (qid, _) in enumerate(_JOB_QUEUE):
        if qid == jid:
            return i + 1
    return 0


def _pump_jobs():
    """队列空闲就取下一个开跑（串行：同一时间只有一个重任务在跑）"""
    global _JOB_RUNNING
    if _JOB_RUNNING is not None or not _JOB_QUEUE:
        return
    jid, factory = _JOB_QUEUE.pop(0)
    _JOB_RUNNING = jid
    JOBS.get(jid, {})["status"] = "running"
    asyncio.get_event_loop().create_task(_run_queued(jid, factory))


async def _run_queued(jid: str, factory):
    global _JOB_RUNNING
    try:
        await factory()
    except Exception as exc:  # noqa: BLE001  兜底：别让队列卡死
        JOBS.get(jid, {}).update(status="error", error=str(exc))
    finally:
        JOBS.get(jid, {})["ended"] = time.time()  # 复用窗口从这个时刻算起
        _JOB_RUNNING = None
        _pump_jobs()


def _enqueue_job(jid: str, factory):
    _JOB_QUEUE.append((jid, factory))
    _pump_jobs()


# 同一个「谁 + 查什么」已经在跑（或刚跑完）时复用那一个任务，不再排第二遍：
# 群里和私聊同时发、或者连点两下，不会把同一份全量翻页重复拉一遍。
_JOB_DEDUP: dict[str, str] = {}   # 去重键 → jid
_JOB_REUSE_SEC = 120              # 已结束的任务在这个秒数内仍复用（刚出完图再来一次不重跑）


def _reuse_job(key: str) -> str | None:
    """同一键的任务在跑 / 刚结束 → 返回它的 jid；过期或已被清理则返回 None"""
    jid = _JOB_DEDUP.get(key)
    if not jid:
        return None
    j = JOBS.get(jid)
    if not j:  # 跟着 _prune_jobs 一起没了
        _JOB_DEDUP.pop(key, None)
        return None
    st = j.get("status")
    if st in ("queued", "running"):
        return jid
    if st == "done":
        # 正常路径上 ended 由 _run_queued 收尾时打；兜底取 ts 是为了盖住
        # 「工厂函数已把 status 置成 done、收尾还没跑到」那一瞬（也正好是「刚跑完」那一下）。
        # 长任务取 ts 会算出更久的耗时 → 不复用 → 重跑，偏保守，不会把旧结果当新数据发出去。
        ended = j.get("ended") or j.get("ts") or 0
        return jid if time.time() - ended <= _JOB_REUSE_SEC else None
    return None


def _mark_reused(jid: str, who: str) -> None:
    """记一笔复用（面板上能看到「复用 N 次 / 谁复用的」，方便解释为什么没多出一条任务）"""
    j = JOBS.get(jid)
    if not j:
        return
    j["reused"] = (j.get("reused") or 0) + 1
    if who:
        by = j.setdefault("reused_by", [])
        if who not in by:
            by.append(who)


def _register_job(key: str, jid: str, meta: dict) -> None:
    _prune_jobs()
    JOBS[jid] = meta
    _JOB_DEDUP[key] = jid


def job_shared(jid: str) -> bool:
    """这个 jid 不是现场算出来的（复用了别人的任务，或直接命中了热力图结果缓存）"""
    j = JOBS.get(jid) or {}
    return bool(j.get("reused") or j.get("cached"))


def job_reuse_window() -> int:
    """复用窗口秒数（提示语里告诉用户多久之后可以强制重跑）"""
    return _JOB_REUSE_SEC


# 单场对局里"某玩家的武器/技能击杀明细"缓存：换范围（全生涯 ↔ 某赛季）重跑时复用，
# 不必把同一场 PGCR 再拉一遍。key: instance_id → {membership_id: 明细}
# 还会落盘（pvp_weapon_cache.json，放 exe/项目同目录），所以第二次查基本是秒开。
# PVE 用的是同一份 PGCR 解析，所以 PVP/PVE 共用这个缓存和这个文件（内容完全一样）。
_PVP_MATCH_CACHE: dict[str, dict] = {}
_PVP_CACHE_MAX = 20000
_PVP_CACHE_FILE = "pvp_weapon_cache.json"
_pvp_cache_ready = False
# PGCR 单发延迟约 2 秒（Bungie 服务端就慢），16 路并发 ≈ 10+ req/s，远低于 25/s 限流
_PVP_CONCURRENCY = 16
PVP_MATCH_CAP = 2000      # 逐场拉 PGCR 的上限，防止十年老号把任务拖成几十分钟
# PVE 场次比 PVP 多一个数量级（一个赛季通常几百场），默认只统计单赛季，全生涯才可能吃满上限
PVE_MATCH_CAP = 3000
# 探索/巡逻（mode 6）没有实质击杀，实测还偶发没有武器明细，统计里排除（PVE 通关率也已排除它）
_PVE_SKIP_MODES = frozenset({6})


def _writable_path(name: str) -> str:
    """可写缓存文件位置：打包后放 exe 同目录（和 user_bindings.json 一起），源码运行放项目目录"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _load_pvp_cache():
    global _pvp_cache_ready
    if _pvp_cache_ready:
        return
    _pvp_cache_ready = True
    try:
        with open(_writable_path(_PVP_CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _PVP_MATCH_CACHE.update(data)
    except Exception:  # noqa: BLE001 首次运行/文件损坏都不影响统计
        pass


def _save_pvp_cache():
    try:
        with open(_writable_path(_PVP_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(_PVP_MATCH_CACHE, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重拉
        pass


# 生涯武器"汇总结果"缓存：整份排名 + 上次覆盖到的日期。
# 同一玩家同一范围再查时，只补拉覆盖日期之后的新对局，累加进缓存的排名即可，
# 不必把整段历史重新枚举一遍（枚举本身要翻很多页活动历史，是全生涯任务里的大头）。
# key: f"{membership_id}|{kind}|{scope}"；scope_since/until 用来判断范围有没有变，
# 变了就整段重算（例如赛季滚动导致"当前赛季"窗口改变）。
_AGG_CACHE: dict[str, dict] = {}
_AGG_CACHE_FILE = "weapon_agg_cache.json"
_agg_cache_ready = False


def _load_agg_cache():
    global _agg_cache_ready
    if _agg_cache_ready:
        return
    _agg_cache_ready = True
    try:
        with open(_writable_path(_AGG_CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _AGG_CACHE.update(data)
    except Exception:  # noqa: BLE001 首次运行/文件损坏都不影响统计
        pass


def _save_agg_cache():
    try:
        with open(_writable_path(_AGG_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(_AGG_CACHE, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重算
        pass


def bind_path() -> str:
    """绑定文件位置（和缓存一样放 exe / 项目同目录，程序与面板读写同一份）"""
    return _writable_path("user_bindings.json")


def bindings_snapshot() -> list[dict]:
    """面板用：QQ → 绑定账号 一览（编号已补零，和卡片/进度条一致）"""
    try:
        with open(bind_path(), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(d, dict):
        return []
    return [{"qq": str(k), "name": str(v)} for k, v in sorted(d.items(), key=lambda x: str(x[0]))]


def season_by_key(key: str) -> dict | None:
    """'s27' / '27' / '赛季27' → 赛季定义；'current' / '本赛季' → 当前赛季；
    'all' / '' → None（全生涯）"""
    k = (key or "").strip().lower()
    if k in ("current", "本赛季", "当前赛季", "本季"):
        return current_season()
    k = k.removeprefix("赛季").removeprefix("s").strip()
    if not k.isdigit():
        return None
    return next((s for s in SEASONS if str(s.get("number")) == k), None)


def scope_window(scope: str) -> tuple[str, str, str]:
    """统计范围 → (since, until, 显示名)；scope 为空或 all 表示全生涯"""
    s = season_by_key(scope)
    if not s:
        return "", "", "全生涯"
    label = f"S{s['number']} {s.get('name', '')}".strip()
    if (scope or "").strip().lower() in ("current", "本赛季", "当前赛季", "本季"):
        label += "（当前赛季）"
    return s["start"][:10], (s.get("end") or "2999")[:10], label


def _basic(values: dict, key: str) -> int:
    return int((values.get(key) or {}).get("basic", {}).get("value", 0) or 0)


async def pvp_match_contribution(instance: str, mid: str) -> dict | None:
    """单场对局里某玩家的武器/技能击杀明细（带缓存，供生涯武器任务复用）

    取的是 PGCR 原始 extended 数据：武器区给精准击杀，entry 区给近战/手雷/大招/技能击杀
    （get_pgcr 的裁剪版没有这些字段，所以这里单独拉一次并只留需要的部分）
    """
    hit = _PVP_MATCH_CACHE.get(instance, {}).get(mid)
    if hit is None:
        _load_pvp_cache()  # 首次调用时才读盘，避免 import 阶段做 IO
        hit = _PVP_MATCH_CACHE.get(instance, {}).get(mid)
    if hit is not None:
        return hit
    r = await client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{instance}/")
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        return None  # 包括限流/已删除对局，交给调用方重试或跳过
    me = None
    for e in resp["Response"].get("entries", []):
        info = e.get("player", {}).get("destinyUserInfo", {})
        if info.get("membershipId") == mid:
            me = e
            break
    if not me:
        return None
    ev = (me.get("extended") or {}).get("values") or {}
    weapons = []
    for w in (me.get("extended") or {}).get("weapons") or []:
        v = w.get("values") or {}
        h = str(w.get("referenceId", ""))
        wd = _weapons_full.get(h) or _weapons.get(h) or {}
        kills = _basic(v, "uniqueWeaponKills")
        if not kills and not wd:
            continue
        weapons.append({"name": wd.get("name", "未知武器"), "icon": wd.get("icon", ""),
                        "type": wd.get("type", ""), "hash": h,
                        "kills": kills, "precision": _basic(v, "uniqueWeaponPrecisionKills")})
    out = {"kills": _basic(me.get("values") or {}, "kills"),
           "precision": _basic(ev, "precisionKills"),
           "melee": _basic(ev, "weaponKillsMelee"),
           "grenade": _basic(ev, "weaponKillsGrenade"),
           "super": _basic(ev, "weaponKillsSuper"),
           "ability": _basic(ev, "weaponKillsAbility"),
           "weapons": weapons}
    if len(_PVP_MATCH_CACHE) >= _PVP_CACHE_MAX:  # 满了丢最早的四分之一，够用就行
        for k in list(_PVP_MATCH_CACHE)[: _PVP_CACHE_MAX // 4]:
            _PVP_MATCH_CACHE.pop(k, None)
    _PVP_MATCH_CACHE.setdefault(instance, {})[mid] = out
    return out


async def _collect_matches(mtype: int, mid: str, chars: list[str], mode: int,
                           since: str, until: str, cap: int,
                           skip_modes: frozenset = frozenset()) -> list[dict]:
    """收集对局（跨角色去重，新→旧）；since/until 为空串表示不限时间

    mode: 5=所有PVP 7=所有PVE；skip_modes 里的具体玩法会被丢掉
    """
    seen, matches = set(), []
    for cid in chars:
        page = 0
        while page < 60 and len(matches) < cap:  # 60页×250 ≈ 1.5万场/角色的兜底
            acts = await activity_history(mtype, mid, cid, mode, count=250, page=page)
            if not acts:
                break
            stop = False
            for m in acts:
                d = m["period"][:10]
                if not d:
                    continue
                if since and d < since:  # 本页已翻到范围之前（新→旧），不必再翻
                    stop = True
                    break
                if until and d > until:  # 比范围更晚（跨赛季边界的少量场次），跳过
                    continue
                if m["mode"] in skip_modes:
                    continue
                if m["instance"] and m["instance"] not in seen:
                    seen.add(m["instance"])
                    matches.append(m)
                    if len(matches) >= cap:
                        stop = True
                        break
            if stop or len(acts) < 250:
                break
            page += 1
    matches.sort(key=lambda m: m["period"], reverse=True)
    return matches


async def _start_weapon_job(name: str, scope: str, kind: str, mode: int,
                            cap: int, skip_modes: frozenset,
                            who: str = "") -> str | None:
    """生涯武器后台任务通用入口；kind: 'pvp' / 'pve'（只影响卡片标题与标签）"""
    member = await resolve_member(name)
    if not member:
        return None
    mtype, mid = member["mtype"], member["mid"]
    since, until, label = scope_window(scope)
    # 去重键用解析后的时间窗而不是 scope 原文：'27' / 's27' / '赛季27' 是同一个窗口，
    # 不该因为写法不同就跑两遍
    key = f"{mtype}:{mid}:wp:{kind}:{since}:{until}"
    hit = _reuse_job(key)
    if hit:  # 命中就不必再拉 profile 了，直接把人带去等已有任务
        _mark_reused(hit, who)
        return hit
    profile = await get_profile(mtype, mid)
    chars = list(profile.get("characters", {}).get("data", {}))
    jid = f"{mid}_wp{kind}_{len(JOBS)}"
    _register_job(key, jid, {
        "done": 0, "total": 0, "status": "queued", "name":
        f"{member['display']}#{fmt_code(member['code'])}", "result": None,
        "kind": kind, "who": who or "网页", "ts": time.time(),
        "label": ("PVE 生涯武器" if kind == "pve" else "PVP 生涯武器") + f"（{label}）"})
    _enqueue_job(jid, lambda: _run_weapon_job(
        jid, mtype, mid, chars, since, until, scope or "all", label,
        kind, mode, cap, skip_modes))
    return jid


async def start_pvp_weapons(name: str, scope: str = "all", who: str = "") -> str | None:
    """PVP 生涯武器后台任务；scope: 'all'=全生涯，'s27'/'27'/'赛季27'=只统计该赛季"""
    return await _start_weapon_job(name, scope, "pvp", 5, PVP_MATCH_CAP, frozenset(), who)


async def start_pve_weapons(name: str, scope: str = "current", who: str = "") -> str | None:
    """PVE 生涯武器后台任务；scope 默认 'current'=当前赛季（全生涯 PVE 场次太多，要显式指定）"""
    return await _start_weapon_job(name, scope, "pve", 7, PVE_MATCH_CAP, _PVE_SKIP_MODES, who)


async def _run_weapon_job(jid: str, mtype: int, mid: str, chars: list[str],
                          since: str, until: str, scope: str, label: str,
                          kind: str, mode: int, cap: int, skip_modes: frozenset):
    """跑生涯武器统计；带"汇总结果"缓存——同范围再查只补拉上次覆盖日期之后的新对局

    缓存命中且范围未变时：直接读回上次的排名与覆盖日期，只枚举/统计比它更新的对局，
    把新增的击杀累加进去（逐场 PGCR 也走 _PVP_MATCH_CACHE），于是第二次查基本只花
    "翻最近几页活动历史"的时间。
    """
    _load_agg_cache()
    key = f"{mid}|{kind}|{scope}"
    base = _AGG_CACHE.get(key)
    reuse = bool(base and base.get("weapons")
                 and base.get("scope_since", "") == since
                 and base.get("scope_until", "") == until)
    agg: dict[str, dict] = {}
    tot = {"kills": 0, "precision": 0, "melee": 0, "grenade": 0, "super": 0, "ability": 0}
    base_matches = base_missed = 0
    base_oldest = base_newest_full = ""
    eff_since = since
    if reuse:
        agg = {h: dict(v) for h, v in (base.get("weapons") or {}).items()}
        for k in tot:
            tot[k] = int((base.get("tot") or {}).get(k, 0) or 0)
        base_matches = int(base.get("matches", 0) or 0)
        base_missed = int(base.get("missed", 0) or 0)
        base_oldest = base.get("oldest", "") or ""
        base_newest_full = base.get("newest_full", "") or ""
        eff_since = base_newest_full[:10] or since
    empty = {"display": JOBS[jid]["name"], "scope": scope, "scope_label": label,
             "kind": kind, "matches": base_matches, "missed": base_missed, "capped": False,
             "range": (base_oldest, base_newest_full[:10]),
             "cached": base_matches if reuse else 0, "added": 0,
             "weapons": sorted(agg.values(), key=lambda x: -x["kills"]), **tot}
    try:
        matches = await _collect_matches(mtype, mid, chars, mode, eff_since, until, cap, skip_modes)
        if reuse and base_newest_full:  # 边界那天会重复枚举，按完整时间戳只留更新的
            matches = [m for m in matches if m["period"] > base_newest_full]
        if not matches:  # 没有新对局：有缓存就直接返回上次排名，否则返回空态
            JOBS[jid].update(status="done", total=1, done=1, result=empty)
            return
        JOBS[jid].update(total=len(matches))
        missed = 0
        sem = asyncio.Semaphore(_PVP_CONCURRENCY)
        lock = asyncio.Lock()

        async def one(m: dict):
            nonlocal missed
            async with sem:
                try:
                    c = await pvp_match_contribution(m["instance"], mid)
                except Exception:  # noqa: BLE001 单场失败不打断整体统计
                    c = None
            if not c:
                missed += 1
                JOBS[jid]["done"] += 1
                return
            async with lock:
                for k in tot:
                    tot[k] += c[k]
                for w in c["weapons"]:
                    if w["kills"] <= 0:
                        continue
                    a = agg.setdefault(w["hash"] or w["name"], {
                        "name": w["name"], "icon": w["icon"], "type": w["type"],
                        "kills": 0, "precision": 0, "matches": 0})
                    a["kills"] += w["kills"]
                    a["precision"] += w["precision"]
                    a["matches"] += 1
                JOBS[jid]["done"] += 1

        await asyncio.gather(*(one(m) for m in matches))
        _save_pvp_cache()
        weapons = sorted((v for v in agg.values() if v["kills"] > 0), key=lambda x: -x["kills"])
        total_matches = base_matches + len(matches)
        dates = [m["period"][:10] for m in matches]
        newest = max(dates) if dates else base_newest_full[:10]
        pool = [d for d in dates if d] + ([base_oldest] if base_oldest else [])
        oldest = min(pool) if pool else ""
        newest_full = max([m["period"] for m in matches] + ([base_newest_full] if base_newest_full else []))
        result = {
            "display": JOBS[jid]["name"], "scope": scope, "scope_label": label, "kind": kind,
            "matches": total_matches, "missed": base_missed + missed,
            "capped": total_matches >= cap, "range": (oldest, newest),
            "added": len(matches), "cached": base_matches if reuse else 0,
            "weapons": weapons,
            # 卡片只展示前 60 把，汇总块要用全量，所以单独给两个总数
            "weapon_kills": sum(w["kills"] for w in weapons),
            "weapon_precision": sum(w["precision"] for w in weapons),
            **tot}
        JOBS[jid].update(status="done", result=result)
        _AGG_CACHE[key] = {
            "scope_since": since, "scope_until": until,
            "weapons": agg, "tot": tot, "matches": total_matches,
            "missed": base_missed + missed, "oldest": oldest,
            "newest": newest, "newest_full": newest_full,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_agg_cache()
    except Exception as exc:  # noqa: BLE001
        JOBS[jid].update(status="error", error=str(exc))


def _rec_done(prof_records: dict, rh: str) -> bool:
    st = prof_records.get(str(rh), {}).get("state")
    return bool(st is not None and st & 1)


def _obj_progress(st: dict) -> tuple[int, int]:
    """记录自带的第一个有效目标 → (已达成, 目标值)；没有则 (0, 0)。

    锻造图案的"深视共振萃取"进度就存在这里（未完成也会有 1/5 这种中间值），
    所以图案卡能像游戏里一样显示 x/y，而不是只有"已获得/未获得"。
    """
    for o in st.get("objectives") or []:
        cv, pg = o.get("completionValue") or 0, o.get("progress") or 0
        if cv > 0:
            return min(pg, cv), cv  # 计数器型目标会超出（击杀数 > 需求），截到目标值
    return 0, 0


def _merged_records(prof: dict) -> dict:
    """合并 profileRecords 与 characterRecords。

    关键：部分记录（突袭/地牢凯旋等）只在 characterRecords 里返回，
    只读 profileRecords 会误判成"未完成"，称号进度因此长期偏小。
    """
    pr = dict((prof.get("profileRecords") or {}).get("data", {}).get("records", {}))
    for ch in ((prof.get("characterRecords") or {}).get("data") or {}).values():
        pr.update((ch or {}).get("records", {}))
    return pr


async def node_report(name: str, kind: str) -> dict:
    """称号(kind=titles)/锻造图案(kind=patterns)：基于记录状态"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    r = await client().get(f"/Platform/Destiny2/{member['mtype']}/Profile/{member['mid']}/",
                         params={"components": "200,900"})
    prof = _parse(r).get("Response", {})
    pr = _merged_records(prof)

    items = []
    if kind == "titles":
        for root, group in (("616318467", "称号"), ("1881970629", "传承称号")):
            if root not in _pnodes:
                continue
            for c in _pnodes[root]["children"]["presentationNodes"]:
                nd = _pnodes[str(c["presentationNodeHash"])]
                rh = nd.get("completionRecordHash")
                if not rh or str(rh) not in _records:
                    continue
                d = _records[str(rh)]
                dp = d.get("displayProperties", {})
                gild = d.get("titleInfo", {}).get("gildingTrackingRecordHash")
                child = nd["children"]["records"]
                earned = _rec_done(pr, str(rh))
                n_done = sum(1 for rc in child if _rec_done(pr, rc["recordHash"]))
                n_total = len(child)
                # 已获得的称号：部分子记录会随赛季重置，子记录计数会显示成 0/12 这种矛盾值，
                # 此时改用"完成记录"自带的汇总目标（progress/completionValue）作为真实进度。
                if earned:
                    for o in (pr.get(str(rh), {}).get("objectives") or []):
                        cv, pg = o.get("completionValue") or 0, o.get("progress") or 0
                        if cv > 0 and 0 < pg <= cv:
                            n_done, n_total = pg, cv
                            break
                items.append({
                    "name": nd["displayProperties"].get("name") or dp.get("name", "?"),
                    "icon": BASE + nd["displayProperties"]["icon"] if nd["displayProperties"].get("icon") else "",
                    "desc": dp.get("description", "")[:120],
                    "completed": earned,
                    # can_gild=该称号有镀金目标（部分称号不可镀金）；gilded=镀金目标已完成
                    "can_gild": bool(gild),
                    "gilded": bool(gild and _rec_done(pr, str(gild))),
                    "group": group,
                    "done_n": n_done, "total_n": n_total,
                })
    else:
        # 分组、顺序、组名 1:1 对照小日向的锻造页（固定表 pattern_groups.json）。
        # 两个关键点：
        #   1. "异域催化"不是锻造图案，不再混进这张卡（旧版把 141 条催化也算进来，
        #      所以列表里会看到"洛伦兹驱动器 能量武器 0/400"这类催化条目）；
        #   2. 组名不再用藏品 sourceString 现推——那会冒出"季票奖励""升级过程中获得"
        #      这种来源串当分组名，固定表按副本/赛季/DLC 归组。

        def leaves(h):
            n = _pnodes[str(h)]
            if n["children"]["records"]:
                yield n
            for c in n["children"]["presentationNodes"]:
                yield from leaves(c["presentationNodeHash"])

        info: dict[str, dict] = {}
        for mid in _pnodes["2642502414"]["children"]["presentationNodes"]:
            mid_node = _pnodes[str(mid["presentationNodeHash"])]
            for c in mid_node["children"]["presentationNodes"]:
                sh = c["presentationNodeHash"]   # children 里是 {hash, priority} 字典
                sname = _pnodes[str(sh)]["displayProperties"].get("name") or ""
                if "催化" in sname:  # 异域催化 → 不列
                    continue
                for leaf in leaves(sh):
                    wtype = leaf["displayProperties"].get("name", "")
                    for rc in leaf["children"]["records"]:
                        dp = (_records.get(str(rc["recordHash"]), {}) or {}).get("displayProperties", {})
                        nm = dp.get("name")
                        if not nm or "[PLACEHOLDER" in nm:
                            continue
                        done_n, total_n = _obj_progress(pr.get(str(rc["recordHash"])) or {})
                        info[nm] = {
                            "name": nm, "icon": BASE + dp["icon"] if dp.get("icon") else "",
                            "desc": (dp.get("description", "") or "")[:120],
                            "completed": _rec_done(pr, rc["recordHash"]),
                            "gilded": False, "group": "",
                            "slot": sname.replace("模式", ""), "type": wtype,
                            "done_n": done_n, "total_n": total_n,
                        }

        table = _pat_groups.get("groups") or {}
        buckets: dict[str, list] = {}
        if table:  # 组名/归属按表来（一把武器可在两组里各出现一次）
            for g in _pat_groups.get("order") or list(table):
                for nm in table.get(g) or []:
                    it = info.get(nm)
                    if it:
                        buckets.setdefault(g, []).append({**it, "group": g})
        else:  # 没建过分组表 → 退回按槽位分组，页面不空
            for it in info.values():
                buckets.setdefault(it["slot"], []).append({**it, "group": it["slot"]})
            for g in buckets:
                buckets[g].sort(key=lambda x: (x["type"], not x["completed"], x["name"]))
        # 组排序（小日向式智能排序）：
        #   1) 先按「出的顺序」排（pattern_groups.json 的 release 表，旧→新）；
        #   2) 已经全部集齐的组整组往后排——顶部永远留没集齐的组；
        #   组内保持表里的固定顺序（组标题仍显示各自的 x/y 进度）。
        rank = {g: i for i, g in enumerate(_pat_groups.get("release") or
                                           _pat_groups.get("order") or list(buckets))}
        for g in sorted(buckets,
                        key=lambda g: (all(i["completed"] for i in buckets[g]),
                                       rank.get(g, 999))):
            items.extend(buckets[g])
    if kind == "titles":
        order = ["称号", "传承称号"]
        rank = {g: i for i, g in enumerate(order)}
        items.sort(key=lambda x: (rank.get(x["group"], 99), x.get("type", ""),
                                  not x["completed"], x["name"]))
    # 图案：顺序已在上面按 pattern_groups.json 排好，不再二次排序
    done = sum(1 for i in items if i["completed"])
    return {"display": f"{member['display']}#{fmt_code(member['code'])}",
            "title": "称号进度" if kind == "titles" else "武器锻造图案",
            "done": done, "total": len(items), "items": items,
            "gildable": sum(1 for i in items if i.get("can_gild")),
            "gilded": sum(1 for i in items if i.get("gilded")),
            "kind": kind}


# 热力图结果缓存（落盘 heatmap_cache.json，和别的缓存一起放 exe 同目录）。
# 全历史聚合一次要翻十几页，同一个人隔一会儿再查完全没必要重来：
# 缓存里记着「已统计到哪一场」（newest_full，精确到分钟），配合 profile 里每个角色的
# dateLastPlayed 就能判断有没有新数据——没新数据直接出缓存，有新数据只补拉新增的那几场。
# 两边都是 Bungie 返回的 UTC 时间戳（截到分钟），所以直接按字符串比大小就是对的。
_HEAT_CACHE: dict[str, dict] = {}
_HEAT_CACHE_FILE = "heatmap_cache.json"
_HEAT_CACHE_MAX = 60          # 最多留多少个玩家的结果（一天一条，单人是 100KB 量级）
_heat_cache_ready = False


def _load_heat_cache():
    global _heat_cache_ready
    if _heat_cache_ready:
        return
    _heat_cache_ready = True
    try:
        with open(_writable_path(_HEAT_CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _HEAT_CACHE.update(data)
    except Exception:  # noqa: BLE001 首次运行/文件损坏都不影响统计
        pass


def _save_heat_cache():
    if len(_HEAT_CACHE) > _HEAT_CACHE_MAX:  # 满了丢最久没更新的四分之一，够用就行
        stale = sorted(_HEAT_CACHE, key=lambda k: _HEAT_CACHE[k].get("updated") or "")
        for k in stale[: _HEAT_CACHE_MAX // 4]:
            _HEAT_CACHE.pop(k, None)
    try:
        with open(_writable_path(_HEAT_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(_HEAT_CACHE, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重算
        pass


def heat_cache_key(mtype: int, mid: str) -> str:
    return f"{mtype}:{mid}"


def _heat_snapshot(key: str, chars: list[str],
                   last_played: dict[str, str]) -> dict | None:
    """缓存能不能直接端出去：角色没变、且每个角色最后一次上号都不晚于「已统计到的那一场」

    不能只比日期——当天完全可能又打了几场，所以比的是完整时间戳。只要有角色拿不到上号时间
    就返回 None（保守起见重跑一趟），宁可多翻一页也不给出偏旧的数据。
    """
    base = _HEAT_CACHE.get(key)
    if not base or not base.get("days") or base.get("chars") != chars:
        return None
    newest = base.get("newest_full") or ""
    if not newest:
        return None
    stamps = [last_played.get(c) or "" for c in chars]
    if not stamps or not all(stamps) or max(stamps) > newest:
        return None
    return base


def _heat_result(jid: str, days: dict, *, cached: bool, added: int,
                 gate: str, newest_full: str) -> dict:
    return {"display": JOBS[jid]["name"], "days": days, "cached": cached,
            "added": added, "matches": sum(v["matches"] for v in days.values()),
            "gate": gate, "newest_full": newest_full}


async def start_heatmap(name: str, who: str = "") -> str | None:
    """热力图走后台任务：全历史活动按天聚合（翻页直到 2019-06 赛季纪元前）"""
    member = await resolve_member(name)
    if not member:
        return None
    mtype, mid = member["mtype"], member["mid"]
    dkey = f"{mtype}:{mid}:heat"          # 去重键（任务级）
    ckey = heat_cache_key(mtype, mid)     # 缓存键（结果级）
    hit = _reuse_job(dkey)
    if hit:  # 命中就不必再拉 profile 了，直接把人带去等已有任务
        _mark_reused(hit, who)
        return hit
    profile = await get_profile(mtype, mid)
    char_data = profile.get("characters", {}).get("data", {})
    chars = list(char_data)
    # 各角色最后一次上号（UTC，和 activity_history 的 period 同格式，可直接比）
    last_played = {cid: (c.get("dateLastPlayed") or "")[:16].replace("T", " ")
                   for cid, c in char_data.items()}
    jid = f"{mid}_heat_{len(JOBS)}"
    _register_job(dkey, jid, {
        "done": 0, "total": 0, "status": "queued",
        "name": f"{member['display']}#{fmt_code(member['code'])}", "result": None,
        "kind": "heat", "who": who or "网页", "ts": time.time(),
        "label": "热力图（全历史活跃）"})
    gate = max([v for v in last_played.values() if v] or [""])
    _load_heat_cache()
    snap = _heat_snapshot(ckey, chars, last_played)
    if snap:  # 没有新数据：连队都不排，直接当已完成的任务返回
        JOBS[jid].update(status="done", total=1, done=1, cached=True,
                         result=_heat_result(jid, {k: dict(v) for k, v in snap["days"].items()},
                                             cached=True, added=0, gate=gate,
                                             newest_full=snap.get("newest_full") or ""))
        return jid
    _enqueue_job(jid, lambda: _run_heatmap(
        jid, mtype, mid, chars, ckey, last_played, gate))
    return jid


async def _run_heatmap(jid: str, mtype: int, mid: str, chars: list[str],
                       ckey: str, last_played: dict[str, str], gate: str):
    """翻页聚合。命中过缓存的话只补拉「已统计到的那一场」之后的部分"""
    _load_heat_cache()
    base = _HEAT_CACHE.get(ckey)
    reuse = bool(base and base.get("days") and base.get("chars") == chars)
    days = {k: dict(v) for k, v in base["days"].items()} if reuse else {}
    # cutoff 是「缓存已经统计到哪一场」，翻页时用它决定收工；newest_full 是本次跑完后的最新，
    # 两者必须分开——共用一个变量的话第二轮就会拿刚统计的第一场当边界，只计一场就停
    cutoff = (base.get("newest_full") or "") if reuse else ""
    newest_full = cutoff
    counted = 0
    try:
        for cid in chars:
            page = 0
            while page < 60:  # 60页×250 ≈ 上限1.5万场/角色
                acts = await activity_history(mtype, mid, cid, 0, count=250, page=page)
                if not acts:
                    break
                for m in acts:
                    p = m["period"]
                    d = p[:10]
                    if not d:
                        continue
                    if cutoff and p <= cutoff:
                        break  # 已经统计过的部分（新→旧），后面更旧，不用再翻
                    days.setdefault(d, {"matches": 0, "minutes": 0, "kills": 0})
                    days[d]["matches"] += 1
                    days[d]["minutes"] += m["duration"] // 60
                    days[d]["kills"] += m["kills"]
                    counted += 1
                    if p > newest_full:
                        newest_full = p
                    if d < "2019-06":  # 赛季纪元前，不再翻页
                        break
                else:
                    page += 1
                    JOBS[jid]["total"] = page + 1
                    JOBS[jid]["done"] = page
                    continue
                break
        total_n = sum(v["matches"] for v in days.values())
        # 补拉时数进来的每一场都是新的（遇到已统计过的就停了）；全量重跑则没有「新增」可言
        added = counted if reuse else 0
        _HEAT_CACHE[ckey] = {"display": JOBS[jid]["name"], "chars": chars, "days": days,
                             "newest_full": newest_full, "total": total_n,
                             "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_heat_cache()
        JOBS[jid].update(status="done", total=JOBS[jid]["total"] or 1, done=JOBS[jid]["total"] or 1,
                         cached=reuse,
                         result=_heat_result(jid, days, cached=reuse, added=added,
                                            gate=gate, newest_full=newest_full))
    except Exception as exc:  # noqa: BLE001
        JOBS[jid].update(status="error", error=str(exc))
async def mode_report(name: str, mode: int, count: int = 100) -> dict:
    """基于对局历史聚合某模式战绩（跨角色合并 + 细分模式 + 胜率）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})

    seen, matches = set(), []
    for cid in chars:
        for m in await activity_history(mtype, mid, cid, mode, count=count):
            key = m["instance"] or f"{m['ref']}{m['period']}"
            if key in seen:
                continue
            seen.add(key)
            matches.append(m)
    matches.sort(key=lambda m: m["period"], reverse=True)

    done = [m for m in matches if m["completed"]]
    competitive = any(m["competitive"] for m in matches)
    # 探索(巡逻)这类活动本身没有"完成"概念，别拉低 PVE 通关率
    rate_base = matches if competitive else [m for m in matches if m["mode"] not in NON_COMPLETABLE]
    wins = sum(1 for m in done if m["win"])
    losses = sum(1 for m in done if not m["win"]) if competitive else 0
    kills = sum(m["kills"] for m in done)
    deaths = sum(m["deaths"] for m in done)
    assists = sum(m["assists"] for m in done)
    secs = sum(m["duration"] for m in done)

    br: dict[str, dict] = {}
    for m in matches:
        b = br.setdefault(m["mode_name"] or "其他",
                          {"name": m["mode_name"] or "其他", "n": 0, "done": 0, "wins": 0,
                           "kills": 0, "deaths": 0, "assists": 0, "secs": 0})
        b["n"] += 1
        if m["completed"]:
            b["done"] += 1
            b["wins"] += 1 if m["win"] else 0
            b["kills"] += m["kills"]
            b["deaths"] += m["deaths"]
            b["assists"] += m["assists"]
            b["secs"] += m["duration"]
    breakdown = sorted(br.values(), key=lambda b: -b["n"])
    for b in breakdown:
        b["kd"] = b["kills"] / b["deaths"] if b["deaths"] else 0.0
        b["kda"] = (b["kills"] + b["assists"]) / b["deaths"] if b["deaths"] else 0.0
        b["win_rate"] = b["wins"] / b["done"] * 100 if b["done"] else 0.0
        b["avg_kills"] = b["kills"] / b["done"] if b["done"] else 0.0

    # 当前连胜/连败（从最近一场往回数，遇到结果翻转即停）
    streak, streak_win = 0, None
    if competitive:
        for m in matches:
            if not m["completed"]:
                continue
            if streak_win is None:
                streak_win, streak = m["win"], 1
            elif m["win"] == streak_win:
                streak += 1
            else:
                break
    eff_list = [m["eff"] for m in done if m.get("eff")]
    return {
        "display": f"{member['display']}#{fmt_code(member['code'])}",
        "competitive": competitive,
        "total": len(matches),
        "completed": len(done),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(done) * 100) if (competitive and done) else 0.0,
        "streak": streak, "streak_win": bool(streak_win),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kd": (kills / deaths) if deaths else 0.0,
        "kda": ((kills + assists) / deaths) if deaths else 0.0,
        "avg_kills": (kills / len(done)) if done else 0.0,
        "eff": (sum(eff_list) / len(eff_list)) if eff_list else 0.0,
        "rate_base": len(rate_base),
        "clear_rate": (sum(1 for m in rate_base if m["completed"]) / len(rate_base) * 100) if rate_base else 0.0,
        "hours": secs / 3600,
        "breakdown": breakdown,
        "matches": matches,
    }


# ---------- Eververse 光尘商店（数据源：Bungie 官方 GetVendors，需账号授权） ----------
# 说明：只有 API Key 读不了商店（InsufficientPrivileges），必须用 Bungie 账号授权
# （见 bungie_auth.py，面板里点「授权 Bungie 账号」）。
#
# 游戏里「商店 → 日常优惠」的三行，是三组**互相独立的 vendor**，不在总店 3361454721 里面：
#   主要光尘优惠 = 6 个 EVERVERSE_BRIGHT_DUST_ROTATOR_EXOTIC_*（异域武器装饰 / 护甲装饰 /
#                  表情 / 机灵 / 飞船 / 快雀）
#   其他光尘优惠 = 4 个 EVERVERSE_BRIGHT_DUST_ROTATOR_LEGENDARY_*（动作与终结技 / 机灵投影 /
#                  着色器 / 传送特效）
#   银币优惠     = EVERVERSE_FEATURED_SLOTS
# 旧实现只查总店：那里是 223 件 700 银币的常驻旧货 + 当天恰好 1 件光尘商品，
# 所以卡片永远只出一把枪皮。
EVERVERSE_VENDOR_HASH = "3361454721"          # 泰斯·艾夫瑞斯（Eververse）总店，留作备用
BRIGHT_DUST_HASHES = {"2817410917", "3168101969"}   # 「光尘」货币的物品 hash
SILVER_HASHES = {"3147280338"}                       # 「银币」货币的物品 hash
_EV_CUR_HASHES = {"光尘": BRIGHT_DUST_HASHES, "银币": SILVER_HASHES}

# 分节顺序 = 游戏内显示顺序（每节给一组 vendor）。银币（银币优惠那栏）不出，只做光尘。
EV_SECTIONS = (
    ("主要光尘优惠", "光尘", ("2168194999", "2031393824", "3118972542",
                              "3702989297", "4020265966", "1105106638")),
    ("其他光尘优惠", "光尘", ("2184482416", "1446296883", "2041776156", "213864513")),
)
# 护甲装饰 vendor 是按角色职业发的（同一天泰坦/猎人/术士各一件），要三个角色都查
EV_CLASS_VENDOR = "2031393824"
# 用大图（screenshot）当背景的类型，**顺序即卡片里的排列**：武器皮肤 → 三职业护甲皮肤
# → 飞船 → 载具；其余（机灵、表情、投影、着色器、传送特效…）只出方形缩略图
EV_BIG_TYPES = ("武器皮肤", "泰坦皮肤", "猎人皮肤", "术士皮肤", "通用皮肤", "面具皮肤",
                "飞船", "载具", "快雀")

EV_CACHE_FILE = "eververse_cache.json"
EV_CACHE_VER = 3      # 换缓存结构/展示规则时 +1，旧缓存自动作废
_EV_CACHE: dict = {"at": 0.0, "day": "", "data": None}
_EV_INDEX = None

# 货币图标也走索引（光尘 2817410917 / 银币 3147280338 都在 eververse_items.json 里）
_EV_CUR_ITEM = {"光尘": "2817410917", "银币": "3147280338"}


def ev_cur_icon(cur: str) -> str:
    """「光尘 / 银币」的货币图标 URL，索引里取不到就返回空串（卡片自行降级）。"""
    rec = _ev_index().get(_EV_CUR_ITEM.get(cur, "")) or []
    return (BASE + rec[3]) if len(rec) > 3 and rec[3] else ""


class BungieAuthRequired(RuntimeError):
    """还没授权 Bungie 账号（面板里点一次「授权 Bungie 账号」即可）"""


def _ev_index() -> dict:
    global _EV_INDEX
    if _EV_INDEX is None:
        try:
            _EV_INDEX = json.load(open(_idx_file("eververse_items.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _EV_INDEX = {}
    return _EV_INDEX


def _ev_day(ts: float | None = None) -> str:
    """商店「当日」缓存键：商店北京时间凌晨 1 点刷新，所以 1 点前查到的东西算前一天。"""
    t = datetime.datetime.fromtimestamp(ts if ts is not None else time.time())
    return (t - datetime.timedelta(hours=1)).strftime("%Y-%m-%d")


def _ev_cache_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, EV_CACHE_FILE)


def _ev_cache_load(day: str) -> dict | None:
    try:
        d = json.load(open(_ev_cache_path(), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if d.get("ver") != EV_CACHE_VER or d.get("day") != day:
        return None
    return d.get("data")


def _ev_cache_save(day: str, data: dict):
    try:
        json.dump({"ver": EV_CACHE_VER, "day": day, "at": time.time(), "data": data},
                  open(_ev_cache_path(), "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _ev_from_vendors(resps: list) -> dict:
    """把各角色 GetVendors（角色级、一次拿全部 vendor）的返回整理成商品分节。

    这个接口的 sales 是 {vendorHash: {"saleItems": {n: sale}}}，与单店 GetVendor 的
    {saleIndex: sale} 不同，所以这里按 vendor hash 直接取。价格只看 costs 里是不是光尘，
    不依赖 vendor 的 categories（那层是 {"categories":[...]} 嵌套，实测只有一个「全部」分类）。

    resps 是**每个角色各一份**：护甲装饰 vendor 按职业发不同商品，只查一个角色会缺两个职业。
    合并时按 itemHash 去重，其余 vendor 三个角色返回一样，不受影响。
    """
    idx = _ev_index()
    sections = []
    for title, cur, vendors in EV_SECTIONS:
        want = _EV_CUR_HASHES[cur]
        items, seen = [], set()
        for vh in vendors:
            for resp in resps:
                sales = ((resp.get("sales") or {}).get("data")) or {}
                group = sales.get(vh)
                sale_items = group.get("saleItems") if isinstance(group, dict) else None
                for sale in (sale_items or {}).values():
                    if not isinstance(sale, dict):
                        continue
                    h = str(sale.get("itemHash") or "")
                    if not h or h in seen:
                        continue
                    amount = None
                    for c in (sale.get("costs") or []):
                        if isinstance(c, dict) and str(c.get("itemHash")) in want:
                            amount = int(c.get("quantity") or 0)
                            break
                    if amount is None:      # 该 vendor 里不是光尘价的商品
                        continue
                    rec = idx.get(h) or []
                    name = rec[0] if len(rec) > 0 else ""
                    if not name:            # 索引里没有的（非 Eververse 物品）直接跳过
                        continue
                    ty = rec[1] if len(rec) > 1 else ""
                    tier = rec[2] if len(rec) > 2 else ""
                    icon = (BASE + rec[3]) if len(rec) > 3 and rec[3] else ""
                    shot = (BASE + rec[4]) if len(rec) > 4 and rec[4] else ""
                    seen.add(h)
                    items.append({"hash": h, "n": name, "ty": ty, "tier": tier,
                                  "icon": icon, "shot": shot, "cost": amount, "cur": cur,
                                  "big": any(k in ty for k in EV_BIG_TYPES)})
        if items:
            # 有预览图的排在前面，顺序按 EV_BIG_TYPES；其余保持 vendor 顺序跟在后面
            items.sort(key=lambda it: (EV_BIG_TYPES.index(it["ty"])
                                       if it["ty"] in EV_BIG_TYPES else len(EV_BIG_TYPES)))
            sections.append({"name": title, "cur": cur, "items": items})
    return {"sections": sections, "source": "bungie"}


async def _ev_vendor_responses() -> list:
    import bungie_auth
    if not bungie_auth.authorized():
        raise BungieAuthRequired("还没有授权 Bungie 账号")
    mem = await bungie_auth.membership()
    if not mem or not mem.get("membership_id"):
        raise BungieAuthRequired("Bungie 授权信息无效，请在面板重新授权")
    mt, mid = mem["membership_type"], mem["membership_id"]
    prof = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/", {"components": "200"})
    chars = ((prof.get("characters") or {}).get("data")) or {}
    if not chars:
        raise RuntimeError("该 Bungie 账号没有命运2角色")
    out = []
    for cid in chars:
        out.append(await bungie_auth.authorized_get(
            f"/Platform/Destiny2/{mt}/Profile/{mid}/Character/{cid}/Vendors/",
            {"components": "400,401,402"}))
    return out


async def eververse_store(force: bool = False) -> dict:
    """游戏内**当前上架**的光尘商品，按「主要光尘 / 其他光尘」分节。

    商店北京时间凌晨 1 点刷新：当天取过一次就直接复用（内存 + 落盘 eververse_cache.json），
    过了 1 点缓存键换成新的日期，会自动重新拉一轮。force=True 强制重取。

    未授权时抛 BungieAuthRequired（上层提示去面板点授权）。
    """
    now = time.time()
    day = _ev_day(now)
    if not force:
        if _EV_CACHE["data"] and _EV_CACHE["day"] == day:
            return _EV_CACHE["data"]
        disk = _ev_cache_load(day)
        if disk:
            _EV_CACHE.update(at=now, day=day, data=disk)
            return disk
    resps = await _ev_vendor_responses()
    data = _ev_from_vendors(resps)
    data["updated"] = now
    data["day"] = day
    _EV_CACHE.update(at=now, day=day, data=data)
    _ev_cache_save(day, data)
    return data


# ---------- 本周轮换（突袭 / 地牢） ----------
# 数据来源：官方 `Destiny2.GetPublicMilestones`（**只要 API Key，不需要 OAuth**）+ 本地配对表。
#
# 官方接口**不标**「本周轮换的是哪两个」，但它给每个突袭一个周常里程碑：
# **带 challengeObjectiveHashes 的那个就是当周轮换的**（实测本周 = 克洛塔的末日 / 深岩墓室，
# 与第三方站一致）。另有「永恒沙漠」也带挑战，但它不在配对表的突袭列里，取交集就滤掉了。
#
# 配对表（manifest_index/rotation_pairs.json，由 scrape_starside_rotation.py 抓）是**固定周期**：
# 每周整体后移一格，于是拿本周突袭①所在的行号 k 就能推出另外三个：
#   突袭② = 突袭① 在「有突袭的行」里往后数 4 个（共 9 行有突袭，循环）
#   地牢① = 第 k-1 行的地牢
#   地牢② = 第 k+3 行的地牢（共 10 行，循环）
# 这四条偏移用 2026-09-09 / 09-16 / 09-23 / 09-30 四周的公开排期逐一验过。
ROT_CACHE_FILE = "rotation_cache.json"
ROT_CACHE_VER = 1
_ROT_CACHE: dict = {"key": "", "data": None}
_ROT_PAIRS = None


def _rot_pairs() -> list:
    global _ROT_PAIRS
    if _ROT_PAIRS is None:
        try:
            _ROT_PAIRS = (json.load(open(_idx_file("rotation_pairs.json"), encoding="utf-8"))
                          .get("rows") or [])
        except Exception:  # noqa: BLE001
            _ROT_PAIRS = []
    return _ROT_PAIRS


def _bj(iso: str):
    """Bungie 的 UTC ISO 时间 → 北京时间"""
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
            datetime.timezone(datetime.timedelta(hours=8)))
    except Exception:  # noqa: BLE001
        return None


def rot_activity(name: str) -> dict:
    """按基础名找活动（索引里的名字带「: 标准 / 大师」后缀），返回它的图标与横图。"""
    if not name:
        return {}
    for key in (name, name + ": 标准", name + "：标准"):
        rec = _activities.get(key)
        if rec:
            return rec
    for rec in _activities.values():          # 退一步：前缀匹配
        if (rec.get("name") or "").startswith(name):
            return rec
    return {}


def _rot_week_label(start: str, end: str) -> str:
    """周区间显示：结束时间是下周三 01:00，所以往前退一天才是本周最后一天。"""
    a, b = _bj(start), _bj(end)
    if not a or not b:
        return "本周"
    b = b - datetime.timedelta(days=1)
    return f"{a:%m月%d日} - {b:%m月%d日}"


async def rotation_week(force: bool = False) -> dict:
    """本周突袭①②/地牢①②（每周三凌晨 1 点换）。按周缓存。

    解析不出配对表时退化为「只列官方查到有周常挑战的突袭」，不会报错。
    """
    r = await client().get("/Platform/Destiny2/Milestones/")
    resp = r.json().get("Response") or {}
    pairs = _rot_pairs()
    raid_rows = [p for p in pairs if p.get("raid")]
    raid_names = {p["raid"] for p in raid_rows}

    feat, start, end = set(), "", ""
    for m in resp.values():
        acts = [a for a in (m.get("activities") or []) if isinstance(a, dict)]
        if not any(a.get("challengeObjectiveHashes") for a in acts):
            continue
        start = start or (m.get("startDate") or "")
        end = end or (m.get("endDate") or "")
        for a in acts:
            base = (activity_name(a.get("activityHash")).get("name") or "").split(":")[0].split("：")[0].strip()
            if base in raid_names:
                feat.add(base)

    key = start or "unknown"
    if not force and _ROT_CACHE["data"] and _ROT_CACHE["key"] == key:
        return _ROT_CACHE["data"]
    cached = None
    try:
        d = json.load(open(_rot_cache_path(), encoding="utf-8"))
        if d.get("ver") == ROT_CACHE_VER and d.get("key") == key:
            cached = d.get("data")
    except Exception:  # noqa: BLE001
        pass
    if cached and not force:
        _ROT_CACHE.update(key=key, data=cached)
        return cached

    raids, dungeons, k = [], [], -1
    order = [p["raid"] for p in raid_rows]
    for i, p in enumerate(pairs):
        if not p.get("raid"):
            continue
        second = order[(order.index(p["raid"]) + 4) % len(order)]
        if {p["raid"], second} == feat:
            k = i
            break
    if k >= 0:
        raids = [pairs[k]["raid"], order[(order.index(pairs[k]["raid"]) + 4) % len(order)]]
        dungeons = [pairs[(k - 1) % len(pairs)]["dungeon"],
                    pairs[(k + 3) % len(pairs)]["dungeon"]]
    else:                       # 配对表对不上（版本更新）时至少把官方查到的突袭列出来
        raids = sorted(feat)

    data = {"raids": raids, "dungeons": dungeons,
            "week_start": start, "week_end": end,
            "label": _rot_week_label(start, end),
            "matched": k >= 0, "source": "bungie"}
    _ROT_CACHE.update(key=key, data=data)
    try:
        json.dump({"ver": ROT_CACHE_VER, "key": key, "data": data},
                  open(_rot_cache_path(), "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return data


def _rot_cache_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, ROT_CACHE_FILE)
