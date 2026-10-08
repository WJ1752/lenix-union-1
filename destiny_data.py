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

import bot_runtime
import bungie_status as bst
import name_i18n
from bungie_status import BungieMaintenanceError
from jsonio import dump_json


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


# ---------- 响应判定 / 补查（维护期的第二道检查） ----------
# 为什么要有这一层：官方维护时不止会「报错」，还会「HTTP 200 + 空数据」——
# 不报错也没有内容，各调用点以前各自静默退化（返回 {} / [] / 0），统计就变成了
# 「全是 0」的假结果（2026-10-06 维护期群里查出来一堆错数据就是这么来的）。
# 这里统一把响应判成三态，调用点据此决定「照旧按空处理」还是「复核后报错」。
_NA_CODES = {1601, 1620, 1653}   # 档案不存在 / 角色不存在 / PGCR 不存在：业务上「就是没有」


def _resp_check(r) -> str:
    """每个 API 响应过一遍，返回 "ok" / "nocache" / "throttle"

    · 命中维护/系统关闭 → 点亮维护态并抛 BungieMaintenanceError（"nocache"）
    · 正常响应 → 记一笔 note_ok（维护窗口起点、恢复信号都靠它）→ "ok"
    · 其它业务错误码 → "nocache"：以前只看 HTTP 200 就缓存，维护期
      「200 + ErrorCode 5」的脏响应能在缓存里躺到 6 小时（PGCR/Manifest 档）
    · 纯限流（31/36/37…）→ "throttle"：**不抛**。限流是「请求太密」不是
      「服务器关了」，几千场的 PGCR 长扫描撞一下限流不该整条任务失败——
      交回调用点按各自的老口径退化（这次当作没拿到），由 _call 先等两秒重试一次。
    """
    body = r.content or b""
    # 快路径：官方 JSON 是紧凑格式（`"ErrorCode":1` 无空格），且包体里 Response 在前、
    # ErrorCode 在末尾附近收尾。命中就直接放行——PGCR 这种几百 KB 的包体不必再整份
    # 解析一遍（各调用点稍后还会各自 r.json() 一次，能省一趟是一趟）。
    if body.startswith(b"{") and b'"ErrorCode":1,' in body[-160:]:
        bst.note_ok()
        return "ok"
    d = None
    if body.strip()[:1] == b"{":
        try:
            d = json.loads(body.decode("utf-8-sig"))
        except Exception:  # noqa: BLE001 解析不了当非 JSON 处理
            d = None
    if isinstance(d, dict):
        hit = bst.classify_json(d)
        if hit:
            kind, detail = hit
            if kind == "throttled":
                return "throttle"
            bst.trip(kind, detail)
            raise bst.BungieMaintenanceError(bst.text())
        if d.get("ErrorCode") == 1:
            bst.note_ok()
            return "ok"
        return "nocache"
    # 非 JSON（官方维护页）或 5xx
    bst.note_http(r.status_code, body[:4000])
    if bst.is_down():
        raise bst.BungieMaintenanceError(bst.text())
    return "nocache"


class DataSuspiciousError(RuntimeError):
    """接口这次给的数据不完整/不可信（疑似官方维护或异常）

    宁可报错让用户稍后重发，也不出一份错的统计——用户看到的错数字比看不到数字更糟。
    """


def _verdict(resp: dict) -> tuple[str, str]:
    """错误码体检（补查层统一口径）→ (verdict, 说明)

    ok  = ErrorCode==1：有没有内容由调用点自己判（空列表有时是正常语义：翻到底了）
    na  = 业务上「就是没有」（1601 档案不存在 / 1620 角色不存在 / 1653 PGCR 不存在）
    bad = 服务端出问题却没给可用数据（维护期的空 Response、未知错误码）：
          调用点应复核一次，仍旧可疑就抛 DataSuspiciousError，绝不能静静变成 0
    """
    if not isinstance(resp, dict):
        return "bad", "响应不是 JSON 对象"
    code = resp.get("ErrorCode")
    if code == 1:
        return "ok", ""
    if code in _NA_CODES:
        return "na", str(resp.get("ErrorStatus") or code)
    detail = f"{resp.get('ErrorStatus') or code}({code}) {str(resp.get('Message') or '')[:80]}"
    return "bad", detail.strip()


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
        # no_cache=True：跳过读缓存也不写缓存（进行中的 PGCR 实时名单等）
        no_cache = kw.pop("no_cache", False)
        params = kw.get("params") or {}
        body = kw.get("json") if isinstance(kw.get("json"), dict) else None
        ttl = 0 if no_cache else _ttl_for(url, params)
        key = self._key(url, params, body) if ttl else ""
        if key:
            hit = self._hit(key, url)
            if hit is not None:
                return hit
        # 维护闸门：维护中不打接口、不写缓存，直接把「维护中」抛给上层
        await bst.guard()
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
        verdict = _resp_check(r)   # 维护类响应在这里抛，且不会被缓存
        if verdict == "throttle":
            # 官方限流：等两秒再要一次，多半就过了；还是限流就原样返回，
            # 由调用点按自己的老口径退化（比如这场 PGCR 归到 missed）
            await asyncio.sleep(2.0)
            try:
                r2 = await getattr(self._inner, method)(url, **kw)
                if (v2 := _resp_check(r2)) != "throttle":
                    r, verdict = r2, v2
            except httpx.TimeoutException:
                pass
        if verdict == "ok":
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


# 平台别名 → membershipType；PLATFORM_NAMES 反查显示名（绑定多平台提示用）
_PLATFORM_IDS = {"steam": 3, "psn": 2, "playstation": 2, "ps": 2, "xbox": 1, "xbx": 1,
                 "epic": 6, "stadia": 5, "bnet": 4, "战网": 4}
PLATFORM_NAMES = {1: "Xbox", 2: "PSN", 3: "Steam", 4: "Battle.net", 5: "Stadia",
                  6: "Epic", 10: "Demon", 254: "Bungie.net"}


def _bound_platform(fname: str, code: int) -> str:
    """绑定元数据里固化的平台选择（/绑定 名字#编号 平台 时记录）→ 后续查询都按它解析。
    一个 bungie 名字挂多个**真**档案（双平台老玩家，无跨存档）时，光探测分不出该查哪个，
    只有用户在绑定时表态才算数"""
    try:
        want = f"{fname}#{code}".lower()
        for v in (load_binding_meta() or {}).values():
            if isinstance(v, dict) and v.get("platform") and \
                    str(v.get("name", "")).lower() == want:
                return str(v["platform"])
    except Exception:  # noqa: BLE001
        pass
    return ""


async def resolve_member(name: str, platform: str = ""):
    """玩家名#编号 → dict(mtype, mid, display, code, candidates, platform)；
    先精确查 Bungie，查不到（带错编号）再回落本地索引。
    platform（steam/psn/xbox/epic…）可强制平台：名字下挂多个真档案时由用户指定。"""
    if "#" not in name:
        return None
    fname, _, code = name.partition("#")
    code = int(code)
    platform = (platform or _bound_platform(fname, code)).strip().lower()
    # 精确查询（对大小写敏感）
    r = await client().post(
        "/Platform/Destiny2/SearchDestinyPlayerByBungieName/-1/",
        json={"displayName": fname, "displayNameCode": code},
    )
    resp = _parse(r)
    # 以前这里不查 ErrorCode：维护/限流时搜索接口回错误码、候选为空，静默变成
    # 「没找到玩家 XXX」，用户以为名字打错了（10-06 维护期就是这样）
    sv, why = _verdict(resp)
    cands = resp.get("Response") or []
    n_all = len(cands)
    if platform:
        want = _PLATFORM_IDS.get(platform)
        cands = [p for p in cands if p["membershipType"] == want] if want else cands
        if not cands:
            if sv == "bad":
                raise DataSuspiciousError(
                    f"玩家搜索接口这次没返回结果（{why}），疑似官方维护或接口异常，"
                    f"稍后重发一次即可")
            return None
    # 注：Bungie 已下线免鉴权模糊搜索（SearchDestinyPlayers 404），带错编号只能报没找到
    # 排序：跨存档主平台(crossSaveOverride)优先，其余候选跟后。
    # 同一个 bungie 名字可能挂在多个平台成员号上，其中有的平台从没玩过 D2
    # （搜"林黛玉倒拔垂杨柳#7437"会同时出 PSN 空号 + Steam 真号），不能盲取第一条：
    # 逐个探测 GetProfile，第一个真有档案的才算数（1601 响应有 180s 缓存，重复查询不亏）
    ordered = [p for p in cands
               if p.get("crossSaveOverride") and p["membershipType"] == p["crossSaveOverride"]]
    ordered += [p for p in cands if p not in ordered]
    best = None
    valid = True
    for p in ordered:
        mt_p = p.get("crossSaveOverride") or p["membershipType"]
        # 单候选免探测（绝大多数人）；但用户点名了平台时必须探——绑错空号要在绑定时就提醒
        if (len(ordered) == 1 and not platform) or \
                await _has_destiny_account(mt_p, p["membershipId"]):
            best = p
            break
    if best is None and ordered:
        best = ordered[0]     # 全都探不到：沿用第一条，让后续查询报出正常错误
        valid = False         # 用户点名了平台但没有档案 → 绑定时好给提示
    if best:
        mtype = best.get("crossSaveOverride") or best["membershipType"]
        display = best["bungieGlobalDisplayName"]
        dcode = best["bungieGlobalDisplayNameCode"]
        harvest_player(f"{display}#{fmt_code(dcode)}", best["membershipId"], mtype)
        return {"mtype": mtype,
                "mid": best["membershipId"], "display": display,
                "code": dcode,
                "icon": BASE + best["iconPath"] if best.get("iconPath") else "",
                "candidates": n_all,
                "platform": PLATFORM_NAMES.get(mtype, str(mtype)),
                "valid_account": valid}
    # Bungie 没查到：回落本地索引（PGCR 采集的 seen_players.json）
    seen = seen_players()
    if name in seen:
        mtype, mid, _ts = seen[name]
        return {"mtype": mtype, "mid": mid, "display": fname,
                "code": code, "icon": ""}
    if sv == "bad":
        # 搜索本身就没成功、本地索引里也没有：这不是「没找到玩家」，别误导用户
        raise DataSuspiciousError(
            f"玩家搜索接口这次没返回结果（{why}），疑似官方维护或接口异常，"
            f"稍后重发一次即可")
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
        dump_json(_SEEN_PATH, _SEEN)


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


class PlayerLookupError(RuntimeError):
    """玩家搜到了但档案不可用：message 是直接给用户看的提示"""


class ProfilePrivateError(PlayerLookupError):
    """该玩家的命运2档案设为了私密：API 拒绝返回角色/记录等数据"""


async def _has_destiny_account(mtype: int, mid: str) -> bool:
    """这个平台成员号下有没有真的命运2档案（没玩过 D2 的平台成员号 GetProfile 会 1601）"""
    try:
        r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/", params={"components": "100"})
    except bst.BungieMaintenanceError:
        raise            # 维护是「问不了」，不能当成「这个号没档案」（会把绑定标成空号）
    except Exception:  # noqa: BLE001  探测失败按没有算
        return False
    return _parse(r).get("ErrorCode") == 1


async def get_profile(mtype: int, mid: str) -> dict:
    r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/", params={"components": "100,200"})
    resp = r.json()
    if resp.get("ErrorCode") == 1601:
        if resp.get("ErrorStatus") == "DestinyAccountNotFound":
            raise PlayerLookupError("这个平台成员号下没有命运2档案（对方可能主要玩别的平台）")
        raise ProfilePrivateError("该玩家的命运2档案设为了私密，无法查询")
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "Bungie API 错误"))
    return resp["Response"]


async def char_stats(mtype: int, mid: str, char_id: str, groups: str,
                     no_cache: bool = False) -> dict:
    """角色生涯统计（补查层：可疑的空响应复核一次再决定报不报错）

    实测（2026-10-08）：真角色的 groups=101,103,104 一定有 allPvP/allPvE/… 那几块；
    已删角色则是「键还在、每块为空」。所以 **Response 整块为空** 只可能是官方
    没给数据——维护期返回空响应时以前直接变成「全是 0」的假卡片。
    """
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{char_id}/Stats/",
        params={"groups": groups}, no_cache=no_cache,
    )
    resp = r.json()
    v, why = _verdict(resp)
    payload = resp.get("Response")
    if v == "na":
        return {}
    if v == "ok" and payload:
        return payload
    if no_cache:
        raise DataSuspiciousError(
            f"角色生涯统计读取失败（{why}）：Bungie 没返回数据（疑似官方维护或接口异常），"
            f"已拦下避免出错误统计，稍后重发一次即可")
    # 第一遍可疑：绕开响应缓存再要一次，多半是踩在维护/抖动的窗口里
    return await char_stats(mtype, mid, char_id, groups, no_cache=True)


def _sum(stats_list: list[dict], mode: str) -> dict:
    """把多个角色的同一模式统计合并求和（比率取最后一项，最高值取各角色最大）"""
    keys = ("kills", "deaths", "assists", "activitiesEntered", "activitiesWon",
            "killsDeathsRatio", "killsDeathsAssists", "precisionKills", "winRate",
            "secondsPlayed", "bestSingleGameKills", "longestKillSpree")
    out = {k: 0.0 for k in keys}
    for s in stats_list:
        at = s.get(mode, {}).get("allTime", {})
        for k in keys:
            if k in at:
                v = at[k]["basic"]["value"]
                if k in ("killsDeathsRatio", "killsDeathsAssists", "winRate"):
                    out[k] = v
                elif k in ("bestSingleGameKills", "longestKillSpree"):
                    out[k] = max(out[k], v)
                else:
                    out[k] += v
    if out["deaths"]:
        out["kd"] = out["kills"] / out["deaths"]
    else:
        out["kd"] = out.get("killsDeathsRatio", 0.0)
    return out


