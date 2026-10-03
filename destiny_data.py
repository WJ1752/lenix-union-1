"""Bungie API 数据层：查询玩家档案与历史统计"""
import asyncio
import datetime
import functools
import json
import os
import re
import sys
import time
import unicodedata
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
    """当前事件循环专用的 httpx 客户端（同一循环内复用连接池）

    外面套一层响应缓存：同一份数据在短时间里会被反复拉（同一条指令连点、一次
    /队伍 要拉 6 个人的角色级 Stats、面板与机器人都查同一个玩家…），Bungie 这边
    单次要 1~3 秒，缓存掉重复的那些是响应速度上最大的一块。"""
    loop = asyncio.get_running_loop()
    c = _CLIENTS.get(loop)
    if c is None or c.is_closed:
        raw = httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=15,
                                follow_redirects=True,
                                transport=httpx.AsyncHTTPTransport(retries=2))
        c = _CachedClient(raw)
        _CLIENTS[loop] = c
    return c


# ---------- GET 响应缓存 ----------
# TTL 按"这份数据多久才算过期"分档：
#   · 15 秒：实时态（204 在打什么、1000 队伍）—— 刚打完就查也基本反映得过来
#   · 180 秒：生涯/角色/成就（100/200/900/1100）—— 数字本来就按场次慢慢涨
#   · 45 秒：对局历史（翻页/多角色查询会连着拉同一页）
#   · 5~6 小时：对局 PGCR（打完就不会变）、manifest 实体（版本更新才变）
# 只缓存 HTTP 200；表是普通 dict（exe 里三个事件循环共用，最坏只是重复拉一次）。
_RESP: dict[str, tuple[float, bytes]] = {}
_RESP_MAX = 400


def _ttl_for(url: str, params: dict | None) -> float:
    if "/Profile/" in url:
        comps = str((params or {}).get("components") or "")
        if "204" in comps or "1000" in comps:
            return 15          # 实时态：在打什么 / 队伍
        if "1100" in comps:
            return 600         # 突袭指标砖（完成数/导师）：只有通关才会变
        return 180
    if "PostGameCarnageReport" in url:
        return 6 * 3600
    if "/Stats/Activities/" in url:
        return 45
    if "/Character/" in url and "/Stats/" in url:
        return 300
    if "/Manifest/" in url:
        return 6 * 3600
    if "SearchDestinyPlayer" in url:
        return 300
    return 60


class _CachedClient:
    """httpx.AsyncClient 的薄包装：GET/POST 走 TTL 缓存 + 读超时重试，其余原样转发"""

    def __init__(self, inner: httpx.AsyncClient):
        self._inner = inner

    @staticmethod
    def _key(url: str, params: dict | None, body: dict | None) -> str:
        tail = ""
        if params:
            tail += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if body:
            tail += "#" + "&".join(f"{k}={v}" for k, v in sorted(body.items()))
        return url + tail

    def _hit(self, key: str, url: str):
        ent = _RESP.get(key)
        if ent is not None and ent[0] > time.time():
            return httpx.Response(200, content=ent[1], request=httpx.Request("GET", url))
        return None

    def _save(self, key: str, ttl: float, r) -> None:
        if not key or r.status_code != 200:
            return
        _RESP[key] = (time.time() + ttl, r.content)
        if len(_RESP) > _RESP_MAX:
            for k in list(_RESP)[: _RESP_MAX // 4]:   # 先扔最早进来的四分之一
                _RESP.pop(k, None)

    async def _call(self, method: str, url, **kw):
        params = kw.get("params") or {}
        body = kw.get("json") if isinstance(kw.get("json"), dict) else None
        ttl = _ttl_for(url, params)
        key = self._key(url, params, body) if ttl else ""
        if key:
            hit = self._hit(key, url)
            if hit is not None:
                return hit
        for attempt in (1, 2):
            t_req = time.perf_counter()
            try:
                r = await getattr(self._inner, method)(url, **kw)
                break
            except httpx.TimeoutException:
                # 只重试"很快就失败"的那种（连接被掐、瞬时抖动）；真等满超时的
                # 说明链路正堵着，再重试一次只会让用户多等一整个超时
                if attempt == 2 or time.perf_counter() - t_req > 6:
                    raise
                await asyncio.sleep(0.5)
                kw = {**kw, "timeout": 8}
        self._save(key, ttl, r)
        return r

    async def get(self, url, **kw):
        return await self._call("get", url, **kw)

    async def post(self, url, **kw):
        # 只有按名字查账号（SearchDestinyPlayerByBungieName）走这里，POST 本身不带副作用
        return await self._call("post", url, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)

CLASS_NAMES = {0: "泰坦", 1: "猎人", 2: "术士", 3: "守卫者"}
RACE_NAMES = {0: "人类", 1: "觉醒者", 2: "EXO"}


def _used_text(sec: float) -> str:
    """耗时显示：秒级给 0.8s（短任务一眼看出），上了分钟给 1:23 / 1:02:03"""
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _traced(label):
    """公开查询入口的统一「开始 / 结束（实际耗时）」日志。

    一两次请求就完事的入口（对局详情 / 轮换 / 光尘 / 玩家搜索…）靠它补上
    `▶ 开始` 与 `✔ 完成，实际耗时 X`；多步 / 翻页的入口内部另有 log_progress
    的进度条与预计剩余，这里只补首尾两行。

    label 可以是字符串，也可以是 callable(*args, **kwargs) -> str（要带玩家名 / 模式时用）。
    不改变被装饰函数的签名、返回值与异常行为：异常原样抛出（LookupError 也不会被吞），
    只在抛出前多打一行「✘ 失败 · 实际耗时」。同步 / 异步函数都能用。
    """
    def deco(fn):
        def _text(args, kwargs):
            if callable(label):
                try:
                    return str(label(*args, **kwargs))
                except Exception:  # noqa: BLE001 标签算不出来也不能把主流程带崩
                    return getattr(fn, "__name__", "查询")
            return str(label)

        def _begin(args, kwargs):
            txt = _text(args, kwargs)
            print(f"[进度] {time.strftime('%H:%M:%S')} ▶ {txt}", flush=True)
            return txt

        def _finish(txt, t0, exc):
            used = _used_text(time.time() - t0)
            if exc is None:
                print(f"[进度] {time.strftime('%H:%M:%S')} ✔ {txt} 完成，实际耗时 {used}",
                      flush=True)
            else:
                print(f"[进度] {time.strftime('%H:%M:%S')} ✘ {txt} 失败，实际耗时 {used}"
                      f"（{type(exc).__name__}: {exc}）", flush=True)

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrap(*args, **kwargs):
                txt = _begin(args, kwargs)
                t0 = time.time()
                try:
                    out = await fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 原样抛，只补一行日志
                    _finish(txt, t0, exc)
                    raise
                _finish(txt, t0, None)
                return out
            return awrap

        @functools.wraps(fn)
        def swrap(*args, **kwargs):
            txt = _begin(args, kwargs)
            t0 = time.time()
            try:
                out = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 原样抛，只补一行日志
                _finish(txt, t0, exc)
                raise
            _finish(txt, t0, None)
            return out
        return swrap

    return deco


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


def norm_key(s: str) -> str:
    """无符号归一化键：NFKC 折叠全角/特殊形态 → 小写 → 剔除所有非文字字符。

    中文/字母/数字保留，·、'、-、空格、全角括号、# 等符号全部去掉，
    用于用户打不出物品名里的特殊符号时的兜底匹配
    （「阿尔法·鲁皮之脊」↔「阿尔法鲁皮之脊」双向都能对上）。
    """
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^\w]+", "", s)

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
    """玩家名#编号 → dict(mtype, mid, display, code)；
    先精确查 Bungie，查不到（带错编号）再回落本地索引"""
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
    # 注：Bungie 已下线免鉴权模糊搜索（SearchDestinyPlayers 404），带错编号只能报没找到
    # 跨存档玩家：主平台(crossSaveOverride)那条才是有效数据；无跨存档取第一条
    best = next((p for p in cands
                 if p.get("crossSaveOverride") and p["membershipType"] == p["crossSaveOverride"]),
                cands[0] if cands else None)
    if best:
        mtype = best.get("crossSaveOverride") or best["membershipType"]
        display = best["bungieGlobalDisplayName"]
        dcode = best["bungieGlobalDisplayNameCode"]
        harvest_player(f"{display}#{fmt_code(dcode)}", best["membershipId"], mtype)
        return {"mtype": mtype,
                "mid": best["membershipId"], "display": display,
                "code": dcode,
                "icon": BASE + best["iconPath"] if best.get("iconPath") else ""}
    # Bungie 没查到：回落本地索引（PGCR 采集的 seen_players.json）
    seen = seen_players()
    if name in seen:
        mtype, mid, _ts = seen[name]
        return {"mtype": mtype, "mid": mid, "display": fname,
                "code": code, "icon": ""}
    return None


_SEEN_PATH = os.path.join("seen_players.json")
_SEEN: dict | None = None
_SEEN_CAP = 30000


def seen_players() -> dict:
    """本地玩家索引：name#code → [membershipType, membershipId, last_seen_ts]

    Bungie 已关闭免鉴权的前缀搜索，带不出 #编号 的名字只能靠本地积累：
    绑定名单 + 生涯/热力图任务拉过的每场 PGCR 里顺手采集的对局玩家"""
    global _SEEN
    if _SEEN is None:
        try:
            _SEEN = json.load(open(_SEEN_PATH, encoding="utf-8"))
        except Exception:  # noqa: BLE001  首次运行/文件损坏都从空表开始
            _SEEN = {}
    return _SEEN


def harvest_player(name: str, mid, mtype):
    """PGCR 解析时调用：记下对局里出现过的玩家，容量超限时淘汰最久没见的"""
    if not name or not mid or name.endswith("#?"):
        return
    seen = seen_players()
    import time as _t
    seen[name] = [int(mtype or 0), str(mid), int(_t.time())]
    if len(seen) > _SEEN_CAP:
        for k in sorted(seen, key=lambda k: seen[k][2])[:len(seen) - _SEEN_CAP + 1024]:
            del seen[k]


def save_seen_players():
    if _SEEN:
        json.dump(_SEEN, open(_SEEN_PATH, "w", encoding="utf-8"), ensure_ascii=False)


def search_seen(prefix: str) -> list[tuple[str, int, str]]:
    """本地索引按名字前缀（大小写不敏感）找玩家：[(name#code, mtype, mid), ...]"""
    p = prefix.lower()
    return [(k, v[0], v[1]) for k, v in seen_players().items()
            if k.rsplit("#", 1)[0].lower().startswith(p)]


@_traced(lambda q: f"玩家搜索 {q}")
async def search_players_fuzzy(q: str) -> list[dict]:
    """不带 #编号 的模糊搜索：Bungie 公开接口已不支持前缀搜索，
    这里只在本地索引（绑定 + PGCR 采集）里找，返回候选玩家列表"""
    return [{"bungieGlobalDisplayName": k.rsplit("#", 1)[0],
             "bungieGlobalDisplayNameCode": int(k.rsplit("#", 1)[1]),
             "membershipType": mt, "membershipId": mid}
            for k, mt, mid in search_seen(q)]


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


@_traced(lambda name: f"/玩家 {name}")
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
    nchars = len(chars)
    fdisp = f"{member['display']}#{fmt_code(member['code'])}"
    log_progress(f"full:{mid}", 0, nchars, label=f"/玩家 {fdisp}", force=True,
                 extra=f"拉取 {nchars} 个角色的 PVP/PVE/智谋生涯统计")
    for ci, (cid, c) in enumerate(chars.items(), 1):
        chars_meta.append({
            "id": cid, "class": CLASS_NAMES.get(c["classType"], "?"),
            "race": RACE_NAMES.get(c["raceType"], "?"), "light": c["light"],
            "playtime_min": int(c.get("minutesPlayedTotal", 0)),
            "last_played": _cn8(c["dateLastPlayed"][:16].replace("T", " ")),
            "emblem": BASE + c.get("emblemPath", ""),
            "emblem_bg": BASE + c.get("emblemBackgroundPath", ""),
        })
        st = await char_stats(mtype, mid, cid, "101,103,104")
        pvp_list.append(st); pve_list.append(st); gmb_list.append(st)
        log_progress(f"full:{mid}", ci, nchars, label=f"/玩家 {fdisp}",
                     extra=f"角色 {ci}/{nchars} 统计完成")

    # 智谋：官方聚合接口已下线，从对局历史聚合（跨角色，去重）
    log_progress(f"full:{mid}", nchars, nchars, label=f"/玩家 {fdisp}", force=True,
                 extra="聚合智谋对局历史（每人最近 100 场）")
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


@_traced(lambda name, group="allPvP": f"生涯统计 {name}")
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


# ---------- 生涯面板（分赛季 / 分职业 / 分模式时长） ----------

# 历史统计接口返回的键名基本等于模式定义的 friendlyName 归一化（见 build_modes.py 的 key），
# 但聚合模式的键名对不上，这里补一张兜底表（键名 → modeType）。
_MODE_KEY_FIX = {5: "allPvP", 7: "allPvE", 18: "allStrikes", 63: "pvecomp_gambit",
                 64: "allPvECompetitive", 75: "pvecomp_mamba"}


def _mode_key_map() -> dict:
    m: dict = {}
    for k, v in MODES.items():
        if v.get("key"):
            m.setdefault(v["key"], int(k))
    for mt, key in _MODE_KEY_FIX.items():
        m[key] = mt
    return m


# 请求哪些模式：全量都带上，没有数据的模式返回体里直接没有键，UI 再按阈值过滤
# （MODES 在文件下方才建好，这里延迟到用的时候再算）
_CAREER_MODES: tuple | None = None


def _career_modes() -> tuple:
    global _CAREER_MODES
    if _CAREER_MODES is None:
        _CAREER_MODES = tuple(sorted(int(k) for k in MODES))
    return _CAREER_MODES

# 赛季等级：progressionHash → (赛季号, 是否声望档, pass条目下标)。
# 玩家看到的「赛季等级」= 奖励档 + 声望档；S27（溯回，2025-07）起通行证改版为统一轨
# （一个 progression 一路往上数、可超 100，声望档成迁移遗留），且 seasonPassList 里
# 混入活动 pass（铁旗余灰/凯旋 等）——按「pass 名 ⊆ 赛季名」挑主条目（build_seasons.py）。
_SEASON_PROG: dict[int, tuple] = {}
_SEASON_PASS_MAIN: dict[int, int] = {}
for _s in SEASONS:
    _ps = _s.get("passes") or [{"rew": _s.get("prog"), "pres": _s.get("pres"), "name": ""}]
    _pick = 0
    _nm = _s.get("name") or ""
    for _i, _p in enumerate(_ps):
        if _p.get("rew"):
            _SEASON_PROG[int(_p["rew"])] = (int(_s["number"]), False, _i)
        if _p.get("pres"):
            _SEASON_PROG[int(_p["pres"])] = (int(_s["number"]), True, _i)
        if _p.get("name") and _p["name"] in _nm and _pick == 0 and _i > 0:
            _pick = _i
    _SEASON_PASS_MAIN[int(_s["number"])] = _pick