@_traced(lambda name: f"/玩家 {name}")
async def full_report(name: str, jid: str = "") -> dict:
    """玩家全量数据：档案 + 各角色 PVP/PVE/智谋 合并统计"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    return await full_report_member(member, jid=jid)


async def full_report_member(member: dict, jid: str = "") -> dict:
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    if not chars:
        raise LookupError(f"{member['display']} 档案下没有角色")

    def _jp(done: int, total: int) -> None:
        if jid and jid in JOBS:  # 同步进面板后台任务进度条
            JOBS[jid].update(done=int(done), total=int(total))

    chars_meta, pvp_list, pve_list, gmb_list = [], [], [], []
    nchars = len(chars)
    fdisp = f"{member['display']}#{fmt_code(member['code'])}"
    _jp(0, nchars + 1)
    log_progress(f"full:{mid}", 0, nchars + 1, label=f"/玩家 {fdisp}", force=True,
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
        _jp(ci, nchars + 1)
        log_progress(f"full:{mid}", ci, nchars + 1, label=f"/玩家 {fdisp}",
                     extra=f"角色 {ci}/{nchars} 统计完成")

    # 智谋：官方聚合接口已下线，从对局历史聚合（跨角色，去重）
    _jp(nchars, nchars + 1)
    log_progress(f"full:{mid}", nchars, nchars + 1, label=f"/玩家 {fdisp}", force=True,
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
    """Bungie 官方**生涯**统计（跨角色求和）；group: allPvP / allPvE

    智谋不在这里——它是 `pvecomp_gambit` 桶，得带 `modes=63` 请求才会出现在响应里，
    且字段比这几个多（荧光 / 入侵），见 `gambit_career()`；`_sum` 也认不出那些专属键。"""
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
    # 维护窗口内封存过的角色：封存水位和那段日子都不算数
    #   · 官方维护期每日统计会整段缺失，而 done 一旦推过去那些天就永远不会再拉
    #   · 水位回退到窗口起点，配合 _TIME_TAIL_DAYS 会往前多拉 10 天补齐
    #   · 窗口起点之后的日表条目直接删掉，免得残留维护期算出的偏小值
    cs = bst.clean_since()
    if cs:
        ws = bst.windows()
        wstart = ws[-1][0] if ws else cs
        # 日表键来自对局 period（API 给的 UTC 日），这里也按 UTC 日算才对得上；
        # 用北京日会晚 8 小时、少作废几天的缓存（全盘北京时间指的是复位/展示口径）
        since_day = time.strftime("%Y-%m-%d", time.gmtime(wstart))
        n = 0
        for ent in _time_cache.values():
            if not isinstance(ent, dict) or (ent.get("mw") or 0) >= cs:
                continue
            if (ent.get("done") or "") >= since_day:
                ent["done"] = (datetime.date.fromisoformat(since_day)
                               - datetime.timedelta(days=1)).isoformat()
            days = ent.get("days") or {}
            for d in [d for d in days if d >= since_day]:
                days.pop(d, None)
            ent["mw"] = cs
            n += 1
        if n:
            print(f"[维护] season_time_cache 回退 {n} 个角色的封存水位到 {since_day}，"
                  f"这段日子重拉", flush=True)
            _save_time_cache()


def _save_time_cache():
    cs = bst.clean_since()
    for ent in _time_cache.values():
        if isinstance(ent, dict):
            ent["mw"] = cs          # 写盘时刻的「维护代次」：判定上面那次回退用
    try:
        dump_json(_writable_path(_TIME_CACHE_FILE), _time_cache, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重拉
        pass


async def _fetch_daily_secs(mtype: int, mid: str, cid: str,
                            day0: str, day1: str) -> dict[str, float] | None:
    """一段窗口（≤31 天）的 {日期: 在场秒数}；角色已删（1601/1620）返回空表

    **取不到数据时返回 None**——调用点绝不能因为 None 推进封存水位，否则这段日子
    会被永久标记成「已拉过」，时长统计从此少一大截（维护期踩过）。
    """
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
        params={"periodType": "Daily", "groups": "General",
                "daystart": day0, "dayend": day1})
    resp = r.json()
    v, why = _verdict(resp)
    if v == "na":
        return {}
    if v != "ok":
        print(f"[补查] {time.strftime('%H:%M:%S')} 每日统计 {day0}~{day1} 取不到数据"
              f"（{why}），本次不推进封存水位", flush=True)
        return None
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
    ok = failed = 0
    for a, b in _month_chunks(fetch_from, today.isoformat()):
        got = await _fetch_daily_secs(mtype, mid, cid, a, b)
        if got is None:
            # 没拿到就什么都不动：不合并、不推进封存点、不落盘（下次还会重拉这段）
            failed += 1
            continue
        days.update(got)
        ok += 1
        # 封存点推进到「窗口结束」与「今天-10 天」的较早者（当前月只封到今天-10）
        seal = min(datetime.date.fromisoformat(b), today - datetime.timedelta(days=_TIME_TAIL_DAYS))
        if seal.isoformat() > (ent.get("done") or ""):
            ent["done"] = seal.isoformat()
        _save_time_cache()
    if ok == 0 and failed:
        # 一段都没拉到的（维护/接口异常）：不能静静当成「这个角色 0 小时」
        raise DataSuspiciousError(
            f"每日时长统计一段都没取到（{failed} 段失败）：疑似官方维护或接口异常，"
            f"已拦下避免出错误统计，稍后重发一次即可")
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
        v, why = _verdict(resp)
        if v == "na":
            continue
        if v != "ok":
            # 以前是 continue：某一批挂了就少算那一批的时长/击杀，卡片上看不出来
            raise DataSuspiciousError(
                f"角色统计第 {bi}/{len(batches)} 批读取失败（{why}）："
                f"疑似官方维护或接口异常，已拦下避免出错误统计，稍后重发一次即可")
        for key, v2 in (resp.get("Response") or {}).items():
            out.setdefault(key, v2)
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
async def career_report(name: str, jid: str = "") -> dict:
    """生涯面板数据：分赛季等级 + 分职业 / 分模式时长 + 三模式生涯聚合。

    全程只打 GetProfile / GetHistoricalStats，不逐场拉 PGCR，所以是秒级出图。
    """
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    return await career_report_member(member, jid=jid)


async def career_report_member(member: dict, jid: str = "") -> dict:
    mtype, mid = member["mtype"], member["mid"]
    r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                           params={"components": "100,200,202"})
    resp = r.json()
    if resp.get("ErrorCode") == 1601:
        if resp.get("ErrorStatus") == "DestinyAccountNotFound":
            raise PlayerLookupError("这个平台成员号下没有命运2档案（对方可能主要玩别的平台）")
        raise ProfilePrivateError("该玩家的命运2档案设为了私密，无法查询")
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

    def _jp(done: int, total: int) -> None:
        if jid and jid in JOBS:  # 同步进面板后台任务进度条
            JOBS[jid].update(done=int(done), total=int(total))

    _jp(0, total_steps)
    log_progress(f"career:{mid}", 0, total_steps, label=f"/生涯 {disp}", force=True,
                 extra=f"拉取 {nchars} 个角色的分模式历史统计（每角色 {nb} 批）")
    for ci, (cid, c) in enumerate(chars_raw.items(), 1):
        def _on_batch(bi, bn, ci=ci):
            _jp((ci - 1) * nb + bi, total_steps)
            log_progress(
                f"career:{mid}", (ci - 1) * nb + bi, total_steps,
                label=f"/生涯 {disp}", extra=f"角色 {ci}/{nchars} · 第 {bi}/{bn} 批")

        st = await _char_stats_full(mtype, mid, cid, on_batch=_on_batch)
        _jp(ci * nb, total_steps)
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
    if not out and q:  # 英文/繁体名的联想（面板搜索框）
        return _weapons_by_alt_name(q, limit)
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
    返回 [{'hash', 'season', 'event'}]，下标 0 = 版本 1。
    异域催化武器排最前：简中有同名异武器（"龙息"= 异域火箭筒 + 一把冲锋枪），
    纯按赛季排会把冲锋枪当"版本1"默认打开，查询异域的人要的不是它"""
    items = [(h, w) for h, w in _weapons_full.items() if w["name"] == name]
    if not items:
        return []
    items.sort(key=lambda x: (not bool((x[1].get("plugs") or {}).get("catalysts")),
                              _weapon_versions.get(x[0], {}).get("season", 0), x[0]))
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
    if not out and q:  # 英文名 / 台服繁体名兜底（Fatebringer、龍息）：三语索引直接给 hash
        out = _weapons_by_alt_name(q, limit)
    return out[:limit]


def _weapons_by_alt_name(q: str, limit: int = 24) -> list[dict]:
    """英文/繁体武器名 → 武器记录（索引里精确命中在前，其次前缀/子串命中）"""
    out = []
    for h in name_i18n.match_weapons(q, limit=limit * 2):
        rec = _weapons_full.get(h)
        if rec:
            out.append(dict(rec, hash=h))
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
    if not exact and not part and q:  # 英文/繁体 perk 名（Incandescent、熾熱）走三语索引
        for h in name_i18n.match_perks(q, limit=limit):
            p = _perks.get(h)
            if p:
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
    如 vog 打成 vod）；全命中为空才走模糊，避免正常搜索被带偏。
    英文/繁体套装名（Seventh Seraph、第七熾天使）先经三语索引换算成简体名再走同一套匹配。"""
    q = _norm_set(q)
    if not q:
        return []

    def _pass(qn: str) -> list[dict]:
        out = []
        for s in _armor_sets:
            names = {_norm_set(s["name"])} | {_norm_set(a) for a in s.get("aliases", [])}
            src = _norm_set(s.get("source") or "")
            if qn in names or (src and qn == src):   # 来源（副本名）也可精确搜
                out.insert(0, s)
            elif any(qn in n for n in names):
                out.append(s)
        return out

    out = _pass(q)
    if out:
        return out
    alt = name_i18n.set_name(q)  # 英文/繁体套装名 → 简体套装名
    if alt:
        out = _pass(_norm_set(alt))
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
    """对局历史（mode: 5=所有PVP 63=智谋 7=所有PVE 0=全部活动）

    补查层：以前这里**完全不看 ErrorCode**，维护/限流时直接当成「没有对局」，
    翻页静默停在一半、卡片按残缺数据出图（10-06 维护就是这么错的）。
    Response 是 {} 属于正常语义（翻到底了/该角色没打过），只有真报错才抛。
    """
    r = await client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/Activities/",
        params={"mode": mode, "count": count, "page": page} if mode else
               {"count": count, "page": page},
    )
    resp = r.json()
    v, why = _verdict(resp)
    if v == "bad":
        raise DataSuspiciousError(
            f"对局历史读取失败（{why}）：Bungie 没返回对局数据（疑似官方维护或接口异常），"
            f"已拦下避免出错误统计，稍后重发一次即可")
    if v == "na":
        return []
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
            "opp": int(v.get("opponentsDefeated", {}).get("basic", {}).get("value", 0)),
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
    v, why = _verdict(resp)
    if v == "na":
        return {}          # 1653：这场对局不存在（过期/被删）——业务语义，照旧当空
    if v == "bad" or not resp.get("Response"):
        # 服务端没给数据（维护/异常）时别让上层当成「这场没有明细」继续算——
        # 维护期这样会静默少算一堆武器击杀
        raise DataSuspiciousError(
            f"对局详情读取失败（{why or 'Response 为空'}）：疑似官方维护或接口异常，"
            f"已拦下避免出错误统计，稍后重发一次即可")
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
        dump_json(_RR_RANK_PATH, _rr_ranks())
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


# 通关数/最快全程主源：api.raidreport.dev 的 /{raid|dungeon}/player/{mid}。
# 官方对局历史会被裁剪（只留 ~2020-03 起、已删角色的场完全拉不到），所以官方历史算出的
# 通关数永远低于 raid.report 页面（它是历史累计库）；该接口逐 hash 给 clears/fullClears/
# fastestFullClear，与其页面显示一致。30 分钟 TTL；拉不到时退回过期缓存；连缓存都没有
# 才回落官方历史口径（此时「最快全程」只认 PGCR 复核过从头开始的场）。
_RR_STATS_PATH = os.path.join("raidreport_stats.json")
_RR_STATS: dict | None = None
_RR_STATS_TTL = 1800


def _rr_stats_cache() -> dict:
    global _RR_STATS
    if _RR_STATS is None:
        try:
            _RR_STATS = json.load(open(_RR_STATS_PATH, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _RR_STATS = {}
    return _RR_STATS


def _rr_stats_save() -> None:
    try:
        dump_json(_RR_STATS_PATH, _rr_stats_cache())
    except Exception:  # noqa: BLE001
        pass


async def _rr_stats(mid: str, mode: int) -> dict | None:
    """→ {"ts": 取数时刻, "acts": {hash: {"clears", "full", "ffc"}}}；拿不到且无缓存 → None"""
    kind = "dungeon" if mode == 82 else "raid"
    key = f"{mid}:{kind}"
    ent = _rr_stats_cache().get(key)
    if ent and time.time() - ent.get("ts", 0) < _RR_STATS_TTL:
        return ent
    d, not_found = await _rr_fetch_json(f"{_RR_BASE}/{kind}/player/{mid}", str(mid))
    if d is None:
        # 404 当作「没打过」（空表缓存）；网络/CF 失败用过期缓存兜底，没有就 None
        if not_found:
            out = {"ts": time.time(), "acts": {}}
            _rr_stats_cache()[key] = out
            _rr_stats_save()
            return out
        return ent
    acts = {}
    for a in (d.get("response") or {}).get("activities") or []:
        v = a.get("values") or {}
        ffc = (v.get("fastestFullClear") or {}).get("value")
        acts[str(a.get("activityHash"))] = {
            "clears": int(v.get("clears") or 0),
            "full": int(v.get("fullClears") or 0),
            "ffc": int(ffc) if ffc else None,
        }
    out = {"ts": time.time(), "acts": acts}
    _rr_stats_cache()[key] = out
    _rr_stats_save()
    return out


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


def _merge_rr_stats(groups: dict, acts: dict) -> None:
    """raid.report 逐 hash 统计并入分组：通关数取 max（它是历史累计库，只会更全）、
    最快全程取 min；官方历史完全没有的组（老对局/已删角色）只在发售表里的主副本补建。"""
    for h, st in acts.items():
        if not st["clears"]:
            continue
        info = _activities.get(str(h)) or {}
        base, diff = split_activity(info.get("name") or "")
        if not base:
            continue
        is_master = diff == "大师"
        g = groups.get((base, is_master))
        if g is None:
            # 杂项活动（众神殿分身之类）没有历史组就跳过
            if base not in _RAID_ORDER_IDX:
                continue
            rel = _RAID_RELEASE_UTC.get(base) or ""
            g = groups.setdefault((base, is_master), {
                "name": base, "master_mode": is_master,
                "ref": 0, "pgcr": info.get("pgcr", ""),
                "plays": 0, "clears": 0, "best": None, "last": "", "ffc": None,
                "flawless": 0, "solo": 0, "duo": 0, "trio": 0,
                "solo_fl": 0, "duo_fl": 0, "trio_fl": 0, "master": 0,
                "diffs": [diff] if diff else [],
                "day_one": 0, "week_one": 0,
                "rel_d1": _ts_shift(rel, 24 * 3600) if rel else "",
                "rel_w1": _ts_shift(rel, 7 * 24 * 3600) if rel else "",
            })
        # 同一副本会有多个活动 hash（原版/重制版/竞赛版等），页面口径是逐难度【求和】
        g["rr_sum"] = g.get("rr_sum", 0) + st["clears"]
        if g["rr_sum"] > g["clears"]:
            g["clears"] = g["rr_sum"]
            if g["clears"] > g["plays"]:
                g["plays"] = g["clears"]   # 参与 ≥ 通关，别让卡片自相矛盾
        if diff and diff not in g["diffs"]:
            g["diffs"].append(diff)
        ffc = st.get("ffc")
        if ffc and (g["ffc"] is None or ffc < g["ffc"]):
            g["ffc"] = ffc


# ---------- /raid /地牢 缓存 ----------
# 对局历史只增不减（官方按时间倒序返回，旧场永不改动），翻过的页不必重翻：
# 落盘每人的对局列表 +「统计到哪一场」(gate=最新对局 period)，下次只补 gate 之后的新场。
# PGCR 局面信息（是否从头开始/账号数/私局）是定局数据，一次拉取永久缓存。
_RAID_HIST_PATH = os.path.join("raid_history_cache.json")
_RAID_HIST: dict | None = None
_RAID_PGCR_PATH = os.path.join("raid_pgcr_cache.json")
_RAID_PGCR: dict | None = None


def _raid_hist_cache() -> dict:
    global _RAID_HIST
    if _RAID_HIST is None:
        try:
            _RAID_HIST = json.load(open(_RAID_HIST_PATH, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _RAID_HIST = {}
        # 维护窗口内翻出来的对局列表不可信（官方维护期会回残缺数据，而下面
        # 「上次全量翻过且一场没有」的分支会把它当成结论永久复用）
        # → 直接丢掉重翻
        bad = [k for k, v in _RAID_HIST.items()
               if isinstance(v, dict) and bst.suspect_at(v.get("ts"))]
        for k in bad:
            _RAID_HIST.pop(k, None)
        if bad:
            print(f"[维护] raid_history_cache 丢弃 {len(bad)} 条维护窗口内的缓存，"
                  f"下次查询重新翻取", flush=True)
            _raid_hist_save()
    return _RAID_HIST


def _raid_hist_save() -> None:
    try:
        dump_json(_RAID_HIST_PATH, _RAID_HIST)
    except Exception:  # noqa: BLE001
        pass


async def _pgcr_run_info_cached(instance: str) -> dict:
    """_pgcr_run_info + 永久缓存；拉取失败（空 dict）不缓存，下次再试"""
    global _RAID_PGCR
    if _RAID_PGCR is None:
        try:
            _RAID_PGCR = json.load(open(_RAID_PGCR_PATH, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _RAID_PGCR = {}
    ent = _RAID_PGCR.get(instance)
    if ent is not None:
        return ent
    info = await _pgcr_run_info(instance)
    if info:
        _RAID_PGCR[instance] = info
        dump_json(_RAID_PGCR_PATH, _RAID_PGCR)
    return info


@_traced(lambda name, mode: (f"/地牢 {name}" if mode == 82 else f"/raid {name}"))
async def raid_report(name: str, mode: int) -> dict:
    """Raid(4)/地牢(82) 报告：跨角色合并对局，按副本分组统计（标准与大师各成一组）"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    return await raid_report_member(member, mode)


@_traced(lambda member, mode, jid=None: (f"/地牢 {member['display']}" if mode == 82
                                         else f"/raid {member['display']}"))
def _merge_hist_page(acts: list[dict], gate: str, seen: set, matches: list) -> bool:
    """把一页对局历史并入增量结果（gate=缓存里已统计到的最新对局 period，分钟精度）。

    对局历史按时间倒序且只增不减：早于 gate 的旧场必定已在缓存里，跳过；
    gate 那一分钟内的新场（上一轮统计后才打的）靠 seen 去重兜住。
    返回 True = 该继续翻下一页。"""
    oldest = min((m["period"] for m in acts), default="")
    for m in acts:
        if gate and m["period"] < gate:
            continue
        key = m["instance"] or f"{m['ref']}{m['period']}"
        if key not in seen:
            seen.add(key)
            matches.append(m)
    # 页没满=到底了；整页不晚于缓存门=后面全是旧场，不用再翻
    return len(acts) >= 250 and not (gate and acts and oldest <= gate)


async def raid_report_member(member: dict, mode: int, jid: str | None = None) -> dict:
    mtype, mid = member["mtype"], member["mid"]
    # 跨存档全家桶：raid.report 页面把同一 bungie 账号下所有平台的历史合并显示，
    # 只拉主平台会少算跨平台前打的那些场（实测 Benson 克洛塔 页面87 vs 主平台86）
    accts = [(mtype, str(mid))]
    try:
        # 路径必须带 /Platform：漏了会打到 bungie.net 的 404 HTML 页，r.json() 抛
        # JSONDecodeError 被下面的 except 吞掉——跨存档合并会静默失效（少算跨平台场次）
        r = await client().get(f"/Platform/User/GetMembershipsById/{mid}/{mtype}/")
        for mm in ((r.json().get("Response") or {}).get("destinyMemberships") or []):
            mt2, md2 = mm.get("membershipType"), str(mm.get("membershipId") or "")
            if md2 and mt2 in (1, 2, 3, 4, 5, 6, 10) and (mt2, md2) not in accts:
                accts.append((mt2, md2))
    except Exception:  # noqa: BLE001  拿不到就只算主平台
        pass

    seen, matches = set(), []
    rname = "地牢" if mode == 82 else "raid"
    disp = f"{member['display']}#{fmt_code(member['code'])}"
    # 增量缓存：历史只增不减，已统计到 gate（最新一场）的旧场直接吃缓存，只补新场
    ckey = f"{mid}:{mode}"
    hist = _raid_hist_cache().get(ckey) or {}
    gate = hist.get("gate") or ""
    cplan: list[tuple[tuple[int, str], list[str]]] = []
    nunits = 0
    for at, am in accts:
        try:
            prof = await get_profile(at, am)
            chars_p = list((prof.get("characters", {}).get("data") or {}))
        except Exception:  # noqa: BLE001
            chars_p = []
        cplan.append(((at, am), chars_p))
        nunits += len(chars_p) or 1
    cur_chars = sorted(f"{at}:{am}:{cid}" for (at, am), cids in cplan for cid in cids)

    def _jp(done: int, total: int) -> None:
        if jid and jid in JOBS:  # 同步进面板后台任务进度条
            JOBS[jid].update(done=int(done), total=int(total))

    if not hist.get("matches") and hist.get("chars") == cur_chars:
        # 上次全量翻过且一场没有、角色没变：不可能冒出旧场，直接复用
        log_progress(f"raid:{mid}:{mode}", 0, 0, label=f"/{rname} {disp}", force=True,
                     extra="上次已全量翻过且无对局，直接复用缓存")
    else:
        seen = {m["instance"] or f"{m['ref']}{m['period']}" for m in (hist.get("matches") or [])}
        matches = list(hist.get("matches") or [])
        _jp(0, nunits * 40)
        log_progress(f"raid:{mid}:{mode}", 0, nunits * 40, label=f"/{rname} {disp}", force=True,
                     extra=(f"命中缓存（已有 {len(matches)} 场），只补新对局"
                            if gate else
                            f"翻取副本对局历史（{len(accts)} 个平台 · 每人最多 40 页 × 250 场）"))
        unit = 0
        for ai, ((at, am), cids) in enumerate(cplan):
            for cid in cids:
                unit += 1
                # 翻页拿全：早前只翻 3 页（750 场），老记录的低人通关会被截掉
                page = 0
                while page < 40:
                    if jid:  # 暂停/中止都在翻页边界上响应，不至于把一页翻到一半
                        await _job_checkpoint(jid)
                    acts = await activity_history(at, am, cid, mode, count=250, page=page)
                    page += 1
                    if not _merge_hist_page(acts, gate, seen, matches):
                        break
                    _jp(unit * 40 - 40 + page, nunits * 40)
                    log_progress(f"raid:{mid}:{mode}", unit * 40 - 40 + page, nunits * 40,
                                 label=f"/{rname} {disp}",
                                 extra=f"平台 {ai + 1}/{len(accts)} · "
                                       f"角色 {unit}/{nunits} · 第 {page + 1} 页 · 已收 {len(matches)} 场")
        # 补查守卫：这次一场都没翻到、但上次缓存里有（且角色没变）→ 多半是接口
        # 出了问题而不是「历史被清空」（历史只增不减），保留上次的缓存别覆盖
        if not matches and hist.get("matches") and hist.get("chars") == cur_chars:
            log_progress(f"raid:{mid}:{mode}", 0, 0, label=f"/{rname} {disp}", force=True,
                         extra=f"本次一场都没翻到（上次缓存有 {len(hist['matches'])} 场），"
                               f"疑似接口异常，保留上次缓存不覆盖")
        else:
            _raid_hist_cache()[ckey] = {
                "ts": time.time(), "chars": cur_chars,
                "gate": max((m["period"] for m in matches), default=gate),
                "matches": matches}
            _raid_hist_save()
    _jp(nunits * 40, nunits * 40)
    log_progress(f"raid:{mid}:{mode}", nunits * 40, nunits * 40, label=f"/{rname} {disp}",
                 force=True, extra=f"历史翻取完成，共 {len(matches)} 场，开始统计")
    matches.sort(key=lambda m: m["period"], reverse=True)
    # 展示用北京时间（period 保留 UTC 原值给首日/首周判定）
    for m in matches:
        m["period_cn"] = _cn8(m["period"])
        m["full_run"], m["private"], m["low_accounts"] = True, False, m["player_count"]

    # 特殊通关复核：0 死亡通关 / 低人通关才拉 PGCR —— 判断「是否从头开始打」。
    # 尾王检查点进去通掉尾王（哪怕 0 死）官方 PGCR 给 activityWasStartedFromBeginning=False，
    # 不算全程无暇（用户 2026-10-02 指定口径）；私局（自定义装载）也不进特殊徽章。
    rr = await _rr_stats(mid, mode)
    cand = [m for m in matches if m["completed"] and (m["deaths"] == 0 or 0 < m["player_count"] <= 3)]
    if rr is None:
        # raid.report 接口不可用时「最快全程」只能自己复核：最快的 24 场通关大概率包含
        # 检查点局，逐场验「从头开始」——验证过的才允许进「最快全程」
        fast = sorted((m for m in matches if m["completed"] and m["duration"] > 0),
                      key=lambda m: m["duration"])[:24]
        have = {id(m) for m in cand}
        cand = cand + [m for m in fast if id(m) not in have]
    if cand:
        log_progress(f"raid:{mid}:{mode}", 0, len(cand), label=f"/{rname} {disp}", force=True,
                     extra=f"复核 {len(cand)} 场特殊通关（全程 / 低人口径）")
        _jp(0, len(cand))
        for i in range(0, len(cand), 6):
            if jid:
                await _job_checkpoint(jid)
            chunk = cand[i:i + 6]
            infos = await asyncio.gather(*[_pgcr_run_info_cached(m["instance"]) for m in chunk])
            for m, info in zip(chunk, infos):
                if info:
                    m["full_run"] = info["fresh"] is not False
                    m["full_verified"] = True   # PGCR 明确回答过「是否从头开始」
                    m["private"] = info["private"]
                    # 账号数取 PGCR 全程出现过的账号 与 场上人数 的较大者：
                    # 6 人团中途退到剩 2 人通关，靠 PGCR 账号数戳穿不算双人；
                    # 老对局 PGCR 被官方裁剪只剩 1 条时，回落历史 player_count
                    m["low_accounts"] = max(info["accounts"], m["player_count"])
            _jp(min(i + 6, len(cand)), len(cand))
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
            "plays": 0, "clears": 0, "best": None, "last": "", "ffc": None,
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

    # 通关数/最快全程对齐 raid.report：官方历史裁剪掉的通关（老对局/已删角色）从这里补齐，
    # 分组用 manifest activities.json 的 hash→名（split_activity 拆难度），与历史组分并
    if rr:
        _merge_rr_stats(groups, rr["acts"])
    else:
        # 兜底口径：只认 PGCR 明确回答过「从头开始打」的通关场（fast=False 的检查点局不算）
        for m in matches:
            if (m["completed"] and m.get("full_verified") and m["full_run"]
                    and not m["private"] and m["duration"] > 0):
                g = groups.get((m["base"], m["diff"] == "大师"))
                if g and (g["ffc"] is None or m["duration"] < g["ffc"]):
                    g["ffc"] = m["duration"]
    done = [m for m in matches if m["completed"]]
    total_clears = sum(g["clears"] for g in groups.values())
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
        "total_clears": total_clears,
        "total_plays": len(matches),
        "rr_aligned": bool(rr),
        "flawless": sum(1 for m in done if _mfl(m)),
        "solo_fl": sum(1 for m in done if _mfl(m) and m["low_accounts"] == 1),
        "duo_fl": sum(1 for m in done if _mfl(m) and m["low_accounts"] <= 2),
        "trio_fl": sum(1 for m in done if _mfl(m) and 0 < m["low_accounts"] <= 3),
        "master": sum(1 for m in done if m["diff"] == "大师"),
        "matches": matches,
        "raids": std,
        "raids_master": mst,
    }


async def start_raid_report(name: str, mode: int, who: str = "") -> str | None:
    """突袭/地牢战绩走后台任务队列（翻历史+PGCR 复核耗时以分钟计，群里先回「统计中」）"""
    member = await resolve_member(name)
    if not member:
        return None
    mtype, mid = member["mtype"], member["mid"]
    key = f"{mtype}:{mid}:raidrep:{mode}"
    hit = _reuse_job(key)
    if hit:
        _mark_reused(hit, who)
        return hit
    jid = f"{mid}_rr{mode}_{len(JOBS)}"
    _register_job(key, jid, {
        "done": 0, "total": 0, "status": "queued",
        "name": f"{member['display']}#{fmt_code(member['code'])}", "result": None,
        "kind": "dungeon" if mode == 82 else "raid", "who": who or "网页",
        "ts": time.time(), "label": "地牢战绩" if mode == 82 else "突袭战绩"})
    _enqueue_job(jid, lambda: _run_raid_job(jid, member, mode))
    return jid


async def _run_raid_job(jid: str, member: dict, mode: int):
    try:
        rep = await raid_report_member(member, mode, jid=jid)
        # matches 只服务网页的逐场明细，卡片渲染用不到；别把几千场挂进 JOBS 占内存
        rep.pop("matches", None)
        JOBS[jid].update(status="done", result=rep)
    except Exception as exc:  # noqa: BLE001
        JOBS[jid].update(status="error", error=str(exc))


# /玩家 /生涯 /催化：十秒级查询也走后台任务（群里先回「统计中」，跑完自动出图）。
# 不进上面的串行重任务队列——这几类不翻 PGCR 页，跟 raid/热力图并行跑不会造成限流，
# 排在几分钟的 raid 后面反而把快查询拖成几分钟。
_PROFILE_JOB_LABEL = {"full": "玩家卡片", "career": "生涯面板", "catalysts": "异域催化"}


async def start_profile_job(name: str, kind: str, who: str = "") -> str | None:
    member = await resolve_member(name)
    if not member:
        return None
    key = f"{member['mtype']}:{member['mid']}:profjob:{kind}"
    hit = _reuse_job(key)
    if hit:
        _mark_reused(hit, who)
        return hit
    jid = f"{member['mid']}_{kind}_{len(JOBS)}"
    _register_job(key, jid, {
        "done": 0, "total": 1, "status": "running", "started": time.time(),
        "name": f"{member['display']}#{fmt_code(member['code'])}", "result": None,
        "kind": kind, "who": who or "网页",
        "ts": time.time(), "label": _PROFILE_JOB_LABEL.get(kind, kind)})
    # 这几类不翻 PGCR 页，十几秒就完事，不占并行槽位（占着会白白挡住生涯任务），
    # 但要留运行句柄，好让面板的「中止」按得动
    _JOB_TASK[jid] = asyncio.get_running_loop().create_task(
        _run_queued(jid, lambda: _run_profile_job(jid, member, kind)))
    return jid


async def _run_profile_job(jid: str, member: dict, kind: str):
    try:
        if kind == "full":
            rep = await full_report_member(member, jid=jid)
        elif kind == "career":
            rep = await career_report_member(member, jid=jid)
        elif kind == "catalysts":
            rep = await node_report_member(member, "catalysts")
        else:
            raise ValueError(f"未知的查询类型 {kind}")
        JOBS[jid].update(status="done", done=1, total=1, result=rep)
    except Exception as exc:  # noqa: BLE001
        JOBS[jid].update(status="error", error=str(exc))


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
                    f"（约 {_ev_dt(now + eta):%H:%M:%S} 完成）"
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
# 任务记录里额外挂的字段（下划线开头的属于内部状态，job_snapshot / job_view 不往外吐）：
#   _factory  可重跑入口（面板的「重跑/继续」用它；重启程序后老任务没有这个键）
#   paused    被管理员暂停（不是 status：排队中和运行中都能暂停，面板另有标记）
#   started   真正开跑的时刻；ended 收尾时刻；ts 是发起时刻
_JOB_STATUS_ENDED = ("done", "error", "aborted")


def _prune_jobs():
    """任务只增不减会一直吃内存；留最近 _JOB_KEEP 条，已结束的优先清"""
    # 去重映射指向的任务已经没了就一起清，否则这个 dict 会随「一共查过多少人」一直涨
    for k, v in list(_JOB_DEDUP.items()):
        if v not in JOBS:
            _JOB_DEDUP.pop(k, None)
    for k in list(_JOB_TASK):  # 任务表里已经没有的，把运行句柄也清掉
        if k not in JOBS:
            _JOB_TASK.pop(k, None)
    if len(JOBS) <= _JOB_KEEP:
        return
    for k in list(JOBS):
        if len(JOBS) <= _JOB_KEEP:
            break
        if JOBS[k].get("status") in _JOB_STATUS_ENDED:
            JOBS.pop(k, None)


def _job_can(j: dict) -> dict:
    """这条任务此刻能做什么（面板按它决定渲染哪几个按钮）"""
    status = j.get("status")
    paused = bool(j.get("paused"))
    if status in ("queued", "running"):
        return {"pause": not paused, "resume": paused, "abort": True, "retry": False}
    if status in _JOB_STATUS_ENDED:
        # 已结束的只剩「重跑」；重启程序后老任务没留下 _factory，重跑键就没有
        return {"pause": False, "resume": False, "abort": False,
                "retry": bool(j.get("_factory"))}
    return {"pause": False, "resume": False, "abort": False, "retry": False}


def _job_times(j: dict, now: float) -> dict:
    """把一段任务拆成三段计时：排队多久 / 跑了多久 / 从发起到现在多久

    排队中这两段会实时增长；已结束的两段就定格了（ended 收尾时打的）。
    """
    ts = j.get("ts") or 0
    started = j.get("started") or 0
    status = j.get("status") or "?"
    alive = status not in _JOB_STATUS_ENDED
    end_at = now if alive else (j.get("ended") or started or ts or now)
    return {
        "started": f"{_ev_dt(started):%H:%M:%S}" if started else "",
        "queued_s": int(max(0.0, (started or end_at) - ts)) if ts else 0,
        "run_s": int(max(0.0, end_at - started)) if started else 0,
        "total_s": int(max(0.0, end_at - ts)) if ts else 0,
    }


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
        tm = _job_times(j, now)
        out.append({"id": jid, "label": j.get("label") or "任务",
                    "kind": j.get("kind") or "", "who": j.get("who") or "网页",
                    "name": j.get("name") or "", "status": status,
                    "done": done, "total": total,
                    "queue_pos": queue_position(jid) if status == "queued" else 0,
                    "pct": int(done * 100 / total) if total else 0,
                    # 面板显示发起时刻与分段耗时（请求时间一目了然）
                    "time": f"{_ev_dt(ts):%H:%M:%S}" if ts else "",
                    "date": f"{_ev_dt(ts):%m-%d}" if ts else "",
                    "paused": bool(j.get("paused")),
                    "can": _job_can(j),
                    "error": j.get("error") or "",
                    "note": j.get("note") or "",
                    "reused": j.get("reused") or 0,
                    "reused_by": j.get("reused_by") or [],
                    "reuse_count": j.get("reused") or 0,
                    "reuse_by": list(j.get("reused_by") or []),
                    # elapsed 是「已跑」的旧名（老面板脚本还在读它兜底）
                    "elapsed": tm["run_s"] if status in ("running", "queued") else 0,
                    **tm})
    # 同一状态内按发起时间倒序（最新的排最上面），面板翻页时先看到刚发的
    out.sort(key=lambda x: (order.get(x["status"], 9), -_job_ts(x["id"])))
    return out


def job_view(jid: str) -> dict:
    """单条任务的状态视图（QQ 侧播报排队位次用）；任务不存在返回 {}"""
    for it in job_snapshot():
        if it["id"] == jid:
            return it
    return {}


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
    ts = j.get("ts") or 0
    waited = f"（排了 {_hm(time.time() - ts)}）" if ts and time.time() - ts >= 60 else ""
    slots = f" · 并行 {len(_JOB_RUNNING)}/{job_parallel()}"
    print(f"[任务] {time.strftime('%H:%M:%S')} ▶ 开始：{_job_label(jid)}"
          f"{' · ' + note if note else ''}{waited}{slots}", flush=True)
    _PROG_T0[jid + "#t0"] = time.time()
    _PROG_PCT.pop(jid, None)