def _season_days(s: dict) -> int:
    """赛季持续天数；end 是 2099 哨兵（当前赛季）时算到今天"""
    import datetime
    try:
        st = datetime.date.fromisoformat(s["start"][:10])
    except (KeyError, ValueError):
        return 0
    end = (s.get("end") or "")[:10]
    if not end or end >= "2099":
        en = datetime.date.today()
    else:
        try:
            en = datetime.date.fromisoformat(end)
        except ValueError:
            en = datetime.date.today()
    return max((en - st).days, 0)


# ---------- 每赛季游玩时长（每日历史统计 + 本地缓存） ----------
# 口径（实测核实）：每日统计里 allPvE / allPvP / allPvECompetitive(智谋) 三大类互斥且
# 并起来覆盖全部活动；细分键（patrol/raid/story…）会漏（打击只挂 allStrikes、部分活动
# 无细分键），所以只认这三类，别拿细分键求和。
# 接口限制（Wj#8984 实测）：单次 daystart..dayend 相差 ≤31 天（32 天打回 ErrorCode 18），
# 所以按自然月分块；每日数据官方约保留 2 年半，2019 年（S8/S9）已查不到 → 计 0。
_TIME_DAY_KEYS = ("allPvE", "allPvP", "allPvECompetitive")
_TIME_CACHE_FILE = "season_time_cache.json"
_TIME_TAIL_DAYS = 10                # 最近 N 天官方还会回填，不封存
_time_cache: dict[str, dict] = {}   # mid|cid → {"days": {日期: 秒}, "done": 封存到的日期}
_time_cache_ready = False