def _job_end_log(jid: str, kind: str = "ok", note: str = "") -> None:
    """收尾日志；kind: ok 完成 / err 失败 / abort 中止"""
    t0 = _PROG_T0.get(jid + "#t0")
    used = f" · 总用时 {_hm(time.time() - t0)}" if t0 else ""
    mark = {"ok": "✔ 完成", "err": "✘ 失败", "abort": "⏹ 中止"}.get(kind, kind)
    print(f"[任务] {time.strftime('%H:%M:%S')} {mark}：{_job_label(jid)}{used}"
          f"{' · ' + note if note else ''}", flush=True)


# 生涯武器 / 热力图都要逐场拉 PGCR，请求量很大。原来是一条队逐个跑，一个人跑几分钟，
# 排在后面的人容易等成半小时；现在改成「并行 N 个任务」——它们共享同一个请求并发闸门
# （见 _pgcr_sem），所以并行不会让总请求量超速，只是把几个人的等待重叠起来。
_JOB_QUEUE: list[tuple] = []  # [(jid, factory), ...] 等待中（不含正在跑的）
_JOB_RUNNING: set[str] = set()
_JOB_TASK: dict[str, "asyncio.Task"] = {}   # 运行中的 asyncio 任务（中止要取消它）
_JOB_PARALLEL_DEFAULT = 2
_JOB_PARALLEL_MAX = 4


def job_parallel() -> int:
    """并行槽位：bot_config.json 的 job_parallel（面板「参数设置 → 并行任务数」）

    未设置/非法 = 内置默认 2；1 = 回到原来的「一个个跑」。上限 4：再多也只是互相抢
    同一个并发预算，单个任务反而变慢。
    """
    try:
        n = int(bot_runtime.load_config().get("job_parallel") or 0)
    except (TypeError, ValueError) as exc:
        print(f"[任务] job_parallel 配置值不合法（按默认 {_JOB_PARALLEL_DEFAULT} 处理）：{exc}")
        n = 0
    if n <= 0:
        return _JOB_PARALLEL_DEFAULT
    return max(1, min(n, _JOB_PARALLEL_MAX))


def set_job_parallel(n: int) -> int:
    def _set(cfg):
        cfg["job_parallel"] = max(0, min(int(n), _JOB_PARALLEL_MAX))
    bot_runtime.update_config(_set)
    _pump_jobs()  # 调大就立刻把队列里的任务提上来跑
    return job_parallel()


def queue_position(jid: str) -> int:
    """0 = 正在跑 / 已结束；>0 = 在等待队列里的位次（1 表示下一个就轮到）"""
    if jid in _JOB_RUNNING:
        return 0
    for i, (qid, _) in enumerate(_JOB_QUEUE):
        if qid == jid:
            return i + 1
    return 0


def _loop_or_none():
    """当前事件循环；没有（理论上不该发生）就返回 None，让调用方别硬起任务"""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _pump_jobs():
    """有空槽就把队列里最靠前、且没被暂停的任务取出来跑"""
    if not _JOB_QUEUE:
        return
    loop = _loop_or_none()
    if loop is None:
        print("[任务] 没有事件循环，任务留在队列里等下一次调度")
        return
    while _JOB_QUEUE and len(_JOB_RUNNING) < job_parallel():
        idx = next((i for i, (qid, _) in enumerate(_JOB_QUEUE)
                    if not (JOBS.get(qid) or {}).get("paused")), None)
        if idx is None:  # 队列里的都被暂停了，谁恢复谁来叫醒队列
            return
        jid, factory = _JOB_QUEUE.pop(idx)
        j = JOBS.get(jid)
        if not j:  # 排队期间被清理掉了
            continue
        j.update(status="running", started=time.time())
        _JOB_RUNNING.add(jid)
        _job_start_log(jid)
        _JOB_TASK[jid] = loop.create_task(_run_queued(jid, factory))


async def _run_queued(jid: str, factory):
    """跑一个任务；无论怎么结束都要把状态、耗时、槽位收干净，否则队列会卡死"""
    j = JOBS.get(jid)
    if j is None:
        return
    try:
        await factory()
    except asyncio.CancelledError:
        # 维护触发的取消要跟「管理员中止」分开记：文案不一样，用户/管理员才知道该
        # 等维护结束而不是去查是谁点了按钮（abort_all_jobs 会打 _maint_abort）
        if j.get("_maint_abort") or bst.is_down():
            _mark_aborted(jid, "Bungie 服务器维护中，已自动中止（维护结束后重发即可）")
        else:
            _mark_aborted(jid, "被管理员中止")
        _flush_job_caches()  # 已经拉到的对局明细别白拉，重跑时直接复用
        raise                # 取消要如实往外传，不能当成正常结束
    except bst.BungieMaintenanceError as exc:
        # 维护是「暂时做不了」不是「做错了」：按中止处理（面板上区分于失败），
        # 用户重发即可；已拉到一半的明细仍然落盘复用
        _mark_aborted(jid, f"Bungie 服务器维护中，已自动中止：{exc}")
        _flush_job_caches()
    except Exception as exc:  # noqa: BLE001  兜底：别让队列卡死
        j.update(status="error", error=str(exc))
        _job_end_log(jid, kind="err", note=str(exc))
    else:
        if j.get("status") in ("running", "queued"):
            # 工厂函数正常返回却没写明结局：按完成处理。不补这一手的话任务会一直
            # 挂在「运行中」，白占一个并行槽位，面板上的进度条也永远不动。
            j.update(status="done", done=max(1, j.get("done") or 0),
                     total=max(1, j.get("total") or 0))
        st = j.get("status")
        _job_end_log(jid, kind="abort" if st == "aborted"
                     else "err" if st == "error" else "ok")
    finally:
        j["ended"] = time.time()  # 复用窗口与面板用时都从这个时刻算起
        j["paused"] = False
        _JOB_RUNNING.discard(jid)
        _JOB_TASK.pop(jid, None)
        _pump_jobs()


def _enqueue_job(jid: str, factory):
    JOBS.setdefault(jid, {})["_factory"] = factory
    _JOB_QUEUE.append((jid, factory))
    _pump_jobs()


def _flush_job_caches() -> None:
    """把已经拉到一半的中间结果落盘。

    任务被中止时进程内存里的对局明细说没就没，落一次盘就能让重跑直接复用。
    只写已经加载过的缓存：没加载过的还是空的，写下去等于把盘上的数据抹了。
    """
    if _pvp_cache_ready:
        _save_pvp_cache()
    if _agg_cache_ready:
        _save_agg_cache()


def _mark_aborted(jid: str, note: str = "") -> None:
    j = JOBS.get(jid)
    if not j:
        return
    j.update(status="aborted", paused=False, error=note or "已中止")
    _job_end_log(jid, kind="abort", note=note)


def _cancel_task(task) -> None:
    """在任务自己的事件循环上取消它：面板与任务可能分属两条循环（webui / QQ bot），
    直接 task.cancel() 跨线程调 call_soon 不是线程安全的，轻则取消不掉。"""
    try:
        loop = task.get_loop()
    except AttributeError:  # 老 Python 没有 get_loop
        loop = None
    if loop is None or not loop.is_running():
        return
    loop.call_soon_threadsafe(task.cancel)


def abort_all_jobs(note: str) -> int:
    """中止所有在跑/在排队的后台任务（维护时自动调用），返回中止条数。

    运行中的任务走取消（检查点也会抛，双保险），排队中的直接从队列摘掉——
    否则维护期排队的任务会一个接一个跑起来、又一个个失败，把面板刷满错误。
    """
    n = 0
    for jid, _factory in list(_JOB_QUEUE):
        _JOB_QUEUE[:] = [(q, f) for q, f in _JOB_QUEUE if q != jid]
        _mark_aborted(jid, note)
        n += 1
    for jid in list(_JOB_RUNNING):
        j = JOBS.get(jid)
        if not j:
            continue
        j["paused"] = False          # 卡在暂停检查点上睡着的，先放掉才好取消
        j["_maint_abort"] = True     # 让 _run_queued 心里有数（错误文案用维护口径）
        task = _JOB_TASK.get(jid)
        if task is not None:
            _cancel_task(task)
        _mark_aborted(jid, note)
        n += 1
    return n


async def _job_checkpoint(jid: str) -> None:
    """任务循环里的检查点：被暂停就在这里停下，等管理员「继续」

    刻意用轮询而不是 asyncio.Event：任务可能跑在 webui 的事件循环上，而暂停来自
    QQ bot 那条循环（或反过来），跨线程 set() 不是线程安全的，轮询最稳。

    维护检查也放这里：长任务（翻几万场对局）每翻一页就有一次机会就地停下，
    不必等下一次请求才发现官方在维护、更不会拿维护期的空数据把卡片出完。
    """
    bst.guard_sync()
    if not (JOBS.get(jid) or {}).get("paused"):
        return
    print(f"[任务] {time.strftime('%H:%M:%S')} ⏸ 暂停：{_job_label(jid)}", flush=True)
    while (JOBS.get(jid) or {}).get("paused"):
        await asyncio.sleep(0.5)  # 被中止时这里会抛 CancelledError，正好就地退出
    print(f"[任务] {time.strftime('%H:%M:%S')} ▶ 继续：{_job_label(jid)}", flush=True)


def job_control(jid: str, action: str) -> dict:
    """逐条任务控制（面板按钮）：abort 中止 / pause 暂停 / resume 继续 / retry 重跑"""
    j = JOBS.get(jid)
    if not j:
        return {"ok": False, "msg": "这条任务已经不在了（可能已被清理），重发一次指令即可", "status": ""}
    status = j.get("status") or ""
    if action == "abort":
        if status == "queued":
            _JOB_QUEUE[:] = [(q, f) for q, f in _JOB_QUEUE if q != jid]
            _mark_aborted(jid, "排队中被管理员中止")
            return {"ok": True, "msg": "已从队列里移除", "status": "aborted"}
        if status == "running":
            j["paused"] = False  # 先把暂停放掉，否则它正卡在检查点上睡着
            task = _JOB_TASK.get(jid)
            if not task or task.done():
                _mark_aborted(jid, "被管理员中止")
                return {"ok": True, "msg": "已中止", "status": "aborted"}
            _cancel_task(task)
            return {"ok": True, "msg": "已中止（正在收尾，已拉到的对局明细会保留）",
                    "status": "running"}
        return {"ok": False, "msg": "这条任务已经结束了", "status": status}
    if action == "pause":
        if status not in ("queued", "running"):
            return {"ok": False, "msg": "这条任务已经结束了", "status": status}
        if j.get("paused"):
            return {"ok": True, "msg": "本来就在暂停中", "status": status}
        j["paused"] = True
        print(f"[任务] {time.strftime('%H:%M:%S')} ⏸ 管理员暂停：{_job_label(jid)}", flush=True)
        return {"ok": True,
                "msg": "已暂停：排队中的不会被提起，运行中的在下一个检查点停下",
                "status": status}
    if action == "resume":
        if status in ("queued", "running"):
            if not j.get("paused"):
                return {"ok": True, "msg": "这条任务本来就在跑", "status": status}
            j["paused"] = False
            _pump_jobs()  # 排队中被暂停的，恢复后要让队列重新挑它
            return {"ok": True, "msg": "已恢复", "status": status}
        return job_control(jid, "retry")  # 已结束的「继续」就是重跑
    if action == "retry":
        factory = j.get("_factory")
        if not factory:
            return {"ok": False, "msg": "这条任务没有留下可重跑的入口（重启程序后老任务会这样），"
                                        "重发一次指令即可", "status": status}
        if status in ("queued", "running"):
            return {"ok": False, "msg": "这条任务还在跑，先中止再重跑", "status": status}
        if any(q == jid for q, _ in _JOB_QUEUE):
            return {"ok": True, "msg": "已经在队列里了", "status": "queued"}
        _prune_jobs()
        j.update(status="queued", done=0, total=0, result=None, error="", note="",
                 paused=False, ts=time.time(), started=0.0, ended=0.0)
        j.pop("cached", None)   # 上一轮的「命中缓存/被复用」标记不能留给这一轮
        j.pop("reused", None)
        j["reused_by"] = []
        _JOB_QUEUE.append((jid, factory))
        _pump_jobs()
        return {"ok": True, "msg": "已重新排队", "status": "queued"}
    return {"ok": False, "msg": f"不认识的操作 {action}", "status": status}


def _on_maintenance(detail: str) -> None:
    """维护点亮时的自动处理（bungie_status 回调）：清响应缓存 + 中止所有后台任务 + 面板提醒

    响应缓存必须清：维护期的脏响应（200 + 错误码/空数据）可能已经进去了，
    官方恢复后还会按 TTL（最长 6 小时）继续吐给用户。
    """
    n = len(_RESP)
    _RESP.clear()
    stopped = abort_all_jobs("Bungie 服务器维护中，已自动中止（维护结束后重发即可）")
    print(f"[维护] 已清空 {n} 条响应缓存、自动中止 {stopped} 个后台任务", flush=True)
    try:
        import bot_log
        bot_log.add("out", nickname="Bungie 状态",
                    text=f"[系统] ⛔ 检测到 Bungie 服务器维护（{detail or '官方未给出原因'}）："
                         f"已自动中止 {stopped} 个查询任务并暂停接单，维护结束后会自动恢复。")
    except Exception:  # noqa: BLE001 面板日志写不进去不影响拦截
        pass


bst.on_trip(_on_maintenance)


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
PVP_MATCH_CAP = 2000      # 逐场拉 PGCR 的默认上限，防止十年老号把任务拖成几十分钟
# PVE 场次比 PVP 多一个数量级（一个赛季通常几百场），默认只统计单赛季，全生涯才可能吃满上限
PVE_MATCH_CAP = 3000
# 探索/巡逻（mode 6）没有实质击杀，实测还偶发没有武器明细，统计里排除（PVE 通关率也已排除它）
_PVE_SKIP_MODES = frozenset({6})

# 逐场拉 PGCR 的并发闸门：并行的几个任务共用一条，总请求量就等于「并发上限」设置，
# 不会因为多开任务而超速。按事件循环分开存——webui 与 QQ bot 各有一条循环，
# asyncio 的信号量跨循环用会报「bound to a different event loop」。
_PGCR_SEMS: dict[int, tuple] = {}   # id(loop) → (loop, Semaphore, 容量)


def _pgcr_sem() -> "asyncio.Semaphore":
    loop = asyncio.get_running_loop()
    n = bot_runtime.concurrency_limit(_PVP_CONCURRENCY)
    ent = _PGCR_SEMS.get(id(loop))
    if ent is None or ent[2] != n:
        ent = (loop, asyncio.Semaphore(n), n)  # 连 loop 一起存住，免得 id() 被回收后复用
        _PGCR_SEMS[id(loop)] = ent
        if len(_PGCR_SEMS) > 4:
            for k, (lp, _, _) in list(_PGCR_SEMS.items()):
                if lp is not loop and not lp.is_running():
                    _PGCR_SEMS.pop(k, None)
    return ent[1]


def match_cap(kind: str) -> int:
    """生涯统计场次上限：bot_config.json 的 pvp_match_cap / pve_match_cap（运行状态页可调）。

    未设置 = 内置默认（PVP 2000 / PVE 3000）；0 = 无限制全生涯——受 Bungie 接口本身
    每角色 60 页 × 250 场的可读历史硬顶约束（更早的对局接口根本不给）。
    """
    default = PVP_MATCH_CAP if kind == "pvp" else PVE_MATCH_CAP
    cfg = bot_runtime.load_config()
    key = f"{kind}_match_cap"
    if key not in cfg:
        return default
    try:
        n = int(cfg.get(key) or 0)
    except Exception:  # noqa: BLE001
        return default
    return n if n > 0 else 0    # 0 = 无限制（_collect_matches 对 cap<=0 按无限处理）


RECENT_DEFAULT = 100      # 近期战绩 / 模式细分窗口：跨角色合并后最近多少场
_HISTORY_PAGE = 250       # Bungie 对局历史单页上限（实测超过 250 也只给 250）
_HISTORY_WAVE = 4         # 同一角色一次并发补几页
_HISTORY_CONCURRENCY = 6  # 跨角色并发上限（历史页比 PGCR 轻，但别一次怼 25/s 限流）


def recent_count(kind: str) -> int:
    """近期战绩窗口局数：bot_config.json 的 pvp_recent_count / pve_recent_count /
    gambit_recent_count（运行状态页可调）。

    未设置/非法 = 内置默认 100。窗口是**跨角色合并后**的最近 N 局（不是"每角色 N 局"），
    只影响「近期战绩」与「模式细分」两块——顶部生涯统计走全生涯聚合（见 mode_report 的 career）。"""
    cfg = bot_runtime.load_config()
    try:
        n = int(cfg.get(f"{kind}_recent_count") or 0)
    except Exception:  # noqa: BLE001
        return RECENT_DEFAULT
    return n if n > 0 else RECENT_DEFAULT


GRID_DEFAULT = {"pvp": 0, "gambit": 100}   # 胜点图默认场数：0 = 不画（PvP 那一片格子太吵）


def grid_count(kind: str) -> int:
    """胜点图（红绿方块）画多少场：bot_config.json 的 pvp_grid_count / gambit_grid_count。

    kind 为 pvp / gambit；未设置 = 内置默认（智谋 100 场、PvP 不画）。0 = 不画。
    只影响卡片上那一片方格，不改变任何统计口径。"""
    default = GRID_DEFAULT.get(kind, 0)
    cfg = bot_runtime.load_config()
    key = f"{kind}_grid_count"
    if key not in cfg:
        return default
    try:
        return max(0, int(cfg.get(key) or 0))
    except Exception:  # noqa: BLE001
        return default


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
        dump_json(_writable_path(_PVP_CACHE_FILE), _PVP_MATCH_CACHE, separators=(",", ":"))
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
    # 维护窗口内算出来的生涯武器聚合不可信（对局历史缺场 → 击杀/KD 全都偏小）
    bad = [k for k, v in _AGG_CACHE.items()
           if isinstance(v, dict) and bst.suspect_stamp(v.get("updated"))]
    for k in bad:
        _AGG_CACHE.pop(k, None)
    if bad:
        print(f"[维护] weapon_agg_cache 丢弃 {len(bad)} 条维护窗口内的缓存，下次查询重算",
              flush=True)


def _save_agg_cache():
    try:
        dump_json(_writable_path(_AGG_CACHE_FILE), _AGG_CACHE, separators=(",", ":"))
    except Exception:  # noqa: BLE001 写不进去就算了，只是下次重算
        pass


def bind_path() -> str:
    """绑定文件位置（和缓存一样放 exe / 项目同目录，程序与面板读写同一份）"""
    return _writable_path("user_bindings.json")


def bind_meta_path() -> str:
    """绑定元数据：uid → {mid, mtype, name, checked}。绑定表值只存「名#编号」，
    改名同步得靠 membershipId 才能对回同一个人，meta 与绑定表同目录同生死。"""
    return _writable_path("user_bindings_meta.json")


def load_binding_meta() -> dict:
    try:
        d = json.load(open(bind_meta_path(), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[bind] {bind_meta_path()} 读取失败：{type(exc).__name__}: {exc}")
        return {}


def save_binding_meta(meta: dict):
    try:
        dump_json(bind_meta_path(), meta, indent=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[bind] {bind_meta_path()} 写盘失败：{type(exc).__name__}: {exc}")


async def sync_bindings() -> dict:
    """绑定改名同步：核对绑定表里每个玩家在棒鸡侧的现名，改了就写回绑定表。

    有 meta（绑定时存了 membershipId）的走 GetMembershipsById 对现名，改名照跟；
    老绑定没有 meta 的先 SearchDestinyPlayerByBungieName 补种子——精确搜索本身
    就是「核验这个 ID 在棒鸡侧存在」；搜不到说明已改名（或不存在），没有
    membershipId 无从对回，只能留人工核实。返回给调度器打日志。
    """
    out: dict = {"updated": [], "seeded": 0, "stale": [], "errors": 0}
    if bst.is_down():
        # 维护期核对必然全失败：既刷一屏 JSONDecodeError/限流日志，也可能把
        # 「问不到」误判成「改名了/空号」，不如整轮跳过（下个 tick 再来）
        print("[bind] Bungie 服务器维护中，跳过本轮绑定改名核对")
        return out
    try:
        with open(bind_path(), encoding="utf-8") as f:
            binds = json.load(f)
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(binds, dict) or not binds:
        return out
    meta = load_binding_meta()
    dirty_meta = False
    for uid, name in list(binds.items()):
        if not isinstance(name, str) or "#" not in name:
            continue
        me = meta.get(uid) or {}
        cur = None
        if me.get("mid"):
            try:
                # 同 1777：路径必须带 /Platform，否则 404 HTML 让 R.json() 抛
                # JSONDecodeError，改名核对每天都在这里全军覆没
                r = await client().get(
                    f"/Platform/User/GetMembershipsById/{me['mid']}/{me.get('mtype', -1)}/")
                mems = ((r.json().get("Response") or {}).get("destinyMemberships") or [])
                if mems:
                    cur = (mems[0].get("bungieGlobalDisplayName") or "",
                           int(mems[0].get("bungieGlobalDisplayNameCode") or -1))
            except Exception as exc:  # noqa: BLE001  单个失败不挡其余
                out["errors"] += 1
                print(f"[bind] 改名核对失败（{name}）：{type(exc).__name__}: {exc}")
        else:
            try:
                m = await resolve_member(name)
            except Exception:  # noqa: BLE001
                m = None
            if m:
                meta[uid] = {"mid": str(m["mid"]), "mtype": m["mtype"],
                             "name": name, "checked": time.time()}
                dirty_meta = True
                out["seeded"] += 1
            else:
                out["stale"].append((uid, name))
                continue
        if cur and cur[0]:
            new = f"{cur[0]}#{fmt_code(cur[1])}"
            if new != name:
                binds[uid] = new
                me = meta.setdefault(uid, {})
                me.update({"name": new, "checked": time.time()})
                dirty_meta = True
                out["updated"].append((uid, name, new))
    if out["updated"]:
        try:
            dump_json(bind_path(), binds, indent=1)
        except Exception as exc:  # noqa: BLE001
            print(f"[bind] 同步结果写回失败：{type(exc).__name__}: {exc}")
    if dirty_meta:
        save_binding_meta(meta)
    return out


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
                           on_page=None, check=None) -> list[dict]:
    """收集对局（跨角色去重，新→旧）；since/until 为空串表示不限时间

    mode: 5=所有PVP 7=所有PVE；skip_modes 里的具体玩法会被丢掉
    on_page: 每翻完一页回调一次 on_page(已翻页数, 已收集场次)，用于打进度日志
    check: 每翻一页前 await 一次的钩子（后台任务用它响应暂停/中止）
    """
    seen, matches = set(), []
    for ci, cid in enumerate(chars):
        page = 0
        while page < 60 and (cap <= 0 or len(matches) < cap):  # 60页×250 ≈ 1.5万场/角色的接口硬顶
            if check:
                await check()
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
                    if cap > 0 and len(matches) >= cap:
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
    return await _start_weapon_job(name, scope, "pvp", 5, match_cap("pvp"), frozenset(), who)


async def start_pve_weapons(name: str, scope: str = "current", who: str = "") -> str | None:
    """PVE 生涯武器后台任务；scope 默认 'current'=当前赛季（全生涯 PVE 场次太多，要显式指定）"""
    return await _start_weapon_job(name, scope, "pve", 7, match_cap("pve"), _PVE_SKIP_MODES, who)


def _day_shift(day: str, delta: int) -> str:
    """日期串前后挪几天（时间窗都按 'YYYY-MM-DD' 字符串比大小）"""
    try:
        return (datetime.date.fromisoformat(day[:10])
                + datetime.timedelta(days=delta)).isoformat()
    except (ValueError, TypeError):
        return day


def _sub_agg_segments(mid: str, kind: str, since: str, until: str,
                      skip: str) -> list[dict]:
    """同一玩家同一类统计里，已经被完整缓存、且整段落在本次范围里的其它范围。

    典型场景：先查了某个赛季，再查全生涯——那段赛季就是这里的一「段」，直接折进来，
    不必再把那段时间的活动历史重新翻一遍（逐场明细本来就有 _PVP_MATCH_CACHE 兜着）。

    只挑「已经结束、且没被场次上限截断」的段：还在进行的段每天都有新对局，折进来会
    漏掉那次查询之后打的场次；截断过的段数据不全，折进来会少算。全生涯那种开区间的
    段（没有结束日期）也跳过——它自己就是超集，不可能是别人的子段。
    """
    today = _cn_now().date().isoformat()     # 全盘时钟口径：「段结束了没」也按北京时间判
    out = []
    for k, v in _AGG_CACHE.items():
        if k == skip or not v.get("weapons") or v.get("capped"):
            continue
        if "cap" not in v:   # 2026-10-07 之前写的段没记上限，判断不了是否完整，不当子段用
            continue
        parts = k.split("|", 2)
        if len(parts) != 3 or parts[0] != mid or parts[1] != kind:
            continue
        s, u = v.get("scope_since") or "", v.get("scope_until") or ""
        if not s or not u or u >= today:
            continue
        if (since and s < since) or (until and u > until):
            continue
        out.append(v)
    out.sort(key=lambda v: v["scope_since"], reverse=True)  # 新的在前，配合「取最近 N 场」
    return out


def _fold_segments(agg: dict, tot: dict, segs: list[dict]) -> int:
    """把缓存段的排名/计数折进本次统计；返回折进来的场次数"""
    n = 0
    for seg in segs:
        for h, w in (seg.get("weapons") or {}).items():
            a = agg.setdefault(h, {"name": w.get("name", "未知武器"), "icon": w.get("icon", ""),
                                   "type": w.get("type", ""), "kills": 0, "precision": 0,
                                   "matches": 0})
            for k in ("kills", "precision", "matches"):
                a[k] += int(w.get(k) or 0)
        for k in tot:
            tot[k] += int((seg.get("tot") or {}).get(k, 0) or 0)
        n += int(seg.get("matches") or 0)
    return n


def _gap_windows(since: str, until: str, segs: list[dict]) -> list[tuple[str, str]]:
    """本次范围里没被这些缓存段盖住的时间段（闭区间，按日期串比较；'' = 不限）

    逐场统计按「对局日期落在哪天」归属，所以相邻两段必须错开一天：
    折进来的段占了 [s, u]，空档就只能是 [.., s-1] 与 [u+1, ..]，否则边界那天会算两遍。
    """
    gaps: list[tuple[str, str]] = [(since or "", until or "")]
    for seg in sorted(segs, key=lambda v: v["scope_since"]):
        s, u = seg["scope_since"], seg["scope_until"]
        nxt: list[tuple[str, str]] = []
        for a, b in gaps:
            if (b and s > b) or (a and u < a):   # 这一段跟本窗口不沾边
                nxt.append((a, b))
                continue
            if not a or a < s:
                nxt.append((a, _day_shift(s, -1)))
            if not b or u < b:
                nxt.append((_day_shift(u, 1), b))
        gaps = nxt
    out = [(a, b) for a, b in gaps if not (a and b and a > b)]
    out.sort(key=lambda w: w[0], reverse=True)   # 新的在前：场次上限要先满足最近的
    return out


def _dedup_matches(matches: list[dict]) -> list[dict]:
    """多段收集来的对局按 instance 去重后合起来（新→旧）"""
    seen, out = set(), []
    for m in matches:
        if m["instance"] and m["instance"] in seen:
            continue
        seen.add(m["instance"])
        out.append(m)
    out.sort(key=lambda m: m["period"], reverse=True)
    return out


async def _run_weapon_job(jid: str, mtype: int, mid: str, chars: list[str],
                          since: str, until: str, scope: str, label: str,
                          kind: str, mode: int, cap: int, skip_modes: frozenset):
    """跑生涯武器统计；两级缓存——同范围增量补拉，别的范围整段折进来

    ① 同一玩家同一范围再查：读回上次的排名与覆盖日期，只统计比它更新的对局。
    ② 先查过某个赛季、现在要查全生涯：那个赛季整段落在本次范围里，直接折进来，
       连那段时间的活动历史都不用重翻（逐场 PGCR 明细本来也走 _PVP_MATCH_CACHE）。
    ③ 老汇总的覆盖范围不可信时不拿它当基准，整段重算（见下面的 stale 判断）。
    """
    _load_agg_cache()
    key = f"{mid}|{kind}|{scope}"
    base = _AGG_CACHE.get(key)
    same_scope = bool(base and base.get("weapons")
                      and base.get("scope_since", "") == since
                      and base.get("scope_until", "") == until)
    if same_scope:
        # 这份老汇总可不可信？
        #   · 当时的场次上限比现在小 → 它是被截断出来的，最早日期是假边界；而且截断是按
        #     角色顺序停的，中间那段（后两个角色）也可能整块没统计到。
        #   · 2026-10-07 之前写的缓存没记 cap/capped，判断不了完整性，按不可信算。
        # 不可信就丢掉它整段重算：逐场明细有 _PVP_MATCH_CACHE 兜底，重算主要是重新翻一遍
        # 活动历史，代价很小；不重算的话，上限调大了也永远补不回那几年的对局。
        prev_cap, prev_capped = base.get("cap"), base.get("capped")
        if (prev_cap is None or prev_capped is None
                or (bool(prev_capped) and (cap <= 0 or cap > int(prev_cap)))):
            log_stage(f"{jid}#collect",
                      f"{_job_label(jid)}：老汇总（{base.get('matches', 0)} 场，最早 "
                      f"{base.get('oldest') or '—'}）的场次上限是 "
                      f"{'未记录' if prev_cap is None else prev_cap}，这次按当前上限整段重算")
            JOBS[jid]["note"] = ("老汇总只覆盖到 "
                                 f"{base.get('oldest') or '—'}（当时受场次上限截断），"
                                 "本次整段重算")
            base = None
            same_scope = False
    agg: dict[str, dict] = {}
    tot = {"kills": 0, "precision": 0, "melee": 0, "grenade": 0, "super": 0, "ability": 0}
    base_matches = base_missed = 0
    base_oldest = base_newest_full = ""
    segs: list[dict] = []
    if same_scope:
        agg = {h: dict(v) for h, v in (base.get("weapons") or {}).items()}
        for k in tot:
            tot[k] = int((base.get("tot") or {}).get(k, 0) or 0)
        base_matches = int(base.get("matches", 0) or 0)
        base_missed = int(base.get("missed", 0) or 0)
        base_oldest = base.get("oldest", "") or ""
        base_newest_full = base.get("newest_full", "") or ""
        gaps = [(base_newest_full[:10] or since, until)]
    else:
        segs = _sub_agg_segments(mid, kind, since, until, skip=key)
        base_matches = _fold_segments(agg, tot, segs)
        base_missed = sum(int(s.get("missed") or 0) for s in segs)
        olds = [s.get("oldest") or "" for s in segs if s.get("oldest")]
        news = [s.get("newest_full") or "" for s in segs if s.get("newest_full")]
        base_oldest = min(olds) if olds else ""
        base_newest_full = max(news) if news else ""
        gaps = _gap_windows(since, until, segs)
        if segs:
            span = "、".join(s.get("scope_label") or s.get("scope_since", "")
                             for s in segs)
            JOBS[jid]["note"] = f"复用已缓存范围：{span}"
            log_stage(f"{jid}#collect",
                      f"{_job_label(jid)}：折入 {len(segs)} 段已缓存范围（{span}），"
                      f"共 {base_matches} 场，只补拉剩下 {len(gaps)} 段")
    matches: list[dict] = []
    folded_n = 0 if same_scope else base_matches
    try:
        def _on_page(ci, cn, page, n):
            log_progress(f"{jid}#collect", page, 0,
                         label=f"{_job_label(jid)} · 翻取对局历史",
                         extra=f"角色 {ci}/{cn} · 第 {page} 页 · 本次已收 {len(matches) + n} 场",
                         min_gap=1.5, min_pct=0)

        log_stage(f"{jid}#collect", f"{_job_label(jid)}：开始翻取对局历史…")
        for gi, (gs, gu) in enumerate(gaps):
            await _job_checkpoint(jid)
            remain = cap - folded_n - len(matches) if cap > 0 else 0
            if cap > 0 and remain <= 0:
                break  # 场次上限吃满，更早的空档不用再翻
            span_txt = f"{gs or '最早'} ~ {gu or '现在'}"
            log_progress(f"{jid}#collect", 0, 0, label=f"{_job_label(jid)} · 翻取对局历史",
                         extra=f"范围 {span_txt}", force=True, min_pct=0)
            part = await _collect_matches(mtype, mid, chars, mode, gs, gu, remain,
                                          skip_modes, on_page=_on_page,
                                          check=lambda: _job_checkpoint(jid))
            if gi == 0 and same_scope and base_newest_full:
                # 边界那天会重复枚举，按完整时间戳只留更新的（只对"更新"那段成立：
                # 往前补的那段本来就早于 base_oldest，套上这个过滤会把它整段丢掉）
                part = [m for m in part if m["period"] > base_newest_full]
            matches += part
        matches = _dedup_matches(matches)
        if not matches:  # 没有新对局：有缓存就直接返回上次排名，否则返回空态
            log_stage(f"{jid}#collect", f"{_job_label(jid)}：没有新对局，直接出图")
        JOBS[jid].update(total=max(1, len(matches)), done=0 if matches else 1)
        if matches:
            log_progress(jid, 0, len(matches), label=_job_label(jid), force=True,
                         extra="开始逐场拉取对局明细")
        missed = 0
        sem = _pgcr_sem()
        lock = asyncio.Lock()

        async def one(m: dict):
            nonlocal missed
            await _job_checkpoint(jid)   # 暂停就在新对局之前停下，已发出去的收完为止
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

        if matches:
            await asyncio.gather(*(one(m) for m in matches))
        save_seen_players()
        _save_pvp_cache()
        weapons = sorted((v for v in agg.values() if v["kills"] > 0), key=lambda x: -x["kills"])
        total_matches = base_matches + len(matches)
        dates = [m["period"][:10] for m in matches]
        newest = max(dates + ([base_newest_full[:10]] if base_newest_full else []), default="")
        pool = [d for d in dates if d] + ([base_oldest] if base_oldest else [])
        oldest = min(pool) if pool else ""
        newest_full = max([m["period"] for m in matches]
                          + ([base_newest_full] if base_newest_full else []), default="")
        result = {
            "display": JOBS[jid]["name"], "scope": scope, "scope_label": label, "kind": kind,
            "matches": total_matches, "missed": base_missed + missed,
            "capped": cap > 0 and total_matches >= cap, "cap": cap,
            "range": (oldest, newest),
            "added": len(matches), "cached": base_matches,
            "note": JOBS[jid].get("note") or "",
            "weapons": weapons,
            # 卡片只展示前 60 把，汇总块要用全量，所以单独给两个总数
            "weapon_kills": sum(w["kills"] for w in weapons),
            "weapon_precision": sum(w["precision"] for w in weapons),
            **tot}
        JOBS[jid].update(status="done", total=max(1, JOBS[jid].get("total") or 1),
                         done=max(1, JOBS[jid].get("total") or 1), result=result)
        _AGG_CACHE[key] = {
            "scope_since": since, "scope_until": until, "mid": mid, "kind": kind,
            "weapons": agg, "tot": tot, "matches": total_matches,
            "missed": base_missed + missed, "oldest": oldest,
            "newest": newest, "newest_full": newest_full,
            "cap": cap,   # 记下当时的场次上限：调大之后这份汇总的头就是假边界，要往前补
            "capped": cap > 0 and total_matches >= cap,
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
                extra=f"角色 {ci}/{cn} · 第 {page} 页 · 已收集 {n} 场", min_gap=1.5, min_pct=0),
            check=lambda: _job_checkpoint(jid))
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


def _cata_progress(st: dict) -> tuple[int, int]:
    """催化记录的进度 → (已达成, 目标值)。

    催化 record 常带多个目标（如 警惕羽翼 = 2/2 引入 + 0/250 击杀 + 0/5 场次），
    第一个目标早满了催化仍没拿到，直接用 _obj_progress 会显示成误导性的 2/2。
    这里取需求量最大的那个目标当主进度——那就是玩家还在磨的部分。
    """
    best = (0, 0)
    for o in st.get("objectives") or []:
        cv, pg = o.get("completionValue") or 0, o.get("progress") or 0
        if cv > best[1]:
            best = (min(pg, cv), cv)
    return best


async def _applied_catalyst_plugs(member: dict) -> dict[str, tuple[str, str]]:
    """可锻造异域"已装了哪个催化"：找到这把枪的实例，看武器插槽（组件 304）里
    plugHash 落在候选催化表里的那一个。返回 {武器itemHash: (催化名, 催化图标)}。

    库存/插槽是隐私组件：不带 ReadBasicUserData scope 的授权读不到（响应里直接
    没有 sockets），所以这里一次 Profile 级调用（102 档案仓 + 201 角色背包 + 304
    全部插槽）搞定，读不到就返回空——卡上只显示锻造标，不显示已装。
    注意：旧授权 token 没带 scope，需要在面板重新授权一次才会亮"已装"。
    """
    if not _CATA_OPTIONS:
        return {}
    try:
        import bungie_auth
        if not bungie_auth.authorized():
            return {}
        if str(bungie_auth.status().get("membership_id") or "") != str(member["mid"]):
            return {}  # 授权的是别人：没有权限读这个玩家的库存
        base = f"/Platform/Destiny2/{member['mtype']}/Profile/{member['mid']}/"
        inv = await bungie_auth.authorized_get(base, {"components": "102,201,304"})
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[str, str]] = {}
    try:
        sockets_all = ((inv.get("sockets") or {}).get("data")) or {}
        if not sockets_all:
            return out  # 没权限/隐私设置看不到插槽：安静降级
        by_iids: dict[str, list[str]] = {}
        items = list((((inv.get("profileInventory") or {}).get("data")) or {}).get("items") or [])
        for ch in ((inv.get("characterInventory") or {}).get("data") or {}).values():
            items += (ch or {}).get("items") or []
        for it in items:
            h = str(it.get("itemHash") or "")
            iid = it.get("itemInstanceId") or ""
            if h in _CATA_OPTIONS and iid:
                by_iids.setdefault(h, []).append(iid)  # 同一把枪可能有塑形/掉落两份
        for h, iids in by_iids.items():
            for iid in iids:
                for s in (sockets_all.get(iid, {}) or {}).get("sockets") or []:
                    ph = str(s.get("plugHash") or "")
                    if ph in _CATA_OPTIONS[h]:
                        out[h] = _CATA_OPTIONS[h][ph]
                        break
                if h in out:
                    break
    except Exception:  # noqa: BLE001
        pass
    return out