def _load_time_cache():
    global _time_cache_ready
    if _time_cache_ready:
        return
    _time_cache_ready = True
    try:
        with open(_writable_path(_TIME_CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _time_cache.update(data)
    except Exception:  # noqa: BLE001 首次运行/文件损坏都不影响统计
        pass


def _save_time_cache():
    try:
        with open(_writable_path(_TIME_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(_time_cache, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重拉
        pass


async def _fetch_daily_secs(mtype: int, mid: str, cid: str,
                            day0: str, day1: str) -> dict[str, float]:
    """一段窗口（≤31 天）的 {日期: 在场秒数}；接口报错（角色已删等）返回空表"""
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
        params={"periodType": "Daily", "groups": "General",
                "daystart": day0, "dayend": day1})
    resp = r.json()
    if resp.get("ErrorCode") != 1:
        return {}
    out: dict[str, float] = {}
    R = resp.get("Response") or {}
    for key in _TIME_DAY_KEYS:
        for dv in (R.get(key) or {}).get("daily", []):
            sp = ((dv.get("values") or {}).get("secondsPlayed") or {}).get("basic", {})
            d = (dv.get("period") or "")[:10]
            if d and sp.get("value"):
                out[d] = out.get(d, 0) + float(sp["value"])
    return out


def _month_chunks(day0: str, day1: str):
    """[day0, day1] 按自然月切块（每月一个请求，≤31 天符合接口上限）"""
    import datetime
    d0, d1 = datetime.date.fromisoformat(day0), datetime.date.fromisoformat(day1)
    while d0 <= d1:
        nxt = (d0.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)  # 下月 1 号
        yield d0.isoformat(), min(nxt - datetime.timedelta(days=1), d1).isoformat()
        d0 = nxt


async def _char_day_secs(mtype: int, mid: str, cid: str) -> dict[str, float]:
    """角色全史 {日期: 秒数}：已封存日期走缓存，只补抓封存点前 ~10 天到今天的窗口"""
    import datetime
    _load_time_cache()
    ent = _time_cache.setdefault(f"{mid}|{cid}", {"days": {}, "done": ""})
    days: dict = ent.setdefault("days", {})
    today = datetime.date.today()
    start = min((s["start"][:10] for s in SEASONS), default=today.isoformat())
    done = ent.get("done") or ""
    if done:
        back = (datetime.date.fromisoformat(done)
                - datetime.timedelta(days=_TIME_TAIL_DAYS)).isoformat()
        fetch_from = max(start, back)
    else:
        fetch_from = start
    for a, b in _month_chunks(fetch_from, today.isoformat()):
        days.update(await _fetch_daily_secs(mtype, mid, cid, a, b))
        # 封存点推进到「窗口结束」与「今天-10 天」的较早者（当前月只封到今天-10）
        seal = min(datetime.date.fromisoformat(b), today - datetime.timedelta(days=_TIME_TAIL_DAYS))
        if seal.isoformat() > (ent.get("done") or ""):
            ent["done"] = seal.isoformat()
        _save_time_cache()
    return days


def _season_hours(s: dict, day_maps: list[dict]) -> float:
    """把各角色日表按赛季窗口合计成小时；end 是 2099 哨兵（当前赛季）时算到今天"""
    import datetime
    st = (s.get("start") or "")[:10]
    en = (s.get("end") or "")[:10]
    if not en or en >= "2099":
        en = datetime.date.today().isoformat()
    sec = sum(v for dm in day_maps for d, v in dm.items() if st <= d <= en)
    return sec / 3600.0


async def _char_stats_full(mtype: int, mid: str, cid: str, on_batch=None) -> dict:
    """单角色的历史统计总表（合并多次分批请求后的 Response）。

    modes 参数一次塞太多会被官方接口打回（ErrorCode 3），按 15 个一组分批再去重合并。
    合并后的 Response 既含各模式的 allTime.secondsPlayed（算时长），
    也含 allPvP / allPvE / pvecomp_gambit 等聚合条目（算击杀 / KD）。
    on_batch: 每批回调 on_batch(已完成批数, 总批数)
    """
    modes = list(_career_modes())
    out: dict = {}
    batches = list(range(0, len(modes), 15))
    for bi, i in enumerate(batches, 1):
        r = await client().get(
            f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
            params={"groups": "General",
                    "modes": ",".join(str(m) for m in modes[i:i + 15])})
        resp = r.json()
        if resp.get("ErrorCode") != 1:
            continue
        for key, v in (resp.get("Response") or {}).items():
            out.setdefault(key, v)
        if on_batch:
            on_batch(bi, len(batches))
    return out


def _mode_hours(stats: dict) -> dict:
    """合并后的统计表 → {modeType: 小时}（只保留有时长的模式）"""
    key2mt = _mode_key_map()
    out: dict = {}
    for key, v in (stats or {}).items():
        mt = key2mt.get(key)
        if mt is None or mt in out:
            continue
        sp = (((v.get("allTime") or {}).get("secondsPlayed") or {})
              .get("basic", {}).get("value"))
        if sp:
            out[mt] = sp / 3600.0
    return out


@_traced(lambda name: f"/生涯 {name}")
async def career_report(name: str) -> dict:
    """生涯面板数据：分赛季等级 + 分职业 / 分模式时长 + 三模式生涯聚合。

    全程只打 GetProfile / GetHistoricalStats，不逐场拉 PGCR，所以是秒级出图。
    """
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                           params={"components": "100,200,202"})
    resp = r.json()
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "Bungie API 错误"))
    R = resp["Response"]
    chars_raw = (R.get("characters") or {}).get("data") or {}
    if not chars_raw:
        raise LookupError(f"{member['display']} 档案下没有角色")
    prog_raw = (R.get("characterProgressions") or {}).get("data") or {}
    pdata = (R.get("profile") or {}).get("data") or {}

    chars, stat_list = [], []
    day_maps: list[dict] = []
    season_rank: dict[int, int] = {}
    rank_entries: dict[int, dict[int, list]] = {}  # 赛季号 → pass条目下标 → [奖励档, 声望档]
    # 每个角色要把「全部分模式」的 General 统计分 15 个一批拉完，是本地/生涯里
    # 最耗时的一段（3 角色 × 5 批），日志里给条进度 + 预估。
    nchars = len(chars_raw)
    nb = max(1, (len(_career_modes()) + 14) // 15)
    total_steps = nchars * nb
    disp = f"{member['display']}#{fmt_code(member['code'])}"
    log_progress(f"career:{mid}", 0, total_steps, label=f"/生涯 {disp}", force=True,
                 extra=f"拉取 {nchars} 个角色的分模式历史统计（每角色 {nb} 批）")
    for ci, (cid, c) in enumerate(chars_raw.items(), 1):
        st = await _char_stats_full(
            mtype, mid, cid,
            on_batch=lambda bi, bn, ci=ci: log_progress(
                f"career:{mid}", (ci - 1) * nb + bi, total_steps,
                label=f"/生涯 {disp}", extra=f"角色 {ci}/{nchars} · 第 {bi}/{bn} 批"))
        stat_list.append(st)
        # 每日在场秒数（首跑要分月补抓全史，之后只补最近 ~10 天）
        day_maps.append(await _char_day_secs(mtype, mid, cid))
        chars.append({
            "class": CLASS_NAMES.get(c["classType"], "?"),
            "light": c["light"],
            "minutes": int(c.get("minutesPlayedTotal", 0)),
            "emblem": BASE + c.get("emblemPath", ""),
            "emblem_bg": BASE + c.get("emblemBackgroundPath", ""),
            "modes": _mode_hours(st),
        })
        prog = ((prog_raw.get(cid) or {}).get("progressions")) or {}
        for h, v in prog.items():
            hit = _SEASON_PROG.get(int(h))
            if not hit:
                continue
            num, is_pres, pidx = hit
            lvl = int(v.get("level") or 0)
            b = rank_entries.setdefault(num, {}).setdefault(pidx, [0, 0])
            b[1 if is_pres else 0] = max(b[1 if is_pres else 0], lvl)

    for num, entries in rank_entries.items():
        def _rank_of(rp, _num=int(num)):
            # S27 起统一轨：奖励档一路往上数（可超 100），声望档是迁移遗留不再另加
            rew, pres = rp
            return rew if (_num >= 27 and rew > 100) else rew + pres

        main = _SEASON_PASS_MAIN.get(num, 0)
        if main in entries:
            rk = _rank_of(entries[main])
        else:
            rk = max(_rank_of(rp) for rp in entries.values())
        season_rank[num] = max(season_rank.get(num, 0), rk)

    chars.sort(key=lambda c: -c["minutes"])
    # 只加字段不删字段：旧调用方（角色卡等）拿 dict(s) 依旧兼容
    seasons = [dict(s, days=_season_days(s), rank=season_rank.get(int(s["number"]), 0),
                    time_h=round(_season_hours(s, day_maps), 1))
               for s in SEASONS]

    pvp = _sum(stat_list, "allPvP")
    pve = _sum(stat_list, "allPvE")
    gambit = _sum(stat_list, "pvecomp_gambit")
    gambit.pop("kd", None)
    if gambit.get("deaths"):
        gambit["kd"] = gambit["kills"] / gambit["deaths"]

    return {
        "display": f"{member['display']}#{fmt_code(member['code'])}",
        "guardian_rank": pdata.get("currentGuardianRank") or 0,
        "max_guardian_rank": pdata.get("lifetimeHighestGuardianRank") or 0,
        "last_played": _cn8((pdata.get("dateLastPlayed") or "")[:16].replace("T", " ")),
        "max_light": max(c["light"] for c in chars),
        "total_playtime": sum(c["minutes"] for c in chars),
        "emblem_bg": chars[0]["emblem_bg"],
        "chars": chars,
        "seasons": seasons,
        "pvp": pvp, "pve": pve, "gambit": gambit,
    }



# ---------- Manifest 索引 ----------
_weapons = json.load(open(_idx_file("weapons.json"), encoding="utf-8"))
_weapons_full = json.load(open(_idx_file("weapons_full.json"), encoding="utf-8"))
_perks = json.load(open(_idx_file("perks.json"), encoding="utf-8"))
try:
    _perk_ci = json.load(open(_idx_file("perk_ci.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    _perk_ci = {}
_armor_sets = json.load(open(_idx_file("armor_sets.json"), encoding="utf-8"))
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


try:  # 武器版本表（build_weapon_versions.py 生成）：hash → {season, event}；同名多版本用
    _weapon_versions = json.load(open(_idx_file("weapon_versions.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    _weapon_versions = {}
try:  # 赛季号 → 官方英文名（Bungie 已不再出中文赛季定义）
    _season_names = json.load(open(_idx_file("season_names.json"), encoding="utf-8"))
except Exception:  # noqa: BLE001
    _season_names = {}


def season_tag(n: int) -> str:
    """赛季显示标签：0=首发，正数=S 号（官方英文名太长，卡片里用 S 号）"""
    return "首发" if n <= 0 else f"S{n}"


def season_name(n: int) -> str:
    """赛季官方英文名（无则空串）"""
    return (_season_names.get(str(n)) or {}).get("name", "")


def weapon_versions_by_name(name: str) -> list[dict]:
    """同名武器的全部版本，按赛季从旧到新排（同赛季按 hash 稳定排序）
    返回 [{'hash', 'season', 'event'}]，下标 0 = 版本 1"""
    items = [(h, w) for h, w in _weapons_full.items() if w["name"] == name]
    if not items:
        return []
    items.sort(key=lambda x: (_weapon_versions.get(x[0], {}).get("season", 0), x[0]))
    return [{"hash": h,
             "season": _weapon_versions.get(h, {}).get("season", 0),
             "event": _weapon_versions.get(h, {}).get("event", False)}
            for h, _ in items]


def search_weapons_full(q: str, limit: int = 24) -> list[dict]:
    q = q.lower().strip()
    out = [dict(w, hash=h) for h, w in _weapons_full.items() if q in w["name"].lower()]
    if out or not q:
        out.sort(key=lambda w: (not w["name"].lower().startswith(q), w["name"]))
    else:  # 原始遍历无命中 → 无符号归一化键重跑同一逻辑（· / ' / - 等符号可省略）
        nk = norm_key(q)
        if nk:
            out = [dict(w, hash=h) for h, w in _weapons_full.items()
                   if nk in norm_key(w["name"])]
            out.sort(key=lambda w: (not norm_key(w["name"]).startswith(nk), w["name"]))
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
    if not out and q:  # 无符号归一化兜底：精确优先于子串，顺序与原逻辑一致
        nk = norm_key(q)
        if nk:
            for h, w in _weapons.items():
                n = norm_key(w["name"])
                if n == nk:
                    out.insert(0, {"hash": h, **w})
                elif nk in n:
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
    if not exact and not part and q:  # 无符号归一化兜底
        nk = norm_key(q)
        if nk:
            for h, p in _perks.items():
                n = norm_key(p["name"])
                if n == nk:
                    exact.append({"hash": h, **p})
                elif nk in n:
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


def all_armor_sets() -> list[dict]:
    return _armor_sets


def _norm_set(q: str) -> str:
    """套装搜索归一化：NFKC、小写、剔除符号/空白（查询与套装名两侧同规则，
    「·」「-」等符号省略也能对上）、去掉结尾的 套/套装"""
    q = re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", str(q or "")).lower())
    return re.sub(r"(套装|套)$", "", q)


def _edit_dist(a: str, b: str) -> int:
    """编辑距离（套装别名模糊兜底用，词都很短，O(mn) 足够）"""
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def search_armor_sets(q: str) -> list[dict]:
    """护甲套装检索：套装名 / 别名精确命中 → 包含命中 → 模糊兜底（容 1-2 个字符的手滑，
    如 vog 打成 vod）；全命中为空才走模糊，避免正常搜索被带偏"""
    q = _norm_set(q)
    if not q:
        return []
    out = []
    for s in _armor_sets:
        names = {_norm_set(s["name"])} | {_norm_set(a) for a in s.get("aliases", [])}
        src = _norm_set(s.get("source") or "")
        if q in names or (src and q == src):   # 来源（副本名）也可精确搜
            out.insert(0, s)
        elif any(q in n for n in names):
            out.append(s)
    if out:
        return out
    fuzzy: list[tuple[int, int, dict]] = []  # (距离, 名字长度, 套装)
    for s in _armor_sets:
        for n in {_norm_set(s["name"])} | {_norm_set(a) for a in s.get("aliases", [])}:
            if not n:
                continue
            d = _edit_dist(q, n)
            # 容错随词长放宽：2-5 字符容 1 个，6+ 容 2 个（只在全空时兜底）
            if d <= (1 if len(n) >= 2 else 0) + (1 if len(n) >= 6 else 0):
                fuzzy.append((d, len(n), s))
    fuzzy.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in fuzzy[:3]]


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


@_traced("对局详情")
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
        _pname = f"{info.get('bungieGlobalDisplayName', info.get('displayName', '?'))}#{fmt_code(info.get('bungieGlobalDisplayNameCode', ''))}"
        harvest_player(_pname, info.get("membershipId", ""), info.get("membershipType"))
        entries.append({
            "name": _pname,
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
        "period_cn": _cn8((d.get("period") or "")[:16].replace("T", " ")),
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


def _cn8(s: str) -> str:
    """UTC 'YYYY-MM-DD HH:MM' → 北京时间同格式（非法串原样返回）"""
    import datetime
    try:
        return (datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
                + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


def _ts_shift(ts: str, secs: int) -> str:
    import datetime
    try:
        return (datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M")
                + datetime.timedelta(seconds=secs)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


# 突袭/地牢按发售先后排序（/raid /地牢 卡片的展示顺序）
_RAID_ORDER: tuple[str, ...] = (
    # —— 突袭 ——
    "利维坦", "世界吞噬者，利维坦", "利维坦，星之塔", "最后一愿", "救赎花园",
    "忧愁王冠", "深岩墓室", "玻璃拱顶", "门徒誓约", "国王的陨落", "梦魇根源",
    "克洛塔的末日", "救赎的边缘", "永恒沙漠", "永恒沙漠（史诗）",
    # —— 地牢 ——
    "破碎王座", "异端深渊", "预言", "贪婪之握", "二象性", "守望者尖塔",
    "深渊机灵", "战争领主的废墟", "晚星之主", "分离教义", "平衡",
)
_RAID_ORDER_IDX = {n: i for i, n in enumerate(_RAID_ORDER)}

# 首日/首周判定用的发售时刻（UTC 'YYYY-MM-DD HH:MM'，Bungie 常规 17:00 上线；
# 地牢多为 18:00）。没把握的时刻宁缺毋滥：不在此表里的副本不出首日/首周徽章。
_RAID_RELEASE_UTC: dict[str, str] = {
    "利维坦": "2017-09-13 17:00",
    "世界吞噬者，利维坦": "2018-05-11 17:00",
    "利维坦，星之塔": "2018-07-14 17:00",
    "最后一愿": "2018-09-14 17:00",
    "救赎花园": "2019-10-05 17:00",
    "忧愁王冠": "2019-06-04 23:00",
    "深岩墓室": "2020-11-21 17:00",
    "玻璃拱顶": "2021-05-22 17:00",
    "门徒誓约": "2022-03-05 17:00",
    "国王的陨落": "2022-08-26 17:00",
    "梦魇根源": "2023-03-10 17:00",
    "克洛塔的末日": "2023-09-01 17:00",
    "救赎的边缘": "2024-06-07 17:00",
    "永恒沙漠": "2025-07-19 17:00",
    "永恒沙漠（史诗）": "2025-09-27 17:00",
    "破碎王座": "2018-12-14 18:00",
    "异端深渊": "2019-11-05 18:00",
    "预言": "2020-06-09 18:00",
    "贪婪之握": "2021-12-07 17:00",
    "二象性": "2022-05-27 17:00",
    "守望者尖塔": "2022-12-09 17:00",
    "深渊机灵": "2023-05-26 17:00",
    "战争领主的废墟": "2023-12-01 17:00",
    "晚星之主": "2024-10-11 17:00",
    "分离教义": "2025-02-07 17:00",
    "平衡": "2025-12-13 17:00",
}


# 首日排名数据源：raid.report / dungeon.report 的后端 api.raidreport.dev（无 CF，可直接 HTTP）。
# 前端枚举里挖出来的 worldsfirst 首日榜：/raid|dungeon/leaderboard/worldsfirst/{slug}[/版本]
# ？membershipId=... → 该玩家首日通关的名次（404=没打过首日）。API 慢（冷启动 ~20s+）、
# 连续快查会吃 CF 403 —— 所以结果落盘缓存（首日名次永不变化）+ 进程内去重 + 失败不缓存。
_RR_BASE = "https://api.raidreport.dev"
_RR_SLUG: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # 副本名: (kind, slug, 版本候选)  版本候选依次试，命中即缓存（''=不带版本段）
    "利维坦": ("raid", "leviathan", ("",)),
    "世界吞噬者，利维坦": ("raid", "eaterofworlds", ("",)),
    "利维坦，星之塔": ("raid", "spireofstars", ("",)),
    "最后一愿": ("raid", "lastwish", ("",)),
    "救赎花园": ("raid", "gardenofsalvation", ("",)),
    "忧愁王冠": ("raid", "crownofsorrow", ("",)),
    "深岩墓室": ("raid", "deepstonecrypt", ("",)),
    "玻璃拱顶": ("raid", "vaultofglass", ("",)),
    "门徒誓约": ("raid", "vowofthedisciple", ("",)),
    "国王的陨落": ("raid", "kingsfall", ("",)),
    "梦魇根源": ("raid", "rootofnightmares", ("",)),
    "克洛塔的末日": ("raid", "crotasend", ("",)),
    "救赎的边缘": ("raid", "salvationsedge", ("",)),
    "永恒沙漠": ("raid", "desertperpetual", ("",)),
    "永恒沙漠（史诗）": ("raid", "desertperpetual", ("epic", "epiccontest")),
    "破碎王座": ("dungeon", "shatteredthrone", ("",)),
    "异端深渊": ("dungeon", "pitofheresy", ("",)),
    "预言": ("dungeon", "prophecy", ("",)),
    "贪婪之握": ("dungeon", "graspofavarice", ("",)),
    "二象性": ("dungeon", "duality", ("",)),
    "守望者尖塔": ("dungeon", "spireofthewatcher", ("",)),
    "深渊机灵": ("dungeon", "ghostsofthedeep", ("",)),
    "战争领主的废墟": ("dungeon", "warlordsruin", ("",)),
    "晚星之主": ("dungeon", "vespershost", ("",)),
    "分离教义": ("dungeon", "sundereddoctrine", ("",)),
    "平衡": ("dungeon", "equilibrium", ("",)),
}
_RR_RANK_PATH = os.path.join("raidreport_ranks.json")
_RR_RANKS: dict | None = None
_RR_INFLIGHT: set[str] = set()
# 大师组的版本段（有大师首日赛的副本才有数据，404 就是没有）
_RR_MASTER_VERSIONS: tuple[str, ...] = ("master",)


def _rr_ranks() -> dict:
    global _RR_RANKS
    if _RR_RANKS is None:
        try:
            _RR_RANKS = json.load(open(_RR_RANK_PATH, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _RR_RANKS = {}
    return _RR_RANKS


def _rr_save() -> None:
    try:
        json.dump(_rr_ranks(), open(_RR_RANK_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


_NOT_RANKED = {"rank": 0}   # 确认没打过首日（也缓存，省得反复打 API）


async def _rr_fetch_json(url: str, mid: str) -> tuple[dict | None, bool]:
    """api.raidreport.dev GET：先直连 httpx；被 CF 盾拦/网络错时借用户的调试 Edge
    （weapon_usage 同款 CDP 通道，9222 在线才走）。返回 (json, 是否为 API 明确的 404)。

    注意 CDP 路径必须「先开 raid.report 页、再页内 fetch」：直接 goto API 地址是顶层导航，
    缺 Origin/sec-fetch 头，对方 Lambda 会回 Bad Request。
    (None, False) = 两条通道都不通，调用方不要缓存结果。"""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 404:
                return None, True
            if r.status_code == 200:
                try:
                    return r.json(), False
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
            try:
                ctx = b.contexts[0] if b.contexts else await b.new_context()
                page = await ctx.new_page()
                try:
                    await page.goto("https://raid.report/steam/" + mid,
                                    timeout=60000, wait_until="commit")
                    # 过盾：中英文挑战页标题都认（真实浏览器托管挑战一般几秒自动过）
                    for _ in range(45):
                        t = await page.title()
                        if t and "moment" not in t.lower() and "请稍候" not in t:
                            break
                        await page.wait_for_timeout(1000)
                    raw = await page.evaluate(
                        """async u => { const r = await fetch(u, {credentials:'omit'});
                           return {status: r.status, body: await r.text()}; }""", url)
                finally:
                    await page.close()
            finally:
                await b.close()   # 只断开 CDP，不杀浏览器
        if raw["status"] == 404:
            return None, True
        if raw["status"] != 200:
            return None, False
        return json.loads(raw["body"]), False
    except Exception:  # noqa: BLE001
        return None, False


async def _day_one_rank(mtype: int, mid: str, base: str, is_master: bool) -> dict | None:
    """查首日名次 → {"rank": n, "total": 总队数}；没打过首日/查询失败 → None。

    200 里找到本号条目、或 API 明确 404（没在 24h 内通关）→ 落盘永久缓存；
    403（CF）/超时/其它网络错 → 不缓存，下次再试。"""
    info = _RR_SLUG.get(base)
    if not info:
        return None
    kind, slug, ver_cands = info
    if is_master:
        ver_cands = _RR_MASTER_VERSIONS
    ent = _rr_ranks().get(f"{mtype}:{mid}", {})
    inflight = f"{mtype}:{mid}:{kind}:{slug}:{'M' if is_master else 'S'}"
    if inflight in _RR_INFLIGHT:
        return None
    # 各版本候选：缓存里有名次直接用；缓存过「无排名」的跳过；剩下的才打 API
    need = []
    for ver in ver_cands:
        hit = ent.get(f"{base}|{ver}" if ver else base)
        if hit is not None and hit.get("rank"):
            return hit
        if hit is None:
            need.append(ver)
    if not need:
        return None
    _RR_INFLIGHT.add(inflight)
    try:
        for ver in need:
            k = f"{base}|{ver}" if ver else base
            path = f"/{kind}/leaderboard/worldsfirst/{slug}" + (f"/{ver}" if ver else "")
            url = f"{_RR_BASE}{path}?membershipId={mid}"
            d, not_found = await _rr_fetch_json(url, str(mid))
            if d is None:
                if not_found:
                    # API 的明确回答：该号没在首日窗口内通关（也是终态，缓存）
                    _rr_ranks().setdefault(f"{mtype}:{mid}", {})[k] = _NOT_RANKED
                    _rr_save()
                    continue
                return None
            resp = (d.get("response") or {})
            mine = None
            for e in resp.get("entries") or []:
                for u in e.get("destinyUserInfos") or []:
                    if str(u.get("membershipId")) == str(mid):
                        mine = e
                        break
                if mine:
                    break
            if not mine:
                # 有条目但没本号：拿不准（分页/跨平台），不缓存
                return None
            out = {"rank": int(mine.get("rank") or 0),
                   "total": int((resp.get("metadata") or {}).get("totalResults") or 0)}
            _rr_ranks().setdefault(f"{mtype}:{mid}", {})[k] = out
            _rr_save()
            return out
        return None
    finally:
        _RR_INFLIGHT.discard(inflight)


async def _pgcr_run_info(instance: str) -> dict:
    """PGCR 局面信息：fresh=是否从头开始打、accounts=全程出现过的账号数、private=私局。

    尾王检查点进去的局官方直接给 activityWasStartedFromBeginning=False；
    字段缺失/拉取失败返回 {}（上层按「未知」处理，维持旧口径不瞎扣）。"""
    try:
        r = await client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{instance}/")
        d = json.loads(r.content.decode("utf-8-sig"))
        if d.get("ErrorCode") != 1:
            return {}
        resp = d.get("Response") or {}
        accs = set()
        for e in resp.get("entries") or []:
            mid_ = str(((e.get("player") or {}).get("destinyUserInfo") or {}).get("membershipId") or "")
            if mid_:
                accs.add(mid_)
        return {
            "fresh": resp.get("activityWasStartedFromBeginning"),
            "accounts": len(accs) or len(resp.get("entries") or []),
            "private": bool((resp.get("activityDetails") or {}).get("isPrivate")),
        }
    except Exception:  # noqa: BLE001
        return {}


@_traced(lambda name, mode: (f"/地牢 {name}" if mode == 82 else f"/raid {name}"))
async def raid_report(name: str, mode: int) -> dict:
    """Raid(4)/地牢(82) 报告：跨角色合并对局，按副本分组统计（标准与大师各成一组）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})

    seen, matches = set(), []
    rname = "地牢" if mode == 82 else "raid"
    disp = f"{member['display']}#{fmt_code(member['code'])}"
    nchars = len(chars) or 1
    log_progress(f"raid:{mid}:{mode}", 0, nchars * 40, label=f"/{rname} {disp}", force=True,
                 extra="翻取副本对局历史（每人最多 40 页 × 250 场）")
    for ci, cid in enumerate(chars, 1):
        # 翻页拿全：早前只翻 3 页（750 场），老记录的低人通关会被截掉
        page = 0
        while page < 40:
            acts = await activity_history(mtype, mid, cid, mode, count=250, page=page)
            for m in acts:
                key = m["instance"] or f"{m['ref']}{m['period']}"
                if key not in seen:
                    seen.add(key)
                    matches.append(m)
            if len(acts) < 250:
                break
            page += 1
            log_progress(f"raid:{mid}:{mode}", (ci - 1) * 40 + page, nchars * 40,
                         label=f"/{rname} {disp}",
                         extra=f"角色 {ci}/{nchars} · 第 {page + 1} 页 · 已收 {len(matches)} 场")
    log_progress(f"raid:{mid}:{mode}", nchars * 40, nchars * 40, label=f"/{rname} {disp}",
                 force=True, extra=f"历史翻取完成，共 {len(matches)} 场，开始统计")
    matches.sort(key=lambda m: m["period"], reverse=True)
    # 展示用北京时间（period 保留 UTC 原值给首日/首周判定）
    for m in matches:
        m["period_cn"] = _cn8(m["period"])
        m["full_run"], m["private"], m["low_accounts"] = True, False, m["player_count"]

    # 特殊通关复核：0 死亡通关 / 低人通关才拉 PGCR —— 判断「是否从头开始打」。
    # 尾王检查点进去通掉尾王（哪怕 0 死）官方 PGCR 给 activityWasStartedFromBeginning=False，
    # 不算全程无暇（用户 2026-10-02 指定口径）；私局（自定义装载）也不进特殊徽章。
    cand = [m for m in matches if m["completed"] and (m["deaths"] == 0 or 0 < m["player_count"] <= 3)]
    if cand:
        log_progress(f"raid:{mid}:{mode}", 0, len(cand), label=f"/{rname} {disp}", force=True,
                     extra=f"复核 {len(cand)} 场特殊通关（全程 / 低人口径）")
        for i in range(0, len(cand), 6):
            chunk = cand[i:i + 6]
            infos = await asyncio.gather(*[_pgcr_run_info(m["instance"]) for m in chunk])
            for m, info in zip(chunk, infos):
                if info:
                    m["full_run"] = info["fresh"] is not False
                    m["private"] = info["private"]
                    # 账号数取 PGCR 全程出现过的账号 与 场上人数 的较大者：
                    # 6 人团中途退到剩 2 人通关，靠 PGCR 账号数戳穿不算双人；
                    # 老对局 PGCR 被官方裁剪只剩 1 条时，回落历史 player_count
                    m["low_accounts"] = max(info["accounts"], m["player_count"])
            log_progress(f"raid:{mid}:{mode}", min(i + 6, len(cand)), len(cand),
                         label=f"/{rname} {disp}")

    # 大师单独成组：标准/普通与大师的 无暇/单人/双人/三人 口径不该混在一起算
    groups: dict[tuple[str, bool], dict] = {}
    for m in matches:
        base, diff = split_activity(m["name"])
        m["base"], m["diff"] = base, diff
        is_master = diff == "大师"
        rel = _RAID_RELEASE_UTC.get(base) or ""
        g = groups.setdefault((base, is_master), {
            "name": base, "master_mode": is_master, "ref": m["ref"], "pgcr": m["pgcr"],
            "plays": 0, "clears": 0, "best": None, "last": "",
            "flawless": 0, "solo": 0, "duo": 0, "trio": 0,
            "solo_fl": 0, "duo_fl": 0, "trio_fl": 0, "master": 0, "diffs": [],
            "day_one": 0, "week_one": 0,
            "rel_d1": _ts_shift(rel, 24 * 3600) if rel else "",
            "rel_w1": _ts_shift(rel, 7 * 24 * 3600) if rel else "",
        })
        if diff and diff not in g["diffs"]:
            g["diffs"].append(diff)
        g["plays"] += 1  # 参与次数：含没打完的（中途退、卡机制、只打到一半）
        if m["completed"]:
            g["clears"] += 1
            # 最近 = 最后一次【通关】的时间。对局是倒序遍历的，必须取 max，
            # 直接赋值会把最旧一场通关留在最后（老版本「最近」一直显示成最早通关就是这个坑）
            g["last"] = max(g["last"], m["period_cn"])
            pc = m["player_count"]
            accs = m["low_accounts"]
            # 全程无暇：0 死亡 + 从头开始 + 非私局
            fl = m["deaths"] == 0 and m["full_run"] and not m["private"]
            if fl:
                g["flawless"] += 1
            if pc and not m["private"]:
                # 低人 = 全程出现过的账号数（raid.report 的 accountCount 口径）
                if accs == 1:
                    g["solo"] += 1
                if accs <= 2:
                    g["duo"] += 1
                if accs <= 3:
                    g["trio"] += 1
                if fl:
                    if accs == 1:
                        g["solo_fl"] += 1
                    if accs <= 2:
                        g["duo_fl"] += 1
                    if accs <= 3:
                        g["trio_fl"] += 1
            if is_master:
                g["master"] += 1
            # 首日/首周：以对局开始时刻（UTC）落在发售窗口内为准（私局不算）
            if rel and not m["private"]:
                if g["rel_d1"] >= m["period"] >= rel:
                    g["day_one"] += 1
                if g["rel_w1"] >= m["period"] >= rel:
                    g["week_one"] += 1
            if g["best"] is None or m["duration"] < g["best"]:
                g["best"] = m["duration"]
    done = [m for m in matches if m["completed"]]
    _unk = len(_RAID_ORDER_IDX)
    std = sorted((g for g in groups.values() if not g["master_mode"]),
                 key=lambda g: (_RAID_ORDER_IDX.get(g["name"], _unk), g["name"]))
    mst = sorted((g for g in groups.values() if g["master_mode"]),
                 key=lambda g: (_RAID_ORDER_IDX.get(g["name"], _unk), g["name"]))
    _mfl = lambda m: m["deaths"] == 0 and m["full_run"] and not m["private"]
    # 首日排名：只查真的在首日窗口里有通关的组（API 慢且限流，靠磁盘缓存兜底）
    for g in (*std, *mst):
        if g["day_one"] > 0:
            try:
                g["d1_rank"] = await _day_one_rank(mtype, mid, g["name"], g["master_mode"])
            except Exception:  # noqa: BLE001
                g["d1_rank"] = None
    return {
        "display": f"{member['display']}#{fmt_code(member['code'])}",
        "total_clears": len(done),
        "total_plays": len(matches),
        "flawless": sum(1 for m in done if _mfl(m)),
        "solo_fl": sum(1 for m in done if _mfl(m) and m["low_accounts"] == 1),
        "duo_fl": sum(1 for m in done if _mfl(m) and m["low_accounts"] <= 2),
        "trio_fl": sum(1 for m in done if _mfl(m) and 0 < m["low_accounts"] <= 3),
        "master": sum(1 for m in done if m["diff"] == "大师"),
        "matches": matches,
        "raids": std,
        "raids_master": mst,
    }


@_traced(lambda name, per_char=50: f"/战绩 {name}")
async def history_report(name: str, per_char: int = 50) -> dict:
    """全模式最近对局流（合并所有角色）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    matches = []
    disp = f"{member['display']}#{fmt_code(member['code'])}"
    nch = len(chars) or 1
    log_progress(f"history:{mid}", 0, nch, label=f"/战绩 {disp}", force=True,
                 extra=f"拉取每个角色最近 {per_char} 场对局历史")
    for ci, cid in enumerate(chars, 1):
        matches += await activity_history(mtype, mid, cid, 0, count=per_char)
        log_progress(f"history:{mid}", ci, nch, label=f"/战绩 {disp}",
                     extra=f"角色 {ci}/{nch} 完成 · 已收 {len(matches)} 场")
    matches.sort(key=lambda m: m["period"], reverse=True)
    for m in matches:
        m["period_cn"] = _cn8(m["period"])
    return {"display": f"{member['display']}#{fmt_code(member['code'])}", "matches": matches}


def filter_matches(matches: list[dict], month: str = "", base: str = "",
                   diff: str = "") -> list[dict]:
    out = matches
    if base:
        out = [m for m in out if m.get("base") == base]
    if diff:
        out = [m for m in out if m.get("diff") == diff]
    if month:
        # 月份按北京时间算（raid 链路的对局带 period_cn；旧数据没有该字段退回 UTC period）
        out = [m for m in out if (m.get("period_cn") or m["period"]).startswith(month)]
    return out


# ---------- 后台任务（PVP 生涯武器，带进度） ----------

# ---------- 耗时任务的进度日志 ----------
# 需要拉接口、要跑一会儿的活儿，光在面板进度条上看不见——控制台/日志文件里也要能一眼
# 看出跑到哪了、还剩多久。统一走下面这几个函数输出「进度条 + 已用 + 预估剩余」。
_PROG_T0: dict[str, float] = {}   # key → 上次打印时刻（节流用）
_PROG_PCT: dict[str, float] = {}  # key → 上次打印的百分比


def log_progress(key: str, done: int, total: int, label: str = "",
                 extra: str = "", force: bool = False, min_gap: float = 2.0,
                 min_pct: float = 2.0) -> None:
    """打一行进度日志：`[进度] 标签 [████░░░░] 62.0% (312/503) · 已用 0:48 · 预计剩余 0:29（约 12:34:56 完成）`

    key 同一个任务复用（用于记录起始时刻与节流）；done/total 为 0 时不算百分比，
    只报「已用」。默认「距上次 ≥2 秒 或 百分比涨了 ≥2」才打印，避免刷屏；
    force=True 时无条件打印（阶段开始/结束用）。
    """
    now = time.time()
    t0 = _PROG_T0.setdefault(key + "#t0", now)
    pct = (done * 100.0 / total) if total else 0.0
    if not force:
        last_t = _PROG_T0.get(key, 0.0)
        last_p = _PROG_PCT.get(key, -999.0)
        if pct < 100 and now - last_t < min_gap and pct - last_p < min_pct:
            return
    _PROG_T0[key] = now
    _PROG_PCT[key] = pct
    if len(_PROG_T0) > 400:  # 长跑实例里别让这两个 dict 一直涨
        _PROG_T0.clear()
        _PROG_PCT.clear()
        _PROG_T0[key + "#t0"] = t0
        _PROG_T0[key] = now
        _PROG_PCT[key] = pct

    el = now - t0
    head = f"{label} " if label else ""
    if total:
        filled = int(round(max(0.0, min(100.0, pct)) / 100 * 22))
        bar = "█" * filled + "░" * (22 - filled)
        if done:
            eta = el / done * (total - done)
            tail = (f" · 已用 {_hm(el)} · 预计剩余 {_sec_text(eta)}"
                    f"（约 {time.strftime('%H:%M:%S', time.localtime(now + eta))} 完成）"
                    + (f" · 速度 {el / done:.1f}s/项" if el >= 3 else ""))
        else:  # 刚拿到总量、还没跑第一条，给不出预估
            tail = f" · 已用 {_hm(el)} · 预估中"
        line = f"[进度] {head}[{bar}] {pct:5.1f}% ({done}/{total}){tail}"
    else:
        line = f"[进度] {head}已用 {_hm(el)}"
    if extra:
        line += f" · {extra}"
    print(line, flush=True)


def log_stage(key: str, text: str) -> None:
    """阶段性提示（开始拉某个接口 / 翻到第几页），无百分比时用"""
    print(f"[进度] [{time.strftime('%H:%M:%S')}] {text}", flush=True)
    _PROG_T0.setdefault(key + "#t0", time.time())


def _sec_text(sec: float) -> str:
    """0:29 / 1:05 / 12:30，便于在日志里扫一眼"""
    return _hm(sec)


def _hm(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# 全历史翻页扫描（热力图这类「翻到 2019-06 为止」的任务）没有天然总量：
# 按「已经扫到多早」相对 2019-06 → 今天 的跨度折算成百分比，进度条与预估才有意义。
_SCAN_FLOOR = "2019-06-01"


def _scan_pct(oldest: str) -> float:
    import datetime
    try:
        o = datetime.date.fromisoformat(oldest[:10])
        f = datetime.date.fromisoformat(_SCAN_FLOOR)
        t = datetime.date.today()
    except (ValueError, TypeError):
        return 0.0
    span = (t - f).days or 1
    return max(0.0, min(100.0, (t - o).days * 100.0 / span))


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


def _job_label(jid: str) -> str:
    j = JOBS.get(jid) or {}
    name = j.get("name") or ""
    return f"{j.get('label') or '任务'} {name}".strip()


def _job_log(jid: str, extra: str = "", force: bool = False) -> None:
    """后台任务 → 进度日志（done/total 直接取自 JOBS）"""
    j = JOBS.get(jid) or {}
    log_progress(jid, int(j.get("done") or 0), int(j.get("total") or 0),
                 label=_job_label(jid), extra=extra, force=force)


def _job_start_log(jid: str, note: str = "") -> None:
    j = JOBS.get(jid) or {}
    pos = queue_position(jid)
    tail = f"（前面还有 {pos - 1} 位在排队）" if pos > 0 else ""
    print(f"[任务] {time.strftime('%H:%M:%S')} ▶ 开始：{_job_label(jid)}"
          f"{' · ' + note if note else ''}{tail}", flush=True)
    _PROG_T0[jid + "#t0"] = time.time()
    _PROG_PCT.pop(jid, None)


def _job_end_log(jid: str, ok: bool = True, note: str = "") -> None:
    t0 = _PROG_T0.get(jid + "#t0")
    used = f" · 总用时 {_hm(time.time() - t0)}" if t0 else ""
    mark = "✔ 完成" if ok else "✘ 失败"
    print(f"[任务] {time.strftime('%H:%M:%S')} {mark}：{_job_label(jid)}{used}"
          f"{' · ' + note if note else ''}", flush=True)


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
    _job_start_log(jid)
    asyncio.get_event_loop().create_task(_run_queued(jid, factory))


async def _run_queued(jid: str, factory):
    global _JOB_RUNNING
    try:
        await factory()
    except Exception as exc:  # noqa: BLE001  兜底：别让队列卡死
        JOBS.get(jid, {}).update(status="error", error=str(exc))
        _job_end_log(jid, ok=False, note=str(exc))
    else:
        _job_end_log(jid)
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
                           skip_modes: frozenset = frozenset(),
                           on_page=None) -> list[dict]:
    """收集对局（跨角色去重，新→旧）；since/until 为空串表示不限时间

    mode: 5=所有PVP 7=所有PVE；skip_modes 里的具体玩法会被丢掉
    on_page: 每翻完一页回调一次 on_page(已翻页数, 已收集场次)，用于打进度日志
    """
    seen, matches = set(), []
    for ci, cid in enumerate(chars):
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
            if on_page:
                on_page(ci + 1, len(chars), page, len(matches))
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
        def _on_page(ci, cn, page, n):
            log_progress(f"{jid}#collect", page, 0,
                         label=f"{_job_label(jid)} · 翻取对局历史",
                         extra=f"角色 {ci}/{cn} · 第 {page} 页 · 已收集 {n} 场",
                         min_gap=1.5, min_pct=0)

        log_stage(f"{jid}#collect", f"{_job_label(jid)}：开始翻取对局历史…")
        matches = await _collect_matches(mtype, mid, chars, mode, eff_since, until, cap,
                                         skip_modes, on_page=_on_page)
        if reuse and base_newest_full:  # 边界那天会重复枚举，按完整时间戳只留更新的
            matches = [m for m in matches if m["period"] > base_newest_full]
        if not matches:  # 没有新对局：有缓存就直接返回上次排名，否则返回空态
            log_stage(f"{jid}#collect", f"{_job_label(jid)}：没有新对局，直接出图")
            JOBS[jid].update(status="done", total=1, done=1, result=empty)
            return
        JOBS[jid].update(total=len(matches))
        log_progress(jid, 0, len(matches), label=_job_label(jid), force=True,
                     extra="开始逐场拉取对局明细")
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
                _job_log(jid)
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
                _job_log(jid)

        await asyncio.gather(*(one(m) for m in matches))
        save_seen_players()
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


# ---------- 宗师战绩（征服 / 先锋警戒） ----------
# 宗师类活动在对局历史里以活动名前缀区分难度档位（征服）或类型（宗师日落/警戒）；
# 征服是独立模式(18)不走 mode=7 过滤，所以用全量历史按名字筛

_GM_CONQUEST_TIERS = ("终极征服", "宗师征服", "大师征服", "专家征服")
_GM_NIGHTFALL_KWS = ("宗师日落", "日落: 宗师", "日落：宗师")
# 成就记录：完成赛季中心内所有宗师征服 / 所有终极征服 / 伟大征服者（镀金计数）
_GM_REC_CONQUEST = "340857458"
_GM_REC_ULTIMATE = "914587616"
_GM_REC_GILD = "4018593209"


def _gm_parse(name: str) -> tuple[str, str, str] | None:
    """活动名 → (类别, 副本名, 难度)；非宗师类返回 None"""
    for tier in _GM_CONQUEST_TIERS:
        if name.startswith(tier):
            strike = name[len(tier):].lstrip("：:")
            for suf in (": 自定义", "：自定义", ": 匹配", "：匹配"):
                strike = strike.replace(suf, "")
            return ("conquest", strike.strip(), tier[:-2])
    for kw in _GM_NIGHTFALL_KWS:
        if kw in name:
            strike = name.replace(kw, "").lstrip("：:")
            for suf in (": 自定义", "：自定义", ": 匹配", "：匹配"):
                strike = strike.replace(suf, "")
            return ("nightfall", strike.strip() or name, "宗师")
    return None


def _gm_bucket(matches: list[dict]) -> list[dict]:
    """按 (类别, 副本名, 难度) 聚合宗师对局：次数/通关/通关率/最快"""
    groups: dict[tuple, dict] = {}
    for m in matches:
        p = _gm_parse(m["name"])
        if not p:
            continue
        cat, strike, tier = p
        g = groups.setdefault(p, {
            "cat": cat, "strike": strike, "tier": tier,
            "attempts": 0, "clears": 0, "fastest": 0, "avg": 0,
            "last": ""})
        g["attempts"] += 1
        g["last"] = max(g["last"], _cn8(m["period"]))
        if m["completed"]:
            g["clears"] += 1
            dur = m["duration"]
            if dur > 0 and (not g["fastest"] or dur < g["fastest"]):
                g["fastest"] = dur
    out = list(groups.values())
    for g in out:
        g["rate"] = round(g["clears"] * 100.0 / g["attempts"], 1) if g["attempts"] else 0.0
    out.sort(key=lambda x: (0 if x["cat"] == "conquest" else 1, -x["attempts"]))
    return out


def _gm_avg_clears(matches: list[dict]) -> dict[tuple, int]:
    """每组的通关时长和 → 平均通关时长（对局历史页自带 duration，无需逐场 PGCR）"""
    sums: dict[tuple, list] = {}
    for m in matches:
        if not m["completed"] or m["duration"] <= 0:
            continue
        p = _gm_parse(m["name"])
        if p:
            sums.setdefault(p, []).append(m["duration"])
    return sums


async def start_gm_report(name: str, scope: str = "current", who: str = "") -> str | None:
    """宗师战绩后台任务：当前（或指定）赛季的征服/宗师警戒对局聚合"""
    member = await resolve_member(name)
    if not member:
        return None
    mtype, mid = member["mtype"], member["mid"]
    since, until, label = scope_window(scope)
    key = f"{mtype}:{mid}:gm:{since}:{until}"
    hit = _reuse_job(key)
    if hit:
        _mark_reused(hit, who)
        return hit
    profile = await get_profile(mtype, mid)
    chars = list(profile.get("characters", {}).get("data", {}))
    jid = f"{mid}_gm_{len(JOBS)}"
    _register_job(key, jid, {
        "done": 0, "total": 0, "status": "queued", "name":
        f"{member['display']}#{fmt_code(member['code'])}", "result": None,
        "kind": "gm", "who": who or "网页", "ts": time.time(),
        "label": f"宗师战绩（{label}）"})
    _enqueue_job(jid, lambda: _run_gm_job(jid, mtype, mid, chars, since, until, label, profile))
    return jid


async def _run_gm_job(jid: str, mtype: int, mid: str, chars: list[str],
                      since: str, until: str, label: str, profile: dict):
    try:
        JOBS[jid].update(status="running", total=3, done=0)
        log_progress(jid, 0, 3, label=_job_label(jid), force=True, extra="第 1/3 步：翻取活动历史")
        # 征服是独立模式，mode=0 全量历史再按活动名筛（历史页自带 completed/duration）
        matches = await _collect_matches(
            mtype, mid, chars, 0, since, until, 5000, frozenset(),
            on_page=lambda ci, cn, page, n: log_progress(
                f"{jid}#collect", page, 0, label=f"{_job_label(jid)} · 翻取活动历史",
                extra=f"角色 {ci}/{cn} · 第 {page} 页 · 已收集 {n} 场", min_gap=1.5, min_pct=0))
        JOBS[jid].update(done=1)
        log_progress(jid, 1, 3, label=_job_label(jid), force=True,
                     extra=f"第 2/3 步：读取成就记录（已扫 {len(matches)} 场）")
        rr = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                                params={"components": "900"})
        pr = _merged_records({"profileRecords": (_parse(rr).get("Response") or {}).get("profileRecords")})
        gild = 0
        for o in (pr.get(_GM_REC_GILD, {}).get("objectives") or []):
            gild = max(gild, int(o.get("progress") or 0))
        gm = [m for m in matches if _gm_parse(m["name"])]
        buckets = _gm_bucket(gm)
        avgs = _gm_avg_clears(gm)
        for g in buckets:
            ds = avgs.get((g["cat"], g["strike"], g["tier"]), [])
            g["avg"] = int(sum(ds) / len(ds)) if ds else 0
        result = {
            "display": JOBS[jid]["name"], "scope_label": label,
            "scanned": len(matches), "matches": len(gm),
            "range": (min((m["period"] for m in gm), default=""),
                      max((m["period"] for m in gm), default="")),
            "conquests": [g for g in buckets if g["cat"] == "conquest"],
            "nightfalls": [g for g in buckets if g["cat"] == "nightfall"],
            "records": {
                "conquest": _obj_progress(pr.get(_GM_REC_CONQUEST, {})),
                "ultimate": _obj_progress(pr.get(_GM_REC_ULTIMATE, {})),
                "gilds": gild},
            "added": len(gm), "cached": 0, "missed": 0, "capped": False,
        }
        JOBS[jid].update(status="done", total=3, done=3, result=result)
        log_progress(jid, 3, 3, label=_job_label(jid), force=True,
                     extra=f"第 3/3 步：汇总出图（扫 {len(matches)} 场，命中 {len(gm)} 场）")
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


@_traced(lambda name, kind: (f"武器锻造图案 {name}" if kind == "patterns"
                            else f"称号进度 {name}"))
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
    save_seen_players()
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
    oldest = ""
    try:
        log_progress(jid, 0, 1, label=_job_label(jid), force=True,
                     extra="从最近往 2019-06 翻（翻到哪算哪，预估按时间跨度折算）")
        for ci, cid in enumerate(chars):
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
                    if not oldest or d < oldest:
                        oldest = d
                    if d < "2019-06":  # 赛季纪元前，不再翻页
                        break
                else:
                    page += 1
                    JOBS[jid]["total"] = page + 1
                    JOBS[jid]["done"] = page
                    # 翻页本身说不清总量，这里按「已扫到多早」折算进度条：
                    # 从今天倒着扫到 2019-06 算 100%，预估时间才有意义
                    log_progress(f"{jid}#scan", int(_scan_pct(oldest)), 100,
                                 label=f"{_job_label(jid)} · 翻页扫描",
                                 extra=f"角色 {ci + 1}/{len(chars)} · 第 {page} 页 · "
                                       f"已计 {counted} 场 · 已扫到 {oldest or '—'}")
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
@_traced(lambda name, mode=5, count=100: f"战绩查询 {name}")
async def mode_report(name: str, mode: int, count: int = 100) -> dict:
    """基于对局历史聚合某模式战绩（跨角色合并 + 细分模式 + 胜率）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})

    seen, matches = set(), []
    mdisp = f"{member['display']}#{fmt_code(member['code'])}"
    nch = len(chars) or 1
    log_progress(f"mode:{mid}:{mode}", 0, nch, label=f"战绩 {mdisp}", force=True,
                 extra=f"拉取每个角色最近 {count} 场对局历史")
    for ci, cid in enumerate(chars, 1):
        for m in await activity_history(mtype, mid, cid, mode, count=count):
            key = m["instance"] or f"{m['ref']}{m['period']}"
            if key in seen:
                continue
            seen.add(key)
            matches.append(m)
        log_progress(f"mode:{mid}:{mode}", ci, nch, label=f"战绩 {mdisp}",
                     extra=f"角色 {ci}/{nch} 完成 · 已收 {len(matches)} 场")
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
        # membership() 失败时会吞掉真实原因（token 刷新失败 / 官方 5xx），
        # 这里直接调一次把真实异常透传出去，别让用户误以为要去重新授权
        await bungie_auth.authorized_get("/Platform/User/GetMembershipsForCurrentUser/")
        raise BungieAuthRequired("Bungie 授权信息无效，请在面板重新授权")
    mt, mid = mem["membership_type"], mem["membership_id"]
    prof = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/", {"components": "200"})
    chars = ((prof.get("characters") or {}).get("data")) or {}
    if not chars:
        raise RuntimeError("该 Bungie 账号没有命运2角色")
    out = []
    nch = len(chars)
    for ci, cid in enumerate(chars, 1):
        out.append(await bungie_auth.authorized_get(
            f"/Platform/Destiny2/{mt}/Profile/{mid}/Character/{cid}/Vendors/",
            {"components": "400,401,402"}))
        log_progress("eververse", ci, nch, label="每日光尘商店",
                     force=(ci == 1), extra=f"角色 {ci}/{nch} 商店数据")
    return out


@_traced("每日光尘商店")
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


# ---------- 老九（仄 / Xûr）每周商品 ----------
# 数据源：官方 GetVendors（OAuth，与光尘同管线）。官方中文名就叫「仄」（vendor 展示
# 物品 3329627384）。他每周六凌晨 1 点到高塔、周三凌晨 1 点随维护离场（周五/周二
# 17:00 UTC）。实测：未到场时全量接口也带他的 saleItems（异域护甲等已预上架、hash
# 是真的）；单店接口到场才开，且只有它给 perks/stats（随机卷）组件。
XUR_VENDOR_HASH = "2190858386"
_XUR_CACHE: dict = {"at": 0.0, "data": None}
_VI_INDEX = None

_XUR_CLASS_ZH = {-1: "通用", 0: "泰坦", 1: "猎人", 2: "术士", 3: "通用"}

# 武器插槽类别（DestinySocketCategoryDefinition，稳hash）：武器特性组 / 固有特性。
# 模组(2685412949)与外观(2048875504：皮肤/击杀记录器)不展示。
_SOCKET_WEAPON_PERKS = 4241085061
_SOCKET_INTRINSIC = 3956125808
_SOCK_TYPES: dict | None = None
_DEF_CACHE: dict = {}


def _sock_types() -> dict:
    """socketTypeHash → socketCategoryHash（raw_sockettypes.json 懒加载）。"""
    global _SOCK_TYPES
    if _SOCK_TYPES is None:
        try:
            raw = json.load(open(_idx_file("raw_sockettypes.json"), encoding="utf-8"))
            _SOCK_TYPES = {h: (v or {}).get("socketCategoryHash") for h, v in raw.items()}
        except Exception:  # noqa: BLE001
            _SOCK_TYPES = {}
    return _SOCK_TYPES


async def _item_def(hash_int: int) -> dict:
    """单件物品定义（公开实体接口，client 自带 6 小时 /Manifest/ 缓存）。"""
    h = str(hash_int)
    if h not in _DEF_CACHE:
        try:
            r = await client().get(
                f"/Platform/Destiny2/Manifest/DestinyInventoryItemDefinition/{h}/")
            resp = r.json()
            _DEF_CACHE[h] = (resp.get("Response") or {}) \
                if resp.get("ErrorCode") == 1 else {}
        except Exception:  # noqa: BLE001
            _DEF_CACHE[h] = {}
    return _DEF_CACHE[h]


async def _weapon_socket_plugs(item_hash: int, live: list) -> tuple[dict | None, list, bool]:
    """武器插槽按类别抽取：→ (固有特性, 特性组插值, 是否带随机卷)。

    定义 socketEntries 与组件 sockets 按下标对齐；插值优先用实盘 plugHash
    （隼月的随机卷真值），没有再用定义默认值。随机槽 = 定义里带
    randomizedPlugSetHash 的特性槽。"""
    d = await _item_def(item_hash)
    ents = ((d.get("sockets") or {}).get("socketEntries")) or []
    st = _sock_types()
    intr, plugs, rolled = None, [], False
    for i, e in enumerate(ents):
        cat = st.get(str(e.get("socketTypeHash") or ""))
        if cat not in (_SOCKET_WEAPON_PERKS, _SOCKET_INTRINSIC):
            continue
        live_h = live[i].get("plugHash") if i < len(live) else 0
        val = live_h or e.get("singleInitialItemHash") or 0
        if not val:
            continue
        if cat == _SOCKET_INTRINSIC:
            if intr is None or live_h:
                intr = {"h": int(val)}
        else:
            rnd = bool(e.get("randomizedPlugSetHash"))
            plugs.append({"h": int(val), "rnd": rnd})
            rolled = rolled or rnd
    # 插件 hash 是物品空间，perks.json（perk 定义空间）多半没有 → 拉插件定义补名
    for d in ([intr] if intr else []) + plugs:
        p = _perks.get(str(d["h"]))
        if p:
            d["name"], d["icon"] = p.get("name"), p.get("icon")
    for d in ([intr] if intr else []) + plugs:
        if d.get("name") or d.get("icon"):
            continue
        od = await _item_def(d["h"])
        dp = od.get("displayProperties") or {}
        d["name"] = dp.get("name") or ""
        d["icon"] = (BASE + dp["icon"]) if dp.get("icon") else ""
    # 击杀记录器这类槽在老武器的特性组里，不是 perk
    plugs = [p for p in plugs
             if not any(k in (p.get("name") or "") for k in ("记录器", "计数器"))]
    return intr, plugs, rolled


def _vi_index() -> dict:
    """vendor_items.json 懒加载（build_vendor_items.py 产出，约 4MB）。"""
    global _VI_INDEX
    if _VI_INDEX is None:
        try:
            _VI_INDEX = json.load(open(_idx_file("vendor_items.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _VI_INDEX = {}
    return _VI_INDEX


def _xur_present(ts: float) -> bool:
    """在场窗口：周六 01:00 → 周三 01:00（维护重置离场），本机时间=北京时间。"""
    t = datetime.datetime.fromtimestamp(ts)
    wd, hm = t.weekday(), (t.hour, t.minute)
    if wd == 5:                       # 周六：1 点后算到场
        return hm >= (1, 0)
    if wd in (6, 0, 1):               # 周日/周一/周二：全天在
        return True
    if wd == 2:                       # 周三：1 点离场
        return hm < (1, 0)
    return False                      # 周四/周五：不在


def _xur_next_arrival(ts: float) -> float:
    """下次抵达 = 下一个周六 01:00（含今天周六但还没到 1 点的情况）。"""
    t = datetime.datetime.fromtimestamp(ts)
    d = t.replace(hour=1, minute=0, second=0, microsecond=0)
    d += datetime.timedelta(days=(5 - d.weekday()) % 7)   # 周六 weekday()==5
    if d.timestamp() <= ts:
        d += datetime.timedelta(days=7)
    return d.timestamp()


def _xur_item(sale: dict, perks_by_idx: dict, sockets_by_idx: dict) -> dict | None:
    idx = _vi_index()
    h = str(sale.get("itemHash") or "")
    rec = idx.get(h)
    if not rec:
        return None
    name, ty, tier, icon, cls, shot = (list(rec) + [""] * 6)[:6]
    cost = None
    for c in (sale.get("costs") or []):
        crec = idx.get(str(c.get("itemHash") or "")) or []
        cost = {"n": int(c.get("quantity") or 0),
                "cur": crec[0] if crec else "？",
                "icon": (BASE + crec[3]) if len(crec) > 3 and crec[3] else ""}
        break
    if cost is None and tier != "异域":
        return None                   # 没标价又非异域 = 分节门/占位（如「奇异装备优惠」）
    perks = []
    p = perks_by_idx.get(str(sale.get("vendorItemIndex"))) if perks_by_idx else None
    for pk in ((p or {}).get("perks") or []):
        if pk.get("visible") and pk.get("perkHash"):
            perks.append({"h": pk["perkHash"],
                          "icon": (BASE + pk["iconPath"]) if pk.get("iconPath") else ""})
    # 武器插槽原始值先挂在 _live，xur_stock 里对武器再按类别抽取（需拉物品定义）
    so = (sockets_by_idx or {}).get(str(sale.get("vendorItemIndex"))) or {}
    return {"hash": h, "idx": sale.get("vendorItemIndex") or 0, "n": name, "ty": ty,
            "tier": tier, "cls": _XUR_CLASS_ZH.get(cls if isinstance(cls, int) else -1, "通用"),
            "icon": (BASE + icon) if icon else "", "shot": (BASE + shot) if shot else "",
            "cost": cost, "status": sale.get("saleStatus"), "perks": perks,
            "_live": [s for s in (so.get("sockets") or []) if isinstance(s, dict)],
            "sec": _xur_sec(tier, ty, name)}


def perk_meta(h) -> tuple:
    """perkHash → (中文名, 绝对图标 URL)（perks.json 里没有就给空串）。"""
    p = _perks.get(str(h)) or {}
    return p.get("name") or "", p.get("icon") or ""


_XUR_ARMOR_KW = ("护甲", "头盔", "臂甲", "臂铠", "披风", "猎戏", "印记", "臂环")


def _xur_sec(tier: str, ty: str, name: str) -> str:
    """卡片分节：异域护甲(按职业) → 职业金(一横列) → 异域武器 → 异域武器催化 →
    传说武器 → 传说护甲 → 材料 → 任务 → 其他。催化任务（竞技催化）不算武器催化。"""
    if "任务" not in ty and (name.endswith("催化") or "催化" in ty):
        return "异域武器催化"
    if "记忆水晶" in ty:
        return "材料"
    if tier == "异域" and any(k in ty for k in ("披风", "印记", "臂环", "猎戏")):
        return "职业金"
    armor = any(k in ty for k in _XUR_ARMOR_KW)
    if tier == "异域":
        if armor:
            return "异域护甲"
        return "任务" if "任务" in ty else "异域武器"
    if tier == "传说" and not armor:
        if "材料" in ty or "可兑换" in ty:
            return "材料"
        return "任务" if "任务" in ty else "传说武器"
    if armor:
        return "传说护甲"
    if "材料" in ty or "可兑换" in ty:
        return "材料"
    if "任务" in ty:
        return "任务"
    return "其他"


# ---------- 老九周货主源：Kyber's Corner 内嵌 vendor 数据 ----------
# 武器/催化/传说栏在未解锁账号的 Bungie 接口里被「更多奇异优惠/奇异装备优惠」
# 门槛挡住（只回护甲/材料/任务），而 kyberscorner.com/destiny2/xur/ 每周把完整
# 货单内嵌在页面 JSON 里（window.KYBER_XUR_DATA：异域护甲/隼月整卷 sockets/
# 金枪/催化/传说武器/传说护甲/材料价目，hash 与本地索引对得上）。到场窗口内
# 用它出卡（免授权）；抓不到或已过离场时间再走 Bungie 接口兜底。
_KYBER_XUR_URL = "https://kyberscorner.com/destiny2/xur/"
_KYBER_CACHE_FILE = "xur_kyber_cache.json"
_KYBER_CACHE: dict = {"at": 0.0, "data": None}
_PLUG_META: dict | None = None


def _plug_meta_idx() -> dict:
    """plug_meta.json 懒加载（build_plug_meta.py 产出：插件 hash → 中文名/图标）。"""
    global _PLUG_META
    if _PLUG_META is None:
        try:
            _PLUG_META = json.load(open(_idx_file("plug_meta.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _PLUG_META = {}
    return _PLUG_META


def _iso_ts(s: str) -> float:
    """UTC ISO 时间 → epoch（解析不了给 0）。"""
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


async def _kyber_xur() -> dict | None:
    """抓 Kyber's Corner 老九周货 JSON：内存缓存 2h + 落盘兜底，抓不到回上次数据。"""
    now = time.time()
    c = _KYBER_CACHE
    if c["data"] and now - c["at"] < 7200:
        return c["data"]
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    data = None
    try:
        html = await _web_get_text(_KYBER_XUR_URL)
        m = re.search(r"window\.KYBER_XUR_DATA\s*=\s*", html)
        if m:
            raw, _ = json.JSONDecoder().raw_decode(html[m.end():])
            if isinstance(raw, dict) and raw.get("status") == "ok" and raw.get("arrival"):
                data = raw
    except Exception:  # noqa: BLE001
        data = None
    if data is None:
        try:
            data = json.load(open(os.path.join(base, _KYBER_CACHE_FILE), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    if data and (not c["data"] or data.get("generatedAt") != c["data"].get("generatedAt")):
        try:
            json.dump(data, open(os.path.join(base, _KYBER_CACHE_FILE), "w",
                                 encoding="utf-8"), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
    c.update(at=now, data=data)
    return data


def _xur_kyber_plugs(src: dict, cats: tuple) -> list[dict]:
    """Kyber sockets → perk 芯片（plug_meta 解中文名，缺了退它自带的英文名）。"""
    out = []
    for s in (src.get("sockets") or []):
        cat = (s.get("plugCategoryIdentifier") or "").rsplit(".", 1)[-1]
        ph = s.get("plugHash")
        if cat not in cats or not ph:
            continue
        pm = _plug_meta_idx().get(str(ph)) or {}
        name = pm.get("name") or s.get("name") or ""
        if any(k in name for k in ("记录器", "计数器")):
            continue
        out.append({"h": int(ph), "name": name,
                    "icon": pm.get("icon") or s.get("icon") or ""})
    return out


def _xur_kyber_items(ky: dict) -> list[dict]:
    """Kyber 周货 → 老九卡物品：中文名/类型/职业全走本地 vendor_items 索引，
    索引查不到的（如奇异礼物）跳过；带 grips 槽或可重用池的异域武器判随机卷大卡。"""
    idx = _vi_index()
    items = []
    sec_items = ([(it, "") for it in (ky.get("exoticArmor") or [])]
                 + [(it, "") for it in (ky.get("hawkmoon") or [])]
                 + [(it, "") for it in (ky.get("exoticWeapons") or [])]
                 + [(it, "") for it in (ky.get("catalysts") or [])]
                 + [(it, "") for it in (ky.get("legendaryWeapons") or [])]
                 + [(it, "") for it in (ky.get("legendaryArmor") or [])]
                 + [(it, "材料") for it in (ky.get("strangeOffers") or [])])
    for i, (src, force_sec) in enumerate(sec_items):
        h = str(src.get("hash") or "")
        rec = idx.get(h)
        if not rec or not rec[0]:
            continue
        name, ty, tier, icon, cls, shot = (list(rec) + [""] * 6)[:6]
        cost = None
        for cc in (src.get("costs") or []):
            crec = idx.get(str(cc.get("hash") or ""))
            cost = {"n": int(cc.get("quantity") or 0),
                    "cur": crec[0] if crec else "？",
                    "icon": (BASE + crec[3]) if crec and len(crec) > 3 and crec[3] else ""}
            break
        it = {"hash": h, "idx": i, "n": name, "ty": ty, "tier": tier,
              "cls": _XUR_CLASS_ZH.get(cls if isinstance(cls, int) else -1, "通用"),
              "icon": (BASE + icon) if icon else (src.get("icon") or ""),
              "shot": (BASE + shot) if shot else (src.get("screenshot") or ""),
              "cost": cost, "status": 0, "perks": [],
              "sec": force_sec or _xur_sec(tier, ty, name)}
        if it["sec"] in ("异域武器", "传说武器"):
            it["intr"] = (_xur_kyber_plugs(src, ("intrinsics",)) or [None])[0]
            # 枪管/弹夹也在 sockets 里：大卡要整卷；小卡 _xur_chips 取 plugs[-2:]
            # 仍是两特性（槽序 枪管→弹夹→特性×2）
            it["plugs"] = _xur_kyber_plugs(src, ("barrels", "magazines", "frames", "grips"))
            it["rolled"] = any(
                (s.get("plugCategoryIdentifier") or "").rsplit(".", 1)[-1] in ("frames", "grips")
                and ((s.get("plugCategoryIdentifier") or "").endswith("grips")
                     or len(s.get("reusablePlugs") or []) > 1)
                for s in (src.get("sockets") or []))
        items.append(it)
    return items


@_traced("老九商品")
async def xur_stock(force: bool = False) -> dict:
    """仄（老九 / Xûr）的每周商品。

    主源：Kyber's Corner 每周内嵌的完整货单（免授权，武器/催化/传说栏齐全），
    在场窗口内直接用它出卡。兜底走 Bungie 接口：逐角色查再合并（职业臂/
    职业传说甲只发给对应职业的角色，133/134/135 三格各归各职业），未解锁账号
    会缺武器/催化/传说栏（卡上有提示行）。未到场时全量接口也带预上架商品，
    照常出卡并在卡头标注；单店接口（随机卷 perks）到场才开（未到场 404
    DestinyVendorNotFound），失败自动退回全量兜底。未授权抛
    BungieAuthRequired（上层提示去面板授权）。
    """
    now = time.time()
    if not force:
        c = _XUR_CACHE
        if c["data"] and now - c["at"] < (7200 if c["data"].get("present") else 600):
            return c["data"]
    ky = await _kyber_xur()
    if ky and _iso_ts(ky.get("arrival")) <= now < _iso_ts(ky.get("departure")):
        items = [it for it in _xur_kyber_items(ky) if it["sec"] != "任务"]
        data = {"present": True, "source": "kyber", "items": items, "updated": now,
                "arrives": 0.0, "arrives_txt": "", "in_min": 0}
        _XUR_CACHE.update(at=now, data=data)
        return data

    import bungie_auth
    if not bungie_auth.authorized():
        raise BungieAuthRequired("还没有授权 Bungie 账号")
    mem = await bungie_auth.membership()
    if not mem or not mem.get("membership_id"):
        # membership() 失败会吞掉真实原因，直接调一次把异常透传出去
        await bungie_auth.authorized_get("/Platform/User/GetMembershipsForCurrentUser/")
        raise BungieAuthRequired("Bungie 授权信息无效，请在面板重新授权")
    mt, mid = mem["membership_type"], mem["membership_id"]
    prof = await bungie_auth.authorized_get(
        f"/Platform/Destiny2/{mt}/Profile/{mid}/", {"components": "200"})
    chars = ((prof.get("characters") or {}).get("data")) or {}
    if not chars:
        raise RuntimeError("该 Bungie 账号没有命运2角色")

    present = _xur_present(now)
    raw = []          # 每角色一份 (sales_map, perks_by_idx, sockets_by_idx)
    if present:
        # 单店接口只有到场才开（未到场 404 DestinyVendorNotFound），一次拿全
        # sales + itemComponents（perks 随机卷 / sockets 插槽实际卷值）。逐角色
        # 查：职业臂/职业传说甲只发给对应职业的角色（133/134/135 三格各归各职业）。
        for cid in chars:
            try:
                one = await bungie_auth.authorized_get(
                    f"/Platform/Destiny2/{mt}/Profile/{mid}/Character/{cid}/"
                    f"Vendors/{XUR_VENDOR_HASH}/",
                    {"components": "400,401,402,300,302,304,305"})
                ic = one.get("itemComponents") or {}
                raw.append((((one.get("sales") or {}).get("data")) or {},
                            ((ic.get("perks") or {}).get("data")) or {},
                            ((ic.get("sockets") or {}).get("data")) or {}))
            except Exception:  # noqa: BLE001
                raw.append(({}, {}, {}))
        if not any(s for s, _, _ in raw):
            raw = []      # 单店全挂（Bungie 刷新迟到等）→ 退回全量兜底
    if not raw:
        for cid in chars:
            resp = await bungie_auth.authorized_get(
                f"/Platform/Destiny2/{mt}/Profile/{mid}/Character/{cid}/Vendors/",
                {"components": "400,401,402"})
            group = (((resp.get("sales") or {}).get("data") or {}).get(XUR_VENDOR_HASH) or {})
            raw.append((((group.get("saleItems") or {})
                         if isinstance(group, dict) else {}), {}, {}))
    items, seen = [], set()
    for sales_map, perks_by_idx, sockets_by_idx in raw:
        for s in sales_map.values():
            if not isinstance(s, dict):
                continue
            it = _xur_item(s, perks_by_idx, sockets_by_idx)
            # 三角色合并：同 hash 去重；职业限定货 hash 不同各留一份；
            # 同名双 hash 变体（异色/高光版）只留第一份（键带分节，防跨类撞名互杀）
            if it and it["hash"] not in seen and (it["sec"], it["n"]) not in seen:
                seen.add(it["hash"])
                seen.add((it["sec"], it["n"]))
                items.append(it)
    if not present:
        items = [it for it in items if it.get("status") == 0]
    items = [it for it in items if it.get("sec") != "任务"]   # 异星学等周常任务不出卡
    order = {s: i for i, s in enumerate(("异域护甲", "职业金", "异域武器",
                                         "异域武器催化", "传说武器", "传说护甲",
                                         "材料", "任务", "其他"))}
    items.sort(key=lambda it: (order.get(it["sec"], 9), it["idx"]))
    # 武器：按类别抽插槽（固有特性 / 特性组真值 / 是否带随机卷），要拉物品定义
    for it in items:
        if it.get("sec") in ("异域武器", "传说武器"):
            try:
                intr, plugs, rolled = await _weapon_socket_plugs(
                    int(it["hash"]), it.get("_live") or [])
            except Exception:  # noqa: BLE001
                intr, plugs, rolled = None, [], False
            it["intr"], it["plugs"], it["rolled"] = intr, plugs, rolled
        it.pop("_live", None)
    arr = 0.0 if present else _xur_next_arrival(now)
    at = datetime.datetime.fromtimestamp(arr) if arr else None
    data = {"present": present, "source": "api", "items": items, "updated": now,
            "arrives": arr,
            "arrives_txt": (f"{['周一', '周二', '周三', '周四', '周五', '周六', '周日'][at.weekday()]}"
                            f" {at:%H:%M}") if at else "",
            "in_min": max(0, int((arr - now) // 60)) if arr else 0}
    _XUR_CACHE.update(at=now, data=data)
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


@_traced("本周轮换")
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


# ---------- 扭曲星球轮换（每小时换目的地，7 小时一轮） ----------
# 数据来源同 rotation_pairs.json 里的 distortion 段（scrape_starside_rotation.py 抓）。
# 口径与 starside 轮换页一致：页面按访问者本机时钟高亮，表以「本机周一 00:00」为
# 起点，目的地 = cycle[slots[周几(周一=0)][小时]]；周期 7 小时、一周 168 时段，
# 因此每周的表相同。bot 跑在用户机器上（UTC+8），直接用本机时间即可。
# 对齐自检：Bungie 周复位 = UTC 周二 17:00 = 北京周三 01:00，距周一 00:00 恰
# 49h = 7 整循环，复位点自动回到 cycle[0]，无需单独对齐。
_DIST = None


def _distortion() -> dict:
    global _DIST
    if _DIST is None:
        try:
            _DIST = (json.load(open(_idx_file("rotation_pairs.json"), encoding="utf-8"))
                     .get("distortion") or {})
        except Exception:  # noqa: BLE001
            _DIST = {}
    return _DIST


def distortion_now(dt_: datetime.datetime | None = None) -> dict:
    """扭曲星球当前时段。dt_ 缺省取本机当前时间（bot 所在机器的本地时区）。

    返回 {"ok": True, dest 当前目的地, range 时段文字, start/end 时段起止,
          next_dest 下一个目的地, next_at 切换时刻, next_hm 切换时刻(HH:MM),
          next_in_sec 距切换秒数, today 今日剩余各时段(含当前, current 标记)}。
    数据缺失时返回 {"ok": False}，调用方按「无此板块」处理。
    """
    d = _distortion()
    cyc, slots = d.get("cycle") or [], d.get("slots") or []
    now = dt_ or datetime.datetime.now()
    if not cyc or not slots:
        return {"ok": False}
    try:
        idx = slots[now.weekday()][now.hour]
    except Exception:  # noqa: BLE001
        return {"ok": False}
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=1)
    nxt = (idx + 1) % len(cyc)
    today = [{"hour": h, "range": f"{h:02d}:00-{(h + 1) % 24:02d}:00",
              "dest": cyc[slots[now.weekday()][h]], "current": h == now.hour}
             for h in range(now.hour, 24)]
    return {"ok": True, "dest": cyc[idx],
            "range": f"{now.hour:02d}:00-{end:%H}:00", "start": start, "end": end,
            "next_dest": cyc[nxt], "next_at": end, "next_hm": f"{end:%H:%M}",
            "next_in_sec": max(int((end - now).total_seconds()), 0),
            "today": today}


def _rot_cache_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, ROT_CACHE_FILE)


# ---------- 今日遗失区域 / 当前宗师（第三方轮换页 + 本地中英映射） ----------
# 官方里程碑接口里没有这两样（GetPublicMilestones 实测只有突袭/公会/赛季活动）：
#   · 遗失区域：2025-07 起改为每个目的地各自每日轮换。d2lostsector.report 首页是
#     服务端直渲染的当日 9 区全量数据——卡片背景图 URL 里就带活动 hash，
#     勇士/护盾在图标 alt 文本里；中文名用 manifest_index/rotation_zh.json
#     （build_rotation_zh.py 生成：hash→zh / 目的地→zh / 奖励套装→zh）。
#   · 宗师：lfcarry 周轮换页（固定 URL）声明本周 Grandmaster，英文副本名过同一份映射。
# 各自按 天/周 缓存落盘（刷新点同商店：北京时间凌晨 1 点）；抓取失败回退当日缓存。
LS_CACHE_FILE = "lost_sector_cache.json"
GM_CACHE_FILE = "gm_cache.json"
_LS_CACHE_VER = 2     # v2：奖励套装名剥部位后缀（旧缓存里是单件名）
_ROT_ZH: dict | None = None
_WEB_CLIENTS: dict = {}

# d2lostsector.report 的目的地短名 → rotation_zh.json dest 表的键（manifest 用全称）
_DEST_ALIAS = {"edz": "european dead zone", "moon": "the moon",
               "dreaming city": "the dreaming city", "pale heart": "the pale heart",
               "throne world": "savathûn's throne world",
               "tangled shore": "the tangled shore"}
# dest 表里没有的短名直接给中文名
_DEST_ZH_FIX = {"nessus": "涅索斯"}
_CHAMP_ZH = {"barrier": "壁垒", "overload": "过载", "unstoppable": "不可阻挡"}
_ELEM_ZH = {"solar": "烈日", "arc": "电弧", "void": "虚空", "stasis": "冰影",
            "strand": "缠绕"}


def _rot_zh() -> dict:
    global _ROT_ZH
    if _ROT_ZH is None:
        try:
            _ROT_ZH = json.load(open(_idx_file("rotation_zh.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _ROT_ZH = {}
    return _ROT_ZH


def _gm_week_key(ts: float | None = None) -> str:
    """每周缓存键：周三凌晨 1 点（=周二 17:00 UTC）刷新，1 点前算上一周。"""
    t = datetime.datetime.fromtimestamp(ts if ts is not None else time.time())
    t -= datetime.timedelta(hours=1)
    mon = (t.weekday() - 2) % 7          # 周三=2，往回退到本周三
    return (t - datetime.timedelta(days=mon)).strftime("%G-W%V")


async def _web_get_text(url: str) -> str:
    """第三方页面抓取（独立客户端，不把 Bungie API Key 带出去）"""
    loop = asyncio.get_running_loop()
    c = _WEB_CLIENTS.get(loop)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) D2Query/1.0"},
            timeout=20, follow_redirects=True)
        _WEB_CLIENTS[loop] = c
    r = await c.get(url)
    r.raise_for_status()
    return r.text


def _json_cache(base: str, name: str, ver: int, key: str):
    try:
        d = json.load(open(os.path.join(base, name), encoding="utf-8"))
        if d.get("ver") == ver and d.get("key") == key:
            return d.get("data")
    except Exception:  # noqa: BLE001
        pass
    return None


def _json_cache_save(base: str, name: str, ver: int, key: str, data):
    try:
        json.dump({"ver": ver, "key": key, "at": time.time(), "data": data},
                  open(os.path.join(base, name), "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _parse_ls_html(html: str) -> list[dict]:
    """d2lostsector.report 首页 → 当日遗失区域列表（中文名就地映射，缺映射回退英文）

    卡片结构：<div style="background-image:url(…/for-website/<hash>/<hash>.jpg…)">
    <div class="card-header…"><a href="/sector/<slug>"><h2>英名</h2><p>目的地</p></a>
    …奖励套装图标… 勇士/护盾/强化图标（信息都在 alt 文本里）——hash 在卡片开头。"""
    import html as _html
    rz = _rot_zh()
    dest_map = rz.get("dest") or {}
    out = []
    pat = re.compile(
        r'for-website/(\d{6,12})/\1\.[a-z]+[^>]*>\s*<div class="card-header[^"]*">'
        r'<a[^>]*href="/sector/([a-z0-9_]+)"(.*?)(?=for-website/\d{6,12}/|\Z)', re.S)
    for m in pat.finditer(html):
        h, slug, seg = m.group(1), m.group(2), m.group(3)
        mm = re.search(r"<h2[^>]*>(.*?)</h2>", seg)
        en = _html.unescape(mm.group(1)).strip() if mm else ""
        mm = re.search(r"<p[^>]*>(.*?)</p>", seg)
        dest_en = _html.unescape(mm.group(1)).strip() if mm else ""
        if not en:
            continue
        set_en, set_icon = "", ""
        sm = re.search(r'alt="([^"]+?) set"', seg)
        if sm:
            set_en = _html.unescape(sm.group(1))
            im = re.search(r'icons/([a-f0-9]{32}\.(?:jpg|png))', seg)
            if im:
                set_icon = BASE + "/common/destiny2_content/icons/" + im.group(1)
        rec = (rz.get("ls") or {}).get(h) or {}
        zh = rec.get("zh") or re.sub(r"[:：]\s*(专家|大师)\s*$", "",
                                     (_activities.get(h) or {}).get("name") or "") or en
        set_zh = (rz.get("sets") or {}).get(set_en.lower(), "")
        if set_zh:  # 「第七炽天使斗篷」→「第七炽天使 套装」（图标反查到的是单件名）
            stripped = re.sub(r"(之胄|风帽|面具|斗篷|胄盔|胸甲|臂铠|腿甲)$", "", set_zh)
            if stripped and stripped != set_zh:
                set_zh = stripped + " 套装"
        dl = dest_en.lower()
        dest_zh = (_DEST_ZH_FIX.get(dl)
                   or dest_map.get(_DEST_ALIAS.get(dl, dl)) or dest_en)
        out.append({
            "hash": h, "slug": slug, "en": en, "zh": zh,
            "dest_en": dest_en, "dest_zh": dest_zh,
            "set_en": set_en, "set_zh": set_zh,
            "set_icon": set_icon,
            "champs": [_CHAMP_ZH.get(x, x) for x in re.findall(r'alt="Champion type: (\w+)"', seg)],
            "shields": [_ELEM_ZH.get(x, x) for x in re.findall(r'alt="Shield type: (\w+)"', seg)],
        })
    return out


@_traced("今日遗失区域")
async def lost_sectors_today(force: bool = False) -> dict:
    """当日各目的地遗失区域（每日缓存，北京时间凌晨 1 点换天）。"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    day = _ev_day()
    if not force:
        disk = _json_cache(base, LS_CACHE_FILE, _LS_CACHE_VER, day)
        if disk:
            return disk
    html = await _web_get_text("https://d2lostsector.report/")
    sectors = _parse_ls_html(html)
    if not sectors:
        raise RuntimeError("页面里解析不到遗失区域卡片")
    data = {"ok": True, "day": day, "sectors": sectors}
    _json_cache_save(base, LS_CACHE_FILE, _LS_CACHE_VER, day, data)
    return data


@_traced("当前宗师")
async def gm_this_week(force: bool = False) -> dict:
    """本周宗师夜袭（每周缓存）。lfcarry 固定页声明本周 GM，映射成中文+横图。"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    wk = _gm_week_key()
    if not force:
        disk = _json_cache(base, GM_CACHE_FILE, 1, wk)
        if disk:
            return disk
    html = await _web_get_text("https://lfcarry.com/guides/destiny-2-weekly-rotation")
    import html as _html
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text)
    gm_map = _rot_zh().get("gm") or {}
    en, dest_en, fallback = "", "", ""
    for m in re.finditer(r"Grandmaster\s*[:\-]?\s*([A-Z][^,.\n{]{2,48})", text):
        raw = m.group(1).strip()
        cand, _, rest = raw.partition("(")
        cand = cand.strip()
        if cand.lower() in gm_map:            # 只认映射里认识的活动名，防抓到导航标题
            en = cand
            dest_en = rest.replace(")", "").strip()
            break
        if not fallback or len(cand) < len(fallback):
            fallback = cand                   # 映射全没中时兜底取最短候选（多半是副本名）
    if not en:
        en = fallback
    if not en:
        raise RuntimeError("页面里解析不到本周宗师")
    if not dest_en:                           # 「It is the Nessus strike」句式补目的地
        dm = re.search(r"the ([A-Z][a-zA-Z' ]{2,24}?) strike", text)
        if dm:
            dest_en = dm.group(1).strip()
    rec = gm_map.get(en.lower()) or {}
    dl = dest_en.lower()
    dest_zh = (_DEST_ZH_FIX.get(dl) or (_rot_zh().get("dest") or {}).get(
        _DEST_ALIAS.get(dl, dl)) or dest_en)
    data = {"ok": True, "week": wk, "en": en, "zh": rec.get("zh") or en,
            "hash": str(rec.get("hash") or ""), "pgcr": rec.get("pgcr") or "",
            "dest_en": dest_en, "dest_zh": dest_zh}
    _json_cache_save(base, GM_CACHE_FILE, 1, wk, data)
    return data


# ---------- 进度点（d2checkpoint.com 尾王存档点，/进度点） ----------
# 数据源：D2Checkpoint 的 Astro 动作接口（免授权，免登录）：
#   POST https://d2checkpoint.com/_actions/bots.getBotsFromDb
# 响应是 devalue 序列化：一个数组当引用图用——A[0] 是 bot 下标列表，对象存成
# 「{键: 绝对下标}」的模板，值都在数组槽位里（同名键只存一份，去重）。
# 每个 bot：activityHash（Bungie 活动 hash，0=不在线）+ encounter（该活动关卡下标）。
# 「可进/满员/离场」走官方 GetProfile components=1000（profileTransitoryData）：
#   currentActivity.numberOfPlayers < fireteamSize → 有位；≥ → 满员；
#   transitory 缺失/0 人 → bot 已离场，点位多半没了。
# 下面的 hash→活动、活动→中文名表抄自 d2checkpoint 前端 bundle（2026-10），
# 新副本上线若没认出 hash 会回退显示英文名，不影响出卡。

_CP_HASH_EN: dict[int, tuple[str, int]] = {  # hash → (活动英文名, 火队人数)
    2122313384: ("Last Wish", 6), 2032534090: ("Shattered Throne", 3),
    1042180643: ("Garden of Salvation", 6), 2582501063: ("Pit of Heresy", 3),
    1077850348: ("Prophecy", 3), 910380154: ("Deep Stone Crypt", 6),
    3881495763: ("Vault of Glass", 6), 3022541210: ("Vault of Glass", 6),
    4078656646: ("Grasp of Avarice", 3), 1112917203: ("Grasp of Avarice", 3),
    1441982566: ("Vow of the Disciple", 6), 3889634515: ("Vow of the Disciple", 6),
    2823159265: ("Duality", 3), 3012587626: ("Duality", 3),
    1374392663: ("King's Fall", 6), 3257594522: ("King's Fall", 6),
    1262462921: ("Spire of the Watcher", 3), 2296818662: ("Spire of the Watcher", 3),
    2381413764: ("Root of Nightmares", 6), 2918919505: ("Root of Nightmares", 6),
    313828469: ("Ghosts of the Deep", 3), 2716998124: ("Ghosts of the Deep", 3),
    107319834: ("Crota's End", 6), 1507509200: ("Crota's End", 6),
    2004855007: ("Warlord's Ruin", 3), 2534833093: ("Warlord's Ruin", 3),
    1541433876: ("Salvation's Edge", 6), 4129614942: ("Salvation's Edge", 6),
    300092127: ("Vesper's Host", 3), 4293676253: ("Vesper's Host", 3),
    3834447244: ("Sundered Doctrine", 3), 3521648250: ("Sundered Doctrine", 3),
    4046934917: ("Spire of the Watcher", 3), 3339002067: ("Spire of the Watcher", 3),
    2961030534: ("Ghosts of the Deep", 3), 124340010: ("Ghosts of the Deep", 3),
    715153594: ("Prophecy", 3), 3193125350: ("Prophecy", 3),
    1044919065: ("The Desert Perpetual", 6), 2727361621: ("Equilibrium", 3),
    1516551982: ("Pantheon: Calus Resplendent", 6),
    2530656885: ("Pantheon: Morgeth Surpassing", 6),
    747671496: ("Pantheon: Insurrection Prime Revolutionary", 6),
}

# 活动英文名 → (中文名, 类别, 关卡中文名列表)（关卡顺序与 encounterList 下标对齐）
_CP_ACT_CN: dict[str, tuple[str, str, list[str]]] = {
    "Last Wish": ("最后一愿", "raid",
                  ["卡莉", "舒罗-祈", "莫瑞斯", "玉匣", "瑞文", "女王行走"]),
    "Garden of Salvation": ("救赎花园", "raid",
                            ["神圣心智·躲避", "神圣心智·召唤", "神圣心智", "圣洁心智"]),
    "Deep Stone Crypt": ("深岩墓室", "raid",
                         ["密码保险库", "阿特拉克斯-1", "下降通道", "塔尼克"]),
    "Vault of Glass": ("玻璃拱顶", "raid",
                       ["汇流点", "预言者", "圣殿骑士", "石像鬼", "闸门看守", "阿塞恩"]),
    "Vow of the Disciple": ("门徒誓约", "raid", ["夺取", "守墓人", "倾覆者", "鲁尔克"]),
    "King's Fall": ("国王的陨落", "raid",
                    ["大殿", "战争祭司", "戈尔戈罗斯", "奥尔里克斯之女", "奥里克斯"]),
    "Root of Nightmares": ("梦魇根源", "raid", ["灾变", "分裂", "宏观宇宙", "涅扎瑞克"]),
    "Crota's End": ("克洛塔的末日", "raid", ["深渊", "王魂桥", "伊·尤特", "克洛塔"]),
    "Salvation's Edge": ("救赎的边缘", "raid",
                         ["地基", "耗散", "宝库", "维尔提", "见证者"]),
    "The Desert Perpetual": ("永恒沙漠", "raid", ["科雷戈斯"]),
    "Shattered Throne": ("破碎王座", "dungeon", ["厄瑞玻斯", "沃格斯", "杜尔·因卡鲁"]),
    "Pit of Heresy": ("异端深渊", "dungeon",
                      ["死灵之城", "绝望隧道", "苦难之厅", "圣所", "祖尔马克"]),
    "Prophecy": ("预言", "dungeon",
                 ["天堂-地狱", "方阵回声", "荒原", "六面体", "死海", "族长回声"]),
    "Grasp of Avarice": ("贪婪之握", "dungeon",
                         ["天空守望", "锈蚀跳板", "弗利兹亚", "飙车段", "护盾破坏", "大盗阿瓦罗克"]),
    "Duality": ("二象性", "dungeon", ["梦魇加尔兰", "解封玉匣", "梦魇卡塔尔"]),
    "Spire of the Watcher": ("守望者尖塔", "dungeon", ["攀塔", "阿克勒斯", "珀西斯"]),
    "Ghosts of the Deep": ("深渊机灵", "dungeon", ["巢族仪式", "埃克萨", "西玛玛"]),
    "Warlord's Ruin": ("战争领主的废墟", "dungeon", ["拉斯尔", "风暴关", "赫夫德的复仇"]),
    "Vesper's Host": ("晚星之主", "dungeon",
                      ["韦斯珀站", "统一雷内克斯", "腐化傀儡", "破冰者催化"]),
    "Sundered Doctrine": ("分离教义", "dungeon", ["阿斯福德尔", "生命之锁", "被抹除者克雷夫"]),
    "Equilibrium": ("平衡", "dungeon", ["", "", "", "最终 Boss"]),
    "Pantheon: Calus Resplendent": ("众神殿：辉煌卡鲁斯", "pantheon",
                                    ["阿尔戈斯", "加尔兰", "卡鲁斯"]),
    "Pantheon: Morgeth Surpassing": ("众神殿：超越摩格斯", "pantheon",
                                     ["战争祭司", "神圣心智", "摩格斯"]),
    "Pantheon: Insurrection Prime Revolutionary": ("众神殿：革命暴动首领", "pantheon",
                                                   ["起义至尊"]),
}
# 卡片排序：突袭（按发售序）→ 地牢 → 万神殿
_CP_KIND_ORDER = {"raid": 0, "dungeon": 1, "pantheon": 2}
_CP_ACT_ORDER = ("最后一愿", "救赎花园", "深岩墓室", "玻璃拱顶", "门徒誓约", "国王的陨落",
                 "梦魇根源", "克洛塔的末日", "救赎的边缘", "永恒沙漠",
                 "破碎王座", "异端深渊", "预言", "贪婪之握", "二象性", "守望者尖塔",
                 "深渊机灵", "战争领主的废墟", "晚星之主", "分离教义", "平衡",
                 "众神殿：革命暴动首领", "众神殿：辉煌卡鲁斯", "众神殿：超越摩格斯")
_CP_MEMO: dict = {}          # {"at": 时间戳, "data": …} 进程内 90 秒缓存
_CP_URL = "https://d2checkpoint.com/_actions/bots.getBotsFromDb"
_CP_TTL = 90
_ACTS_CACHE: dict | None = None


def _acts_manifest() -> dict:
    """manifest_index/activities.json（官方 DestinyActivityDefinition 精简表：
    hash → {name: 官方中文名, pgcr/icon: 官方活动图}），本进程内只读一次"""
    global _ACTS_CACHE
    if _ACTS_CACHE is None:
        try:
            _ACTS_CACHE = json.load(open(_idx_file("activities.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _ACTS_CACHE = {}
    return _ACTS_CACHE


def _parse_devalue_bots(payload) -> list[dict]:
    """devalue 数组式引用图 → bot 字典列表。根在 A[0]，对象是「{键: 绝对下标}」模板。"""
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("响应不是 devalue 数组")
    A = payload

    def deref(i, seen=frozenset()):
        if not isinstance(i, int) or not (0 <= i < len(A)) or i in seen:
            return None
        v = A[i]
        if isinstance(v, dict):
            return {k: deref(j, seen | {i}) for k, j in v.items()}
        if isinstance(v, list):
            return [deref(j, seen | {i}) for j in v]
        return v

    root = deref(0)
    if isinstance(root, dict):
        root = [root]
    bots = [b for b in (root or []) if isinstance(b, dict) and b.get("name")]
    if not bots:
        raise RuntimeError("devalue 里解析不出 bot 列表")
    return bots


async def _web_post_json(url: str, payload) -> object:
    """第三方 POST/JSON（独立客户端，不把 Bungie API Key 带出去；带浏览器头防拦）"""
    loop = asyncio.get_running_loop()
    c = _WEB_CLIENTS.get(loop)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0.0.0 Safari/537.36"},
            timeout=20, follow_redirects=True)
        _WEB_CLIENTS[loop] = c
    r = await c.post(url, json=payload, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Origin": "https://d2checkpoint.com", "Referer": "https://d2checkpoint.com/"})
    r.raise_for_status()
    return r.json()


@_traced("进度点")
async def fetch_checkpoints(force: bool = False) -> dict:
    """d2checkpoint.com 在线 bot 的尾王点位 + 官方接口核对「可进/满员/离场」。

    返回 {"ok", "rows": [{bot, act, act_en, boss, kind, kind_cn, state, players,
                          fireteam, encounter}], "ts"}；网络/解析失败抛异常由指令层兜底。
    state：ready=有位（绿） / full=满员（灰） / gone=已离场（灰） / unknown=核对失败。"""
    now = time.time()
    if not force and _CP_MEMO.get("at") and now - _CP_MEMO["at"] < _CP_TTL:
        return _CP_MEMO["data"]
    payload = await _web_post_json(_CP_URL, {})
    bots = _parse_devalue_bots(payload)
    rows: list[dict] = []
    for b in bots:
        try:
            h = int(b.get("activityHash") or 0)
        except (TypeError, ValueError):  # noqa: PERF203
            continue
        if h == 0:                            # 不在线的 bot 不上卡
            continue
        en, fireteam = _CP_HASH_EN.get(h) or (f"活动 {h}", 6)
        zh, kind, encs = _CP_ACT_CN.get(en) or (en, "raid" if fireteam >= 6 else "dungeon", [])
        idx = int(b.get("encounter") or 0)
        boss = encs[idx] if 0 <= idx < len(encs) and encs[idx] else ""
        stage = ("尾王" if (encs and idx == len(encs) - 1) else f"第{idx + 1}关")
        # 活动名/图标用官方 manifest（Pantheon 官方译名是「众神殿：…」这类，别自己音译）
        am = _acts_manifest().get(str(h)) or {}
        act = str(am.get("name") or "").split(":")[0].strip() or zh
        img = str(am.get("pgcr") or am.get("icon") or "")
        rows.append({"bot": b["name"], "act": act, "act_en": en, "boss": boss,
                     "stage": stage, "img": img,
                     "kind": kind, "kind_cn": {"raid": "突袭", "dungeon": "地牢",
                                               "pantheon": "万神殿"}[kind],
                     "fireteam": fireteam, "players": 0, "state": "unknown",
                     "encounter": idx, "membership_id": str(b.get("membershipId") or "")})
    rows.sort(key=lambda r: (_CP_KIND_ORDER.get(r["kind"], 9),
                             _CP_ACT_ORDER.index(r["act"]) if r["act"] in _CP_ACT_ORDER else 99,
                             r["encounter"]))
    # 逐 bot 核对实时状态（官方 transitory：人数 + 是否还在活动里），并发拉省时间
    sem = asyncio.Semaphore(5)

    async def _status(r: dict):
        mid = r.get("membership_id") or ""
        if not mid:
            return
        async with sem:
            try:
                resp = await client().get(f"/Platform/Destiny2/3/Profile/{mid}/",
                                          params={"components": "1000"})
                j = resp.json() if hasattr(resp, "json") else resp
                td = (((j or {}).get("Response") or {}).get("profileTransitoryData") or {})
                data_ = td.get("data") or {}
                cur = data_.get("currentActivity") or {}
                n = int(cur.get("numberOfPlayers") or 0)
                r["players"] = n
                if not cur or n <= 0:
                    r["state"] = "gone"
                elif n >= r["fireteam"]:
                    r["state"] = "full"
                else:
                    r["state"] = "ready"
            except Exception:  # noqa: BLE001
                r["state"] = "unknown"

    await asyncio.gather(*(_status(r) for r in rows))
    for r in rows:
        r.pop("membership_id", None)
    # 已离场/状态没核对上的＝大概率蹭不了，不上榜（用户要求只留能用的）
    rows = [r for r in rows if r["state"] in ("ready", "full")]
    data = {"ok": True, "rows": rows, "ts": now}
    _CP_MEMO.clear()
    _CP_MEMO.update({"at": now, "data": data})
    return data


def checkpoints_text(data: dict) -> str:
    """/进度 的可复制文字版（小日向式）：点位行 + 下一行 /j 编号，玩家长按整行复制"""
    rows = data.get("rows") or []
    if not rows:
        return ("当前没有可用进度点（bot 都在休息）\n"
                "每周三凌晨 1 点周重置后点位最全，稍后再来")
    dot = {"ready": "🟢", "full": "🟡", "gone": "⚪", "unknown": "⚪"}
    lines = []
    for r in rows:
        st = r.get("state") or "unknown"
        if st == "ready":
            n = f"有位 {r.get('players')}/{r.get('fireteam')}"
        elif st == "full":
            n = "满员"
        elif st == "gone":
            n = "已离场"
        else:
            n = "状态未知"
        pos = r.get("stage") or ""
        if r.get("boss"):
            pos = f"{pos}·{r['boss']}"
        lines.append(f"{dot.get(st, '⚪')} {r['act']} {pos}（{n}）")
        lines.append(f"/j {r['bot']}")
    lines.append("——轨道界面聊天框粘贴 /j 进车，进本开打→团灭→退队即存点（本周有效）")
    return "\n".join(lines)