# 催化 record 名 ≠ 武器名（简中译名历史原因），换武器图标时按表纠正：
# 记录名 → weapons.json 里的武器名（就这 9 把特例，其余记录名剥掉"催化"后缀即武器名）
_CATA_WEAPON_ALIAS = {
    "赫沃斯托夫": "赫沃斯托夫7G-0X",
    "焚天者": "焚天者誓约",
    "普罗米修斯": "普罗米修斯透镜",
    "爱莲娜的誓言": "爱莲娜之誓",
    "不动改装": "维王者之剑",
    "阿克瑞斯": "阿克瑞斯传说",
    "D.A.R.C.I.": "D.A.R.C.I",
    "低语": "蠕虫低语",
    "雷霆领主": "雷神",
}

# 武器名 → 本体图标（催化 record 的 displayProperties 图标全是同一张通用图，
# 用武器自己的图标卡片才能一眼认出是哪把枪）。
# 只收录带催化插槽的武器（weapons_full 里 plugs.catalysts 非空 = 异域催化武器）：
# 简中有同名异武器（"龙息"既是异域火箭筒也是一把冲锋枪），不过滤会把冲锋枪的图标挂到催化卡上
_CATA_ICON_BY_NAME: dict[str, str] = {}
_CATA_HASH_BY_NAME: dict[str, str] = {}
for _h, _w in _weapons_full.items():
    if ((_w.get("plugs") or {}).get("catalysts") and _w.get("name") and _w.get("icon")):
        _CATA_ICON_BY_NAME.setdefault(_w["name"], _w["icon"])
        _CATA_HASH_BY_NAME.setdefault(_w["name"], str(_h))

# 武器 itemHash → 可选催化插件 {插件hash: (名称, 图标)}。可锻造异域有多个候选催化
# （零号修订 4 选 1），玩家实际装了哪个要从武器插槽（Item 组件 304）的 plugHash 反查
_CATA_OPTIONS: dict[str, dict[str, tuple[str, str]]] = {}
for _h, _w in _weapons_full.items():
    _opts: dict[str, tuple[str, str]] = {}
    for _c in (_w.get("plugs") or {}).get("catalysts") or []:
        if isinstance(_c, dict) and _c.get("hash"):
            _opts[str(_c["hash"])] = (_c.get("n", ""), _c.get("i", ""))
    if _opts:
        _CATA_OPTIONS[str(_h)] = _opts


def _merged_records(prof: dict) -> dict:
    """合并 profileRecords 与 characterRecords。

    关键：部分记录（突袭/地牢凯旋等）只在 characterRecords 里返回，
    只读 profileRecords 会误判成"未完成"，称号进度因此长期偏小。
    """
    pr = dict((prof.get("profileRecords") or {}).get("data", {}).get("records", {}))
    for ch in ((prof.get("characterRecords") or {}).get("data") or {}).values():
        pr.update((ch or {}).get("records", {}))
    return pr


@_traced(lambda name, kind: ({"patterns": "武器锻造图案", "catalysts": "异域催化"}
                            .get(kind, "称号进度") + f" {name}"))
async def node_report(name: str, kind: str) -> dict:
    """称号(kind=titles)/锻造图案(kind=patterns)/异域催化(kind=catalysts)：基于记录状态"""
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    return await node_report_member(member, kind)


async def node_report_member(member: dict, kind: str) -> dict:
    r = await client().get(f"/Platform/Destiny2/{member['mtype']}/Profile/{member['mid']}/",
                         params={"components": "200,900"})
    d = _parse(r)
    if d.get("ErrorCode") == 1601:  # 档案不可用：不查会静默变成"全是0"的假结果
        if d.get("ErrorStatus") == "DestinyAccountNotFound":
            raise PlayerLookupError("这个平台成员号下没有命运2档案（对方可能主要玩别的平台）")
        raise ProfilePrivateError("该玩家的命运2档案设为了私密，无法查询")
    if d.get("ErrorCode") != 1:
        raise RuntimeError(d.get("Message", "Bungie API 错误"))
    prof = d.get("Response") or {}
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
    elif kind == "catalysts":
        # 异域催化：藏品"异域催化"节点（2744330515）下按槽位分三组（动能/能量/威能），
        # 每条 record 就是一个催化目标——完成态是解锁标记，目标进度（击杀数之类）
        # 在玩家 record 自带的 objectives 里（和锻造萃取进度同结构，_obj_progress 可读）。
        # 记录名大多是"武器名+催化"，少数不带后缀（洛伦兹驱动器/千语等），
        # 统一剥掉"催化/催化剂"后缀当武器名展示。
        # 可锻造异域（描述含塑形/重塑）单独标出；它们"应用任意催化"就算完成，
        # 已获得的再读武器插槽标出实际装的是哪个候选催化（_applied_catalyst_plugs）。
        applied = await _applied_catalyst_plugs(member)
        for mid in _pnodes["2744330515"]["children"]["presentationNodes"]:
            nd = _pnodes[str(mid["presentationNodeHash"])]
            slot = nd["displayProperties"].get("name") or "催化"
            for rc in nd["children"]["records"]:
                rh = str(rc["recordHash"])
                d = _records.get(rh)
                if not d:
                    continue
                dp = d.get("displayProperties", {})
                nm = dp.get("name") or ""
                if not nm or "[PLACEHOLDER" in nm:
                    continue
                for suf in ("催化剂", "催化"):
                    if nm.endswith(suf):
                        nm = nm[:-len(suf)]
                        break
                wn = _CATA_WEAPON_ALIAS.get(nm, nm)
                st_rh = pr.get(rh) or {}
                done_n, total_n = _cata_progress(st_rh)
                desc = (dp.get("description", "") or "")[:120]
                # started：目标里有任何进度但还没完成 → 卡上标"进行中"，和没开磨的区分开
                started = any((o.get("progress") or 0) > 0
                              for o in (st_rh.get("objectives") or []))
                craftable = "塑形" in desc or "重塑" in desc
                wh = _CATA_HASH_BY_NAME.get(wn, "")
                ap = applied.get(wh) or ("", "")
                items.append({
                    "name": nm,
                    "icon": _CATA_ICON_BY_NAME.get(wn) or (BASE + dp["icon"] if dp.get("icon") else ""),
                    "desc": desc,
                    "completed": _rec_done(pr, rh),
                    "gilded": False, "group": slot,
                    "done_n": done_n, "total_n": total_n,
                    "started": started, "craftable": craftable,
                    "applied_name": ap[0], "applied_icon": ap[1],
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
    # 图案/催化：顺序已在上面按固定节点顺序排好（催化=动能→能量→威能，同藏品页），不再二次排序
    done = sum(1 for i in items if i["completed"])
    title = {"titles": "称号进度", "patterns": "武器锻造图案",
             "catalysts": "异域催化"}.get(kind, "称号进度")
    return {"display": f"{member['display']}#{fmt_code(member['code'])}",
            "title": title,
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
    # 维护窗口内跑出来的热力图不可信（官方维护期翻页会缺对局，算出来天数/场次偏少），
    # 别拿它当「已统计到哪一场」的基准，直接丢掉重跑
    bad = [k for k, v in _HEAT_CACHE.items()
           if isinstance(v, dict) and bst.suspect_stamp(v.get("updated"))]
    for k in bad:
        _HEAT_CACHE.pop(k, None)
    if bad:
        print(f"[维护] heatmap_cache 丢弃 {len(bad)} 条维护窗口内的缓存，下次查询重跑",
              flush=True)


def _save_heat_cache():
    save_seen_players()
    if len(_HEAT_CACHE) > _HEAT_CACHE_MAX:  # 满了丢最久没更新的四分之一，够用就行
        stale = sorted(_HEAT_CACHE, key=lambda k: _HEAT_CACHE[k].get("updated") or "")
        for k in stale[: _HEAT_CACHE_MAX // 4]:
            _HEAT_CACHE.pop(k, None)
    try:
        dump_json(_writable_path(_HEAT_CACHE_FILE), _HEAT_CACHE, separators=(",", ":"))
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
                await _job_checkpoint(jid)
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
        if not days:
            # 一场都没翻到：接口出问题（维护/限流），不是「这人没打过」——
            # 以前会把空 days 写进缓存，把那块热力图永久抹成空白
            raise DataSuspiciousError(
                "热力图：对局历史一场都没翻到（疑似官方维护或接口异常），"
                "已拦下避免出错误统计，稍后重发一次即可")
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
def _agg_matches(matches: list[dict]) -> dict:
    """一批对局（已按 instance 去重、新→旧）→ 战绩汇总：胜负 / K-D / 模式细分 / 连胜。

    「近期战绩」与「全生涯统计」共用这一份聚合，保证两块的算法口径完全一致。"""
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
    opp = sum(m.get("opp", 0) for m in done)

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
        "opp": opp,
        # 智谋专用：对局历史的 score 实测就是「存入荧光」（120 场逐场与 PGCR motesDeposited
        # 相等，生涯 8,075 = 官方 motesDeposited 8,075）。其它模式这个键不用。
        "motes": sum(m["score"] for m in matches),
        "best_kills": max((m["kills"] for m in done), default=0),
        "kd": (kills / deaths) if deaths else 0.0,
        "kda": ((kills + assists) / deaths) if deaths else 0.0,
        "avg_kills": (kills / len(done)) if done else 0.0,
        "eff": (sum(eff_list) / len(eff_list)) if eff_list else 0.0,
        "rate_base": len(rate_base),
        "clear_rate": (sum(1 for m in rate_base if m["completed"]) / len(rate_base) * 100) if rate_base else 0.0,
        "hours": secs / 3600,
        "breakdown": breakdown,
    }


def _merge_matches(per_char: dict[str, list[dict]]) -> list[dict]:
    """跨角色合并对局（按 instance 去重，同一场只算一次），新→旧"""
    seen, out = set(), []
    for rows in per_char.values():
        for m in rows:
            key = m["instance"] or f"{m['ref']}{m['period']}"
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
    out.sort(key=lambda m: m["period"], reverse=True)
    return out


async def _char_history_deep(mtype: int, mid: str, cid: str, mode: int, cap: int,
                             sem: asyncio.Semaphore) -> tuple[list[dict], bool]:
    """单角色翻完对局历史：返回 (新→旧的对局列表, 是否被上限/失败截断)。

    cap<=0 = 不限（翻到接口给不出为止，Bungie 每角色约 60 页 × 250 场）。同一角色的
    几页并发拉，跨角色由 sem 限流——250 场一页的响应就是几百毫秒，冷账号也就几秒。"""
    out: list[dict] = []
    page_cap = 0 if cap <= 0 else max(1, (cap + _HISTORY_PAGE - 1) // _HISTORY_PAGE)
    pg = 0
    while True:
        wave = [p for p in range(pg, pg + _HISTORY_WAVE) if not page_cap or p < page_cap]
        if not wave:
            return out, True          # 页数到顶：后面还有历史没翻
        async def _one(p: int):
            async with sem:
                try:
                    return await activity_history(mtype, mid, cid, mode, count=_HISTORY_PAGE, page=p)
                except BungieMaintenanceError:
                    # 维护要一路抛上去（上层会换成「维护中」的提示）：不能当成「这一页坏了」，
                    # 否则拿半截历史出卡——维护期最典型的现象就是「这模式没打过 / 已达上限」
                    raise
                except Exception:  # noqa: BLE001  单页失败：保留已拿到的，标记不完整
                    return None
        rows_list = await asyncio.gather(*(_one(p) for p in wave))
        short = False
        for rows in rows_list:
            if rows is None:
                return out, True
            out += rows
            if len(rows) < _HISTORY_PAGE:
                short = True
        pg += len(wave)
        if short:
            return out, False
        if page_cap and pg >= page_cap:
            return out, True


async def _history_deep(mtype: int, mid: str, cids, mode: int, cap: int) -> tuple[dict[str, list[dict]], bool]:
    """跨角色翻全生涯对局历史 → ({cid: [对局…]}, 是否有角色被截断)"""
    sem = asyncio.Semaphore(_HISTORY_CONCURRENCY)
    res = await asyncio.gather(*(_char_history_deep(mtype, mid, c, mode, cap, sem) for c in cids))
    return {c: r[0] for c, r in zip(cids, res)}, any(r[1] for r in res)


def _emblem_of(chars: dict) -> tuple[str, str]:
    """玩家名片（宽幅底图 + 96×96 纹章）：取「玩得最久的角色」，和 /生涯 的名牌同源"""
    top = max(chars.values(), key=lambda c: int(c.get("minutesPlayedTotal") or 0), default=None)
    if not top:
        return "", ""
    return BASE + (top.get("emblemPath") or ""), BASE + (top.get("emblemBackgroundPath") or "")


def _is_gm_nightfall(name: str) -> bool:
    """宗师难度日落：两种历史写法（新「宗师日落: X」/ 旧「日落: 宗师」），也含赛季活动的宗师档"""
    return (name.startswith("宗师日落") or "日落: 宗师" in name or "日落：宗师" in name
            or name.endswith("：宗师") or name.endswith(": 宗师"))


def _is_master_nightfall(name: str) -> bool:
    """大师难度日落：「日落: 大师」这种写法（大师突袭 / 大师地牢另算，不混进来）"""
    return "日落" in name and "大师" in name


def _pve_endgame(matches: list[dict]) -> dict:
    """终局 PvE 通关数（只算完成的对局）：突袭(4) / 地牢(82) / 宗师日落 / 大师日落 /
    终极征服。

    官方统计接口没有「分难度日落」，也没有逐副本计数——只能按对局历史数（raid.report
    同源做法）。实测 Wj#8984：大师日落 30 与 raid.report 完全一致、突袭 535 ≈ 对方的 536。
    征服系列（专家/大师/宗师/终极）是赛季中心的高难活动，实测顺着 mode=7 的历史一起拿到
    （modes=[7,3,18]），这里只把最高档「终极征服」单列出来。"""
    out = {"raid": 0, "raid_all": 0, "dungeon": 0, "dungeon_all": 0,
           "gm": 0, "gm_all": 0, "master_nf": 0, "master_nf_all": 0,
           "ultimate": 0, "ultimate_all": 0}
    for m in matches:
        if m["mode"] == 4:
            out["raid_all"] += 1
            out["raid"] += int(bool(m["completed"]))
        elif m["mode"] == 82:
            out["dungeon_all"] += 1
            out["dungeon"] += int(bool(m["completed"]))
        elif m["name"].startswith(_GM_CONQUEST_TIERS[0]):     # 终极征服
            out["ultimate_all"] += 1
            out["ultimate"] += int(bool(m["completed"]))
        elif _is_gm_nightfall(m["name"]):
            out["gm_all"] += 1
            out["gm"] += int(bool(m["completed"]))
        elif _is_master_nightfall(m["name"]):
            out["master_nf_all"] += 1
            out["master_nf"] += int(bool(m["completed"]))
    return out


def _conqueror_gild_hash() -> str:
    """「征服者」称号的镀金记录 hash（在记录索引里找带 gildingTrackingRecordHash 的那个）。

    这条记录的 `completedCount` = 历史累计镀金次数（实测 Wj#8984 = 4，与 raid.report 的
    「GILDED CONQUEROR」一致）；objectives 里的 progress/completionValue 只是**本季**进度。"""
    for h, d in _records.items():
        ti = d.get("titleInfo") or {}
        if not ti.get("gildingTrackingRecordHash"):
            continue
        if "征服者" in ((d.get("displayProperties") or {}).get("name") or ""):
            return str(ti["gildingTrackingRecordHash"])
    return ""


_GAMBIT_SUM_KEYS = ("activitiesEntered", "activitiesWon", "kills", "deaths", "assists",
                    "secondsPlayed", "precisionKills", "bestSingleGameKills",
                    "motesDeposited", "motesPickedUp", "motesDenied", "motesLost",
                    "bankOverage", "invasions", "invasionKills", "invasionDeaths",
                    "invaderKills", "invaderDeaths", "primevalKills", "primevalDamage",
                    "highValueKills", "blockerKills", "smallBlockersSent",
                    "mediumBlockersSent", "largeBlockersSent", "roundsPlayed", "roundsWon")


async def gambit_career(mtype: int, mid: str, chars) -> dict:
    """智谋生涯（官方角色级 modes=63 跨角色求和）：荧光 / 入侵 / 原始使者这些专属数据只有它给。

    官方 gambit 桶实测是**完整**的（Wj#8984 三角色 activitiesEntered 285+4+109 = 398
    = 对局历史去重 398 场，逐角色相等）；与 PvP 的 allPvP 少算 2020 年后的试炼/铁旗不同，
    所以智谋顶部放心用官方数。字段名（官方 statId，2026-10 实测都存在）：
    motesDeposited/motesDenied/motesLost/motesPickedUp = 存入/截夺/丢失/拾取荧光，
    invasions/invasionKills/invasionDeaths = 入侵次数/入侵击杀/入侵中阵亡，
    invaderKills/invaderDeaths = 击败入侵者/被入侵者击败，primevalKills = 原始使者击杀。
    任角色拉不到就跳过，全拉不到返回 {}（卡片退化成窗口聚合那套）。"""
    tot = {k: 0 for k in _GAMBIT_SUM_KEYS}
    ok, bad = False, ""
    for cid in chars:
        # 注意：gambit 桶不是 groups 能选出来的——必须带 modes=63，返回体里才出现
        # `pvecomp_gambit`（实测 groups=101,103 只给 allPvP/allPvE/raid… 那几个）
        r = await client().get(
            f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
            params={"groups": "101,103", "modes": 63})
        resp = _parse(r)
        v, why = _verdict(resp)
        if v == "bad":
            bad = why
            continue
        st = resp.get("Response") or {}
        at = (st.get("pvecomp_gambit") or {}).get("allTime") or {}
        if not at:
            continue
        ok = True
        for k in _GAMBIT_SUM_KEYS:
            v2 = ((at.get(k) or {}).get("basic") or {}).get("value")
            if v2:
                tot[k] += int(v2)
    if not ok:
        if bad:
            # 以前任角色拉不到就跳过、全拉不到返回 {}——维护期整块智谋生涯会静静消失，
            # 卡片退化成窗口聚合，用户看不出来数字少了一大截
            raise DataSuspiciousError(
                f"智谋生涯统计读取失败（{bad}）：疑似官方维护或接口异常，"
                f"已拦下避免出错误统计，稍后重发一次即可")
        return {}
    ent, wins = tot["activitiesEntered"], tot["activitiesWon"]
    tot["win_rate"] = (wins / ent * 100) if ent else 0.0
    tot["kd"] = (tot["kills"] / tot["deaths"]) if tot["deaths"] else 0.0
    tot["kda"] = ((tot["kills"] + tot["assists"]) / tot["deaths"]) if tot["deaths"] else 0.0
    tot["avg_kills"] = (tot["kills"] / ent) if ent else 0.0
    tot["hours"] = tot["secondsPlayed"] / 3600.0
    tot["blockers"] = (tot["smallBlockersSent"] + tot["mediumBlockersSent"]
                       + tot["largeBlockersSent"])
    return tot


async def _profile_extras(mtype: int, mid: str) -> dict:
    """成就分 + 征服者镀金次数 + 终极征服本季进度（GetProfile components=900，一次请求；失败不拖垮卡片）

    成就分有两个口径：`lifetimeScore` = 生涯累计（含已失效的传承分数，raid.report 用的就是它）、
    `score`/`activeScore` = 现有（游戏内当前凯旋分）。实测 Wj#8984：现有 17,223 / 累计 84,592。"""
    out = {"triumph": 0, "triumph_now": 0, "gilds": 0, "ultimate": (0, 0)}
    try:
        r = await client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                               params={"components": "900"})
        pr = (_parse(r).get("Response") or {}).get("profileRecords") or {}
        # 注意：分数与记录都嵌在 profileRecords.data 里（同 _merged_records 的读法）
        pdata = pr.get("data") if isinstance(pr.get("data"), dict) else {}
        pdata = pdata or {}
        out["triumph"] = int(pdata.get("lifetimeScore") or pdata.get("score")
                             or pdata.get("activeScore") or 0)
        out["triumph_now"] = int(pdata.get("score") or pdata.get("activeScore")
                                 or pdata.get("lifetimeScore") or 0)
        recs = pdata.get("records") or {}
        gild_rec = recs.get(_conqueror_gild_hash()) or {}
        out["gilds"] = int(gild_rec.get("completedCount") or 0)
        if not out["gilds"]:      # 老数据回退：拿本季目标进度（至少不为空）
            for o in (recs.get(_GM_REC_GILD, {}).get("objectives") or []):
                out["gilds"] = max(out["gilds"], int(o.get("progress") or 0))
        out["ultimate"] = _obj_progress(recs.get(_GM_REC_ULTIMATE, {}))   # 本季终极征服 x/y
    except Exception:  # noqa: BLE001  记录拉不到就显示 0，不影响其它数据
        pass
    return out


@_traced(lambda name, mode=5, count=0, career=False, jid="": f"战绩查询 {name}")
async def mode_report(name: str, mode: int, count: int = 0, career: bool = False,
                      jid: str = "", endgame: bool = False) -> dict:
    """基于对局历史聚合某模式战绩（跨角色合并 + 细分模式 + 胜率）

    count：近期窗口 = **跨角色合并后**的最近多少场（0 = 读后台配置 pvp/pve/gambit_recent_count，
        默认 100；每角色仍先各拉 count 场，保证合并后能凑齐全局最近 count 场）
    career：再算一份全生涯聚合放进 rep["career"]（跨角色去重、含全部模式）。PvP 顶部
        生涯统计用它——官方 allPvP 少算 2020 年之后的试炼/铁旗（实测差一半以上），
        只有对局历史是全的；受 match_cap("pvp") 约束，被截断时 career["capped"]=True。
    endgame：/pve 用。翻全生涯 PvE 历史（不受生涯武器场次上限约束——只翻历史页，不拉 PGCR），
        数出突袭 / 地牢 / 宗师日落 / 大师日落 / 终极征服通关数（rep["endgame"]），并附带
        成就分（现有 + 生涯累计）与征服者镀金次数（rep["triumph"] / rep["triumph_now"] /
        rep["gilds"] / rep["ultimate"]）。
    mode=63（智谋）时额外取官方 gambit 生涯桶（rep["gambit"]：荧光 / 入侵 / 原始使者）；
    智谋卡片的「最近对局」窗口里，对局历史的 score 就是存入荧光（rep["motes"]）。
    """
    member = await resolve_member(name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    profile = await get_profile(mtype, mid)
    chars = profile.get("characters", {}).get("data", {})
    if not chars:
        raise LookupError(f"{member['display']} 档案下没有角色")

    mdisp = f"{member['display']}#{fmt_code(member['code'])}"
    nch = len(chars)
    cids = list(chars)
    kind = {5: "pvp", 63: "gambit"}.get(mode, "pve")
    n_recent = count or recent_count(kind)
    deep = career or endgame
    # 只有 PvP 的全模式生涯聚合吃「生涯武器场次上限」（它和 /pvp生涯武器 共用一份口径）；
    # /pve 的终局通关数不吃——它只翻对局历史页、不逐场拉 PGCR，翻全生涯也就十几秒，
    # 而按上限截断会让「突袭 / 大师日落」这些老记录直接数丢（实测截到 3000 场时大师日落变 0）。
    cap = match_cap(kind) if career else 0
    # 胜点图可以比窗口长：按需多翻几页（智谋默认画 100 个格子，窗口也是 100，一般不额外翻）
    grid = grid_count(kind) if mode in (5, 63) else 0
    fetch_n = max(n_recent, grid)

    # 两种模式都走翻页器：窗口模式下每角色也只翻首页（250 场），合并后再截全局最近 N 场，
    # 这样"设定 300 局"就是整个账号最近 300 局，而不是每角色各 300 局
    fetch_cap = cap if deep else fetch_n
    log_progress(f"mode:{mid}:{mode}", 0, nch, label=f"战绩 {mdisp}", force=True,
                 extra=(f"拉取对局历史（每角色最多 {fetch_cap or '不限'} 场）" if deep
                        else f"拉取每个角色最近 {fetch_n} 局对局历史"))
    hist, capped = await _history_deep(mtype, mid, cids, mode, fetch_cap)
    log_progress(f"mode:{mid}:{mode}", nch, nch, label=f"战绩 {mdisp}", force=True,
                 extra=f"已收 {sum(len(v) for v in hist.values())} 场历史")

    all_matches = _merge_matches(hist)
    window = all_matches[:n_recent]            # 窗口 = 跨角色合并后的最近 N 局
    emblem, emblem_bg = _emblem_of(chars)
    rep = {"display": mdisp, "emblem": emblem, "emblem_bg": emblem_bg,
           "window": n_recent, "grid": grid, "mode": mode,
           "playtime_hours": sum(int(c.get("minutesPlayedTotal") or 0)
                                 for c in chars.values()) / 60.0}
    rep.update(_agg_matches(window))
    rep["matches"] = window          # 「最近对局」列表用（渲染层自己截前 N 条）
    rep["grid_matches"] = all_matches[:grid] if grid else []
    if mode == 63:
        try:
            rep["gambit"] = await gambit_career(mtype, mid, cids)
        except Exception:  # noqa: BLE001  官方智谋桶拉不到就只显示窗口聚合
            rep["gambit"] = {}
    if career:
        car = _agg_matches(all_matches)
        car["cap"] = cap
        car["capped"] = bool(capped and cap)
        rep["career"] = car
        rep["history_total"] = sum(len(v) for v in hist.values())
    if endgame:
        eg = _pve_endgame(all_matches)
        eg["scanned"] = len(all_matches)
        eg["cap"] = cap
        eg["capped"] = bool(capped and cap)
        rep["endgame"] = eg
        rep.update(await _profile_extras(mtype, mid))
    return rep


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
EV_CACHE_VER = 4      # 4: 记官方 nextRefreshDate / 本轮归属刷新点 / 抓取时刻（时间强关联）
_EV_CACHE: dict = {"data": None}
_EV_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = \
    weakref.WeakKeyDictionary()
_EV_INDEX = None

# ---------- 时间强关联（货架什么时候换，以官方字段为准） ----------
# 官方 GetVendors 里每个 vendor 带 nextRefreshDate、**每件商品**带 overrideNextRefreshDate，
# 后者才是货架的刷新时刻。实测 2026-10-08：10 个光尘 vendor 的实体都是 10-14 01:00（周值）、
# 而挂着的 18 件商品全是 10-09 01:00（次日 1 点）——**货架每天 1 点换**（用户口径对，
# 只看 vendor 字段会误判成周刷）。所以下面一律以商品级 override 为准，没有才退回 vendor 值。
# 用户报的「光尘商店没刷新」根因：2026-10-07/08 那两天官方维护，货架冻住不换，
# 而旧实现按「每天 1 点换缓存键」重取时官方接口仍回同一批旧货，落到盘里就当成了新货架。
EV_RESET_HOUR = 1                      # 复位时刻：北京时间凌晨 1 点（校验/回退口径）
EV_TZ = datetime.timezone(datetime.timedelta(hours=8))
EV_MAX_AGE = 30 * 60                   # 缓存最长保留：超过就跟官方核一次（官方可能临时换架）
EV_RETRY_AFTER = 10 * 60               # 过了官方刷新点却还没换架（官方延迟）：最快 10 分钟再问
EV_AFTER_RESET = 30 * 60               # 复位点后 30 分钟 = 「官方可能还没切完」窗口
EV_AFTER_RESET_TTL = 10 * 60           # 该窗口内缓存只留 10 分钟

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


def _ev_dt(ts: float) -> datetime.datetime:
    """epoch → 北京时间 datetime（商店复位按北京时间算，与本机时区无关）"""
    return datetime.datetime.fromtimestamp(ts, EV_TZ)


def cn_str(fmt: str, ts: float | None = None) -> str:
    """北京时间格式化（日志/面板/卡片统一走这里；本机时区不是 +8 也不会写错）"""
    return _ev_dt(ts if ts is not None else time.time()).strftime(fmt)


def _cn_now() -> datetime.datetime:
    """当前北京时间（aware）。

    全盘时钟口径：游戏的日复位 / 周复位都是 UTC 锚点（UTC 17:00 = 北京次日 01:00），
    凡是「算今天是哪天 / 这周是哪周 / 谁在场」的逻辑只用这里，不碰本机时区——
    机器时区一变（或带 DST），用本机时间就会把「今天」算错一天。
    """
    return datetime.datetime.now(EV_TZ)


def _ev_day(ts: float | None = None) -> str:
    """「当日」口径：商店按北京时间凌晨 1 点复位，所以 1 点前算前一天（其他日缓存也用）"""
    t = _ev_dt(ts if ts is not None else time.time())
    return (t - datetime.timedelta(hours=EV_RESET_HOUR)).strftime("%Y-%m-%d")


def _ev_reset_prev(ts: float) -> float:
    """≤ ts 的最近一个 01:00（北京时间）epoch——官方字段缺失时的回退口径"""
    t = _ev_dt(ts) - datetime.timedelta(hours=EV_RESET_HOUR)
    b = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return (b + datetime.timedelta(hours=EV_RESET_HOUR)).timestamp()


def _ev_parse_when(v) -> float:
    """官方 ISO8601 时间 → epoch。哨兵（9999-12-31 = 不再刷新）与坏值返回 0.0"""
    try:
        d = datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return 0.0
    if d.year > 2099:
        return 0.0
    return d.timestamp()


def _ev_lock() -> asyncio.Lock:
    """当前事件循环专用的锁（exe 里三个循环并存，同 client() 的处理）"""
    loop = asyncio.get_running_loop()
    lk = _EV_LOCKS.get(loop)
    if lk is None:
        lk = asyncio.Lock()
        _EV_LOCKS[loop] = lk
    return lk


def _ev_cache_path() -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, EV_CACHE_FILE)


def _ev_cache_get() -> dict | None:
    """内存 / 落盘统一入口：返回整条记录（时间戳在 data 里），能不能用交给 _ev_usable。

    以前这里按「缓存键 == 今天」判，1 点一过整条作废，看着在刷新，其实不问官方口径；
    现在只按时间戳判有效期（含官方 nextRefreshDate），跨天不再强制作废。
    """
    if _EV_CACHE.get("data"):
        return _EV_CACHE
    try:
        d = json.load(open(_ev_cache_path(), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if d.get("ver") != EV_CACHE_VER or not isinstance(d.get("data"), dict):
        return None
    _EV_CACHE.update(d)          # 落盘命中回填内存
    return _EV_CACHE


def _ev_cache_save() -> None:
    try:
        dump_json(_ev_cache_path(), _EV_CACHE)
    except Exception:  # noqa: BLE001
        pass


def _ev_reason(ent: dict, now: float) -> str:
    """这条货架现在还能不能直接出卡片。'' = 可以，否则给一句「为什么要重取」。"""
    d = ent.get("data") or {}
    at = float(d.get("fetched_at") or ent.get("at") or 0.0)
    nr = float(d.get("next_refresh") or 0.0)
    ra = float(d.get("refresh_at") or 0.0)
    if not d.get("sections"):
        return "缓存里没有商品"
    if bst.suspect_at(at):
        return "货架是维护窗口内抓的（维护期官方会冻住货架，恢复后不重取就是旧货）"
    if at < bst.clean_since():
        return "货架早于最近一次维护恢复，官方可能是在维护后才换的架"
    if nr and now >= nr:
        # 过了官方刷新点：要么官方已换架（我们拿的是旧货），要么官方延迟没换。
        # 两种都得再问一次，但 10 分钟内问过就别问了，免得把接口打成死循环。
        if now - at < EV_RETRY_AFTER:
            return ""
        return f"已过官方刷新点（{_ev_dt(nr):%m-%d %H:%M}）"
    ttl = EV_MAX_AGE
    if ra and now - ra < EV_AFTER_RESET:
        ttl = EV_AFTER_RESET_TTL      # 复位点后官方常在半小时内才真正切完货架
    if now - at >= ttl:
        return f"距上次跟官方核对已 {int(now - at) // 60} 分钟"
    return ""


def _ev_usable(ent: dict, now: float) -> bool:
    return not _ev_reason(ent, now)


def ev_round_at(now: float | None = None) -> float:
    """当前这一轮的刷新点（≤now 的最近一个北京 01:00）——卡片说「这轮」时的指代对象"""
    return _ev_reset_prev(now if now is not None else time.time())


def ev_behind(data: dict, now: float | None = None) -> bool:
    """手里的货架是不是「还没轮到今天这一轮」（当前轮 = 最近一个 01:00 之后上架的货）。

    卡片据此标一句「今天 01:00 的新货架还没取到」——官方偶尔晚切（维护后最常见）时，
    我们手里确实只有上一轮的货，宁可写在脸上也不假装是当天刷的。
    """
    ra = float(data.get("refresh_at") or 0)
    if not ra:
        return False
    return ra < _ev_reset_prev(now if now is not None else time.time())


def _ev_cycle_days(nr: float, now: float, prev_days: int = 0) -> int:
    """官方这栏货架的周期（天）：优先用上一轮实测出来的周期，其次看官方字段本身。

    周三 01:00 是周重置锚点（D2 周复位 = UTC 周二 17:00），落在它上面当 7 天；
    否则按「离下次刷新还有多久」判：>1.5 天当周周期，否则当天周期。
    """
    if prev_days:
        return prev_days
    if nr:
        t = _ev_dt(nr)
        if t.weekday() == 2 and t.hour == EV_RESET_HOUR:
            return 7
        if nr - now > 1.5 * 86400:
            return 7
    return 1


def _ev_stamp(data: dict, now: float, prev: dict | None) -> dict:
    """给刚抓到的货架盖时间戳：本轮归属的复位点 / 官方下次刷新 / 刷新周期 / 抓取时刻。

    归属点（refresh_at）的算法，从准到糙：
      ① 上一轮抓取时官方说的「下次刷新」刚过去 → 那就是本轮起点（复位点当场就知道，最准）；
      ② 都没有（首跑/换机器/官方没给字段）→ 从官方给的下次刷新按周期回推到 ≤ now 的那个点。
    官方说的刷新点已过却仍回上一轮货架时（stale），归属点是**上一轮**——货架内容确实还是上一轮的，
    卡片那头另出警告条说明官方延迟，别把旧货标成新一轮。
    """
    nr = float(data.get("next_refresh") or 0.0)
    prev_d = (prev or {}).get("data") or {}
    prev_next = float(prev_d.get("next_refresh") or 0.0)
    ra = 0.0
    if nr and prev_next and prev_next <= now and prev_next < nr:
        ra = prev_next
    if not ra and nr:
        step = 86400 * _ev_cycle_days(nr, now, int(prev_d.get("cycle_days") or 0))
        ra = nr - step
        while ra > now:              # 罕见：官方这次给的周期比上一轮长
            ra -= step
    if not ra:
        ra = _ev_reset_prev(now)     # 官方没给（旧数据/接口异常）：退回「最近一个 1 点」
    days = (nr - ra) / 86400.0 if nr else 0.0
    data["refresh_at"] = ra
    data["next_refresh"] = nr
    data["cycle_days"] = int(round(days)) if 0.4 < days < 400 else 0
    data["fetched_at"] = now
    data["updated"] = now
    data["stale"] = bool(nr and now >= nr)        # 官方刷新点已过 = 官方还没换架，卡片要提示
    data["day"] = _ev_day(ra)                     # 归属哪一轮（卡片抬头用）
    return data


def _ev_next_refresh(resps: list) -> float:
    """官方给的下次刷新时刻 = 跟踪的全部光尘商品里最早的刷新时刻。

    **以商品级 overrideNextRefreshDate 为准**：vendor 实体自己的 nextRefreshDate 是周刷新值
    （实测 10-14 01:00），而挂在上面的每件商品都带 override = **次日 01:00**——真正每天换的
    是货架（用户口径「每天一刷」对，vendor 字段会把人带偏）。商品没给 override 才退回 vendor 值。
    """
    want = [vh for _, _, vs in EV_SECTIONS for vh in vs]
    item_ts, ven_ts = [], []
    for resp in resps:
        vd = ((resp.get("vendors") or {}).get("data")) or {}
        sd = ((resp.get("sales") or {}).get("data")) or {}
        for vh in want:
            ven_ts.append(_ev_parse_when((vd.get(vh) or {}).get("nextRefreshDate")))
            group = sd.get(vh)
            items = (group.get("saleItems") or {}) if isinstance(group, dict) else {}
            for sale in items.values():
                if isinstance(sale, dict):
                    item_ts.append(_ev_parse_when(sale.get("overrideNextRefreshDate")))
    pick = [t for t in item_ts if t] or [t for t in ven_ts if t]
    return min(pick) if pick else 0.0


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
    return {"sections": sections, "source": "bungie",
            "next_refresh": _ev_next_refresh(resps)}


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

    **时间强关联**：货架「哪一轮」以官方商品级 overrideNextRefreshDate 为准（实测 =
    每天 01:00，见上面常量区的实测记录），输出前必查手里的货架在不在有效期：

      · 过了官方刷新点 → 重问官方（最快 EV_RETRY_AFTER 一次，防死循环）；
      · 复位点后 30 分钟内 → 缓存只留 10 分钟（官方常在复位点之后才真正切完货架）；
      · 维护窗口内抓的、早于最近一次维护恢复的 → 一律重取（维护期货架冻在上一轮，就是
        用户报的「光尘商店没刷新」）；
      · 其余 30 分钟跟官方核一次（官方偶尔临时换架）。

    返回的 data 带 refresh_at（本轮归属的 1 点）/ next_refresh（官方下次刷新）/
    fetched_at（抓取时刻）/ stale（刷新点已过但官方还没换架），卡片据此标注归属日，
    并用 `ev_behind` 判断要不要标「今天的新货架还没取到」。

    未授权时抛 BungieAuthRequired（上层提示去面板点授权）。
    """
    now = time.time()
    ent = None if force else _ev_cache_get()
    if ent:
        why = _ev_reason(ent, now)
        if not why:
            return ent["data"]
        print(f"[光尘] {time.strftime('%H:%M:%S')} 重取货架：{why}", flush=True)
    async with _ev_lock():
        now = time.time()
        if not force:
            ent = _ev_cache_get()
            if ent and not _ev_reason(ent, now):
                return ent["data"]        # 等锁期间别的调用（调度预取/并发查询）刚取过
        resps = await _ev_vendor_responses()
        data = _ev_from_vendors(resps)
        if not any(s.get("items") for s in (data.get("sections") or [])):
            # 官方维护/异常时会「成功但空货架」：这时落盘缓存会把一整天定成「没有商品」，
            # 而 1 点才换缓存键——用户看到的就是「光尘商店自动刷新失效了，一整天都是空的」
            raise DataSuspiciousError(
                "光尘商店：Bungie 返回的货架是空的（疑似官方维护或接口异常），"
                "已拦下不落缓存，稍后重发一次即可")
        _ev_stamp(data, now, ent)
        if data["stale"]:
            print(f"[光尘] {time.strftime('%H:%M:%S')} 官方刷新点 "
                  f"{_ev_dt(data['next_refresh']):%m-%d %H:%M} 已过，但官方仍回上一轮货架"
                  f"（{EV_RETRY_AFTER // 60} 分钟后自动再核）", flush=True)
        _EV_CACHE.clear()
        _EV_CACHE.update({"ver": EV_CACHE_VER, "at": now, "day": data["day"], "data": data})
        _ev_cache_save()
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
    """单件物品定义（公开实体接口，client 自带 6 小时 /Manifest/ 缓存）。

    取不到时**不写 _DEF_CACHE**：以前失败也把空 dict 缓存进去（进程活着一辈子），
    那个 hash 之后永远拿不到定义——和维护期查过就永久坏数据是同一个坑。
    """
    h = str(hash_int)
    if h in _DEF_CACHE:
        return _DEF_CACHE[h]
    try:
        r = await client().get(
            f"/Platform/Destiny2/Manifest/DestinyInventoryItemDefinition/{h}/")
        resp = r.json()
    except bst.BungieMaintenanceError:
        raise            # 维护中：如实报错，别把「问不了」缓存成「这件物品没有定义」
    except Exception:  # noqa: BLE001 网络抖动：这次没有，下次再试
        return {}
    if resp.get("ErrorCode") != 1:
        return {}
    d = resp.get("Response") or {}
    if d:
        _DEF_CACHE[h] = d
    return d


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
    """在场窗口：周六 01:00 → 周三 01:00（维护重置离场），一律北京时间（见 _cn_now）。"""
    t = _ev_dt(ts)
    wd, hm = t.weekday(), (t.hour, t.minute)
    if wd == 5:                       # 周六：1 点后算到场
        return hm >= (1, 0)
    if wd in (6, 0, 1):               # 周日/周一/周二：全天在
        return True
    if wd == 2:                       # 周三：1 点离场
        return hm < (1, 0)
    return False                      # 周四/周五：不在


def _xur_next_arrival(ts: float) -> float:
    """下次抵达 = 下一个周六 01:00（含今天周六但还没到 1 点的情况），一律北京时间。"""
    t = _ev_dt(ts)
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
            dump_json(os.path.join(base, _KYBER_CACHE_FILE), data)
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
    at = _ev_dt(arr) if arr else None
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
    rd = r.json()
    v, why = _verdict(rd)
    if v != "ok":
        # 以前不查 ErrorCode：维护期 Response 为空 → 轮换卡变成「什么都没有」并按
        # key="unknown" 落盘缓存，用户看到的是空轮换而不是「查不了」
        raise DataSuspiciousError(
            f"本周轮换里程碑读取失败（{why}）：疑似官方维护或接口异常，稍后重发一次即可")
    resp = rd.get("Response") or {}
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
        dump_json(_rot_cache_path(), {"ver": ROT_CACHE_VER, "key": key, "data": data})
    except Exception:  # noqa: BLE001
        pass
    return data


# ---------- 扭曲星球轮换（每小时换目的地，7 小时一轮） ----------
# 数据来源同 rotation_pairs.json 里的 distortion 段（scrape_starside_rotation.py 抓）。
# 口径与 starside 轮换页一致：表以「周一 00:00」为起点，目的地 =
# cycle[slots[周几(周一=0)][小时]]；周期 7 小时、一周 168 时段，因此每周的表相同。
# 时钟一律北京时间（`_cn_now`）：机器时区一变整张表就错位，锚在 +08 才和页面/游戏一致。
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
    """扭曲星球当前时段。dt_ 缺省取当前北京时间（表按本机时钟高亮，全盘统一北京）。

    返回 {"ok": True, dest 当前目的地, range 时段文字, start/end 时段起止,
          next_dest 下一个目的地, next_at 切换时刻, next_hm 切换时刻(HH:MM),
          next_in_sec 距切换秒数, today 今日剩余各时段(含当前, current 标记)}。
    数据缺失时返回 {"ok": False}，调用方按「无此板块」处理。
    """
    d = _distortion()
    cyc, slots = d.get("cycle") or [], d.get("slots") or []
    now = dt_ or _cn_now()
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
_GM_CACHE_VER = 2     # v2：加本周挑战武器 weapon 字段（旧缓存没有）
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
    """每周缓存键：周三凌晨 1 点（=周二 17:00 UTC）刷新，1 点前算上一周。一律北京时间。"""
    t = _ev_dt(ts if ts is not None else time.time())
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
        dump_json(os.path.join(base, name),
                  {"ver": ver, "key": key, "at": time.time(), "data": data})
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


_WEP_EN_IDX: dict | None = None


def _weapon_by_en(en: str) -> dict:
    """英文武器名 → 本地索引（weapons.json：中文名/类型/图标）。

    英文名→hash 倒排优先用 item_en.json（build_item_index.py 产出，会进 exe 包）；
    缺了退 raw_items_en_lite.json（65MB，只在开发机上有）。同名多个 hash
    （原版/专家/异域任务卷）取第一个能对上武器索引的。"""
    global _WEP_EN_IDX
    if _WEP_EN_IDX is None:
        idx: dict[str, list] = {}
        try:
            idx = json.load(open(_idx_file("item_en.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            try:
                lite = json.load(open(_idx_file("raw_items_en_lite.json"), encoding="utf-8"))
                for h, v in lite.items():
                    n = ((v or {}).get("displayProperties") or {}).get("name")
                    if n:
                        idx.setdefault(n.lower(), []).append(h)
            except Exception:  # noqa: BLE001  没建过英文索引时武器列退化为英文原名
                pass
        _WEP_EN_IDX = idx
    for h in _WEP_EN_IDX.get((en or "").lower()) or []:
        rec = _weapons.get(h)
        if rec and rec.get("name"):
            return {"zh": rec["name"], "type": rec.get("type") or "",
                    "icon": rec.get("icon") or ""}
    return {}


@_traced("当前宗师")
async def gm_this_week(force: bool = False) -> dict:
    """本周宗师夜袭（每周缓存）。lfcarry 固定页声明本周 GM，映射成中文+横图。

    同页还带本周挑战武器（GM 首通必掉的那把），一并解析映射成中文+图标。"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    wk = _gm_week_key()
    if not force:
        disk = _json_cache(base, GM_CACHE_FILE, _GM_CACHE_VER, wk)
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
    wep = {}
    wm = re.search(r"weekly challenge weapon is ([^,.\n]{2,60}?), an? ([a-z ]{3,30})", text)
    if wm:
        wep = _weapon_by_en(wm.group(1).strip())
        wep.setdefault("en", wm.group(1).strip())
        wep["type_en"] = wm.group(2).strip()
    data = {"ok": True, "week": wk, "en": en, "zh": rec.get("zh") or en,
            "hash": str(rec.get("hash") or ""), "pgcr": rec.get("pgcr") or "",
            "dest_en": dest_en, "dest_zh": dest_zh, "weapon": wep}
    _json_cache_save(base, GM_CACHE_FILE, _GM_CACHE_VER, wk, data)
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

