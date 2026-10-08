"""Bungie 服务器状态：维护检测 / 维护中拦截查询 / 维护窗口记录

单独成模块的原因：数据层（destiny_data）与授权层（bungie_auth）都要用它，
放在任何一方都会互相 import 成环；本模块只依赖标准库 + httpx。

三层防线（2026-10-08 加，起因：10-06 凌晨维护期间群里查出来一堆错数据）：
1. **事前拦截** `guard()`：查询入口先看全局维护态。维护中直接抛
   BungieMaintenanceError——不打接口、不写缓存、不出「全是 0」的假卡片。
2. **事中判定** `note_json()` / `note_http()`：官方维护时既可能回
   ErrorCode 5 SystemDisabled、1672 DestinyThrottledByGameServer（10-06 实测），
   也可能只回一个 HTML 错误页；命中就点亮维护态（自动中止在跑的后台任务 +
   面板日志提醒），不缓存这次响应。
3. **事后补救**：官方维护还有「HTTP 200 + 空 Response」这种形态——不报错也
   没有数据，静默变成 0（10-06 日志里表现为翻页一直「本次已收 N 场」不动）。
   这种靠 destiny_data 里的补查复核层兜（可疑数据不落缓存、直接报错）。

维护窗口（`maintenance_log.json`）：点亮维护态时窗口起点取 `last_ok`
（最后一次拿到正常响应的时刻，比「发现维护的时刻」更准——维护开始到我们
发现之间查的那些数据同样有问题），窗口结束取探测到恢复的时刻。落在窗口内的
缓存条目一律视为污染，加载时作废。
"""
import datetime
import json
import os
import sys
import threading
import time

import httpx

BASE = "https://www.bungie.net"


class BungieMaintenanceError(RuntimeError):
    """服务器维护/系统关闭：message 是直接给用户看的整段提示"""


# ---------- 错误码表 ----------
# 官方 PlatformErrorCodes 里所有「系统级关闭 / 维护 / 上游不可用」的码。
# 只列系统级的：单个接口自己的业务错误（1620 角色不存在之类）不能停整个机器人。
MAINT_CODES = {
    5: "SystemDisabled",                    # 官方整体关闭（维护的标准码）
    1218: "PSNExSystemDisabled",
    1233: "PsnApiUnderMaintenance",
    1236: "PsnApiProfileUnderMaintenance",
    1300: "XblExSystemDisabled",
    1500: "LegacyGameStatsSystemDisabled",
    1643: "DestinyServiceFailure",
    1644: "DestinyServiceRetired",
    1651: "DestinyShardRelayClientTimeout",  # 维护期游戏服掐链
    1652: "DestinyShardRelayProxyTimeout",
    1672: "DestinyThrottledByGameServer",    # 10-06 实测维护期就是它
    1688: "DestinyDirectBabelClientTimeout",
}
# 纯限流（请求太密）：只让当前这次查询重试/报错，不点亮全局维护态——
# 一次抖动就把所有人的查询停掉太粗暴
THROTTLE_CODES = {
    31: "ThrottleLimitExceeded",
    35: "ThrottleLimitExceededMinutes",
    36: "ThrottleLimitExceededMomentarily",
    37: "ThrottleLimitExceededSeconds",
    51: "PerEndpointRequestThrottleExceeded",
    54: "PerApplicationThrottleExceeded",
    55: "PerApplicationAnonymousThrottleExceeded",
    56: "PerApplicationAuthenticatedThrottleExceeded",
    57: "PerUserThrottleExceeded",
    2004: "TokenThrottling",
}
# 非 JSON 响应里出现这些词 = 官方维护页/风控页（本模块按「维护」处理）
_HTML_HINTS = ("maintenance", "maintain", "down for", "system is unavailable",
               "service unavailable", "temporarily unavailable")

PROBE_TTL = 60          # 维护态下最多每 60 秒探一次官方状态接口
PASS_EVERY = 60         # 维护中每 60 秒放一个真实请求出去探路（官方一恢复就自动解锁）


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v:
        return v
    # 源码运行时 destiny_data 会先把 .env 灌进环境；这里兜一手 exe/面板单独 import 的情况
    for base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
                 os.path.dirname(sys.executable)):
        p = os.path.join(base, ".env")
        if os.path.exists(p):
            try:
                for line in open(p, encoding="utf-8"):
                    k, _, val = line.strip().partition("=")
                    if k == name and val:
                        os.environ.setdefault(k, val)
                        return val
            except Exception:  # noqa: BLE001
                pass
            break
    return default


def _writable_path(name: str) -> str:
    """可写文件位置：打包后放 exe 同目录，源码运行放项目目录（与 destiny_data 同口径）"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------- 全局维护态 ----------
_LOG_FILE = "maintenance_log.json"
_WINDOW_KEEP = 12       # 只留最近 12 个维护窗口，够判定缓存污染了

# 全盘时钟口径：中国北京时间（维护窗口是游戏语义的时间，别跟着本机时区走）
_TZ8 = datetime.timezone(datetime.timedelta(hours=8))


def _cn_hhmm(ts: float, hm_only: bool = False) -> str:
    return datetime.datetime.fromtimestamp(ts, _TZ8).strftime("%H:%M" if hm_only
                                                              else "%m-%d %H:%M")

_lock = threading.Lock()
_state: dict = {
    "on": False,        # 是否处于维护态
    "since": 0.0,       # 本轮维护态点亮时刻
    "start": 0.0,       # 本轮维护窗口起点（≈最后一次正常响应的时刻）
    "until": 0.0,       # 官方给的预计结束时刻（有就显示）
    "kind": "",         # disabled / throttled / html / probe
    "detail": "",       # 官方原文，给日志与面板看
    "last_ok": 0.0,     # 最后一次拿到正常（ErrorCode==1）响应的时刻
    "probe_at": 0.0,    # 上次探测官方状态接口的时刻
    "pass_at": 0.0,     # 上次「放行一个真实请求探路」的时刻
    "pass_n": 0,        # 当前维护态里已放行几次
    "down_time": 0.0,   # 探测到恢复的时刻（窗口终点）
}
_windows: list = []     # [[start, end], ...] 维护窗口，落盘
_loaded = False
_trip_cbs: list = []    # 点亮维护态时回调（destiny_data 注册「中止所有后台任务」）
_clear_cbs: list = []   # 维护结束时回调（调度器注册「立刻补跑本轮预取/推送」）


def on_clear(cb) -> None:
    """注册维护结束回调：调度器用它把「维护期错过的日/周刷新」立刻补上

    没有它的话，维护恰好在日刷新点（每天 1 点 / 每周三 1 点）结束时光尘商店、
    轮换这些缓存要等下一个 30 分钟 tick 才补——用户看到的就是「自动刷新失效了」。"""
    if cb not in _clear_cbs:
        _clear_cbs.append(cb)


def _load_windows():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        d = json.load(open(_writable_path(_LOG_FILE), encoding="utf-8"))
        if isinstance(d, list):
            _windows.extend([[float(a), float(b)] for a, b in d if b])
    except Exception:  # noqa: BLE001 首次运行/文件损坏都按「没有维护史」算
        pass


def _save_windows():
    try:
        tmp = _writable_path(_LOG_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_windows[-_WINDOW_KEEP:], f)
        os.replace(tmp, _writable_path(_LOG_FILE))
    except Exception:  # noqa: BLE001 写不进去只是这次维护窗口没记下，不影响拦截
        pass


def on_trip(cb) -> None:
    """注册维护点亮回调（destiny_data 用它挂「自动中止所有后台任务」）"""
    if cb not in _trip_cbs:
        _trip_cbs.append(cb)


def state() -> dict:
    """当前状态快照（面板/日志用）"""
    with _lock:
        return dict(_state, windows=[list(w) for w in _windows[-_WINDOW_KEEP:]])


def is_down() -> bool:
    return bool(_state["on"])


def note_ok() -> None:
    """每次拿到正常响应都记一笔：维护窗口的起点靠它算（比「发现维护的时刻」准）

    顺带当恢复信号：维护态下放行的那个探测请求如果成功了，这里就把维护态熄掉。"""
    _state["last_ok"] = time.time()
    if _state["on"]:
        clear("真实请求已恢复正常")


# ---------- 判定 ----------
def classify_json(d: dict) -> tuple[str, str] | None:
    """解析好的响应体 → 维护类问题则给 ("disabled"/"throttled", 官方原文)"""
    if not isinstance(d, dict):
        return None
    try:
        code = int(d.get("ErrorCode") or 0)
    except (TypeError, ValueError):
        return None
    status = str(d.get("ErrorStatus") or "")
    msg = str(d.get("Message") or "").strip()
    if code in MAINT_CODES or status in ("SystemDisabled",) or status in MAINT_CODES.values():
        return "disabled", f"{status or MAINT_CODES.get(code, code)} {msg}".strip()
    if code in THROTTLE_CODES:
        return "throttled", f"{status or THROTTLE_CODES.get(code, code)} {msg}".strip()
    return None


def note_json(d: dict) -> tuple[str, str] | None:
    """数据层每个响应都过一遍：命中就点亮维护态并返回判定结果"""
    hit = classify_json(d)
    if hit:
        trip(*hit)
    return hit


def note_http(status_code: int, body: bytes | str) -> tuple[str, str] | None:
    """HTTP 层面的判定：维护时官方会直接回 503 / 一个 HTML 维护页"""
    txt = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    head = txt.lstrip()[:200].lower()
    is_html = head.startswith("<!doctype") or head.startswith("<html") or "<body" in head
    if is_html:
        low = txt[:4000].lower()
        if any(h in low for h in _HTML_HINTS) or status_code >= 500:
            return trip("html", f"官方返回 HTML 错误页（HTTP {status_code}）")
        return None
    if status_code in (503, 504):
        return trip("html", f"官方返回 HTTP {status_code}")
    return None


def classify_throttle_body(txt: str) -> tuple[str, str] | None:
    """OAuth 换 token 那种非标准响应体：{"error":"server_error","error_description":"DestinyThrottledByGameServer"}"""
    if "DestinyThrottledByGameServer" in txt or "SystemDisabled" in txt:
        return "disabled", txt.strip()[:160]
    return None


# ---------- 点亮 / 熄灭 ----------
def trip(kind: str, detail: str = "") -> tuple[str, str]:
    """点亮维护态（幂等）。若刚从非维护态进来，通知所有回调「自动中止在跑的任务」"""
    with _lock:
        first = not _state["on"]
        if first:
            _load_windows()
            start = _state["last_ok"] or time.time()
            # 窗口起点不能早于上一次维护窗口的终点（否则重复覆盖同一段时间）
            if _windows and _windows[-1][1] and _windows[-1][1] > start:
                start = _windows[-1][1]
            _state.update(on=True, since=time.time(), start=start, down_time=0.0,
                          pass_at=0.0, pass_n=0, kind=kind, detail=detail)
            print(f"[维护] {time.strftime('%H:%M:%S')} ⛔ 检测到 Bungie 服务器维护/关闭"
                  f"（{kind} · {detail or '未给出原因'}），已暂停所有查询", flush=True)
        elif kind != "throttled" or _state["kind"] != "disabled":
            # 限流类判定不覆盖更严重的「官方关闭」判定
            _state.update(kind=kind, detail=detail or _state["detail"])
    if first and kind != "throttled":
        for cb in list(_trip_cbs):
            try:
                cb(detail)
            except Exception as exc:  # noqa: BLE001 回调炸了不能连带把数据层带崩
                print(f"[维护] 中止回调失败：{type(exc).__name__}: {exc}", flush=True)
    return kind, detail


def clear(reason: str = "") -> None:
    """维护结束：关掉窗口、清响应缓存（维护期的脏响应可能还在里面）、放行查询"""
    with _lock:
        if not _state["on"]:
            return
        end = time.time()
        span, kind = "", _state["kind"]
        if kind != "throttled" and _state["start"] and end > _state["start"]:
            _load_windows()
            _windows.append([_state["start"], end])
            del _windows[:-_WINDOW_KEEP]
            _save_windows()
            span = (f"维护窗口 {_cn_hhmm(_state['start'])}"
                    f" ~ {_cn_hhmm(end, hm_only=True)}，窗口内缓存已标记作废")
        _state.update(on=False, down_time=end, kind="", detail="", last_ok=end)
        head = ("Bungie 服务器已恢复" if kind != "throttled" else "接口限流已解除")
        print(f"[维护] {time.strftime('%H:%M:%S')} ✅ {head}"
              f"（{reason or '状态正常'}）{span}", flush=True)
    if kind != "throttled":
        for cb in list(_clear_cbs):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001 回调炸了不能连带把状态机带崩
                print(f"[维护] 恢复回调失败：{type(exc).__name__}: {exc}", flush=True)


# ---------- 状态探测 ----------
async def refresh() -> bool:
    """探官方状态接口（/Platform/Settings/ 的 systems.Destiny2.enabled）→ 仍在维护返回 True

    **不用它判定「恢复」**：官方是分级维护——游戏服挂了（接口回 1672/SystemDisabled）
    时状态接口往往照样正常，拿「状态接口 OK」当恢复信号会立刻把刚点亮的维护态抹掉、
    然后下一个真实请求又炸，反复横跳（2026-10-08 测试实测到这个问题）。
    它的用途只有两个：
      · 确认维护 + 拿一句官方原文（比只记「ErrorCode 5」有用）
      · 状态接口自己都正常时，把 pass_at 归零 → 下一个真实请求立刻放行去「证实」恢复
    真正可信的恢复信号只有一个：真实请求成功了（数据层 note_ok → clear）。
    """
    key = _env("BUNGIE_API_KEY")
    headers = {"X-API-Key": key} if key else {}
    with _lock:
        _state["probe_at"] = time.time()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(BASE + "/Platform/Settings/", headers=headers)
    except Exception as exc:  # noqa: BLE001 探测失败保持现状
        print(f"[维护] 状态探测失败（保持现有判定）：{type(exc).__name__}: {exc}", flush=True)
        return is_down()
    if r.status_code in (503, 504):
        trip("html", f"状态接口 HTTP {r.status_code}")
        return True
    try:
        d = r.json()
    except ValueError:
        trip("html", "状态接口返回非 JSON（维护页）")
        return True
    if hit := classify_json(d):
        trip(*hit)
        return True
    resp = d.get("Response") if isinstance(d.get("Response"), dict) else {}
    sys_ = resp.get("systems")
    if isinstance(sys_, dict) and sys_:
        # 权威开关：官方整体维护/关闭时会把这几项翻成 false（Destiny2 是总开关）
        off = [k for k in ("Destiny2", "D2Profiles", "D2Characters", "D2Vendors")
               if isinstance(sys_.get(k), dict) and sys_[k].get("enabled") is False]
        if off:
            trip("disabled", "官方状态接口：" + "、".join(f"{k}=disabled" for k in off))
            return True
    if is_down():
        # 状态接口正常但游戏服可能还在维护：让下一个真实请求马上去证实
        with _lock:
            _state["pass_at"] = 0.0
        print("[维护] 官方状态接口正常，放行一个真实请求去核实是否真的恢复", flush=True)
    return is_down()


async def guard() -> None:
    """查询入口/每次 API 请求前的闸门：维护中直接拦，不打接口也不写缓存

    非维护态是纯内存读（零开销）；维护态下每 PROBE_TTL 探一次官方状态接口
    （确认维护/拿官方原文），恢复则靠 PASS_EVERY 放行的那次真实请求证实。
    """
    if not _state["on"]:
        return
    if time.time() - _state["probe_at"] >= PROBE_TTL:
        await refresh()
        if not _state["on"]:
            return
    raise_if_down()


def guard_sync() -> None:
    """同步版闸门（任务翻页检查点用）：维护中抛错让任务就地中止"""
    raise_if_down()


def raise_if_down() -> None:
    if not _state["on"]:
        return
    now = time.time()
    # 维护久了不能一直死锁：每隔 PASS_EVERY 放一个真实请求出去探路，
    # 它若成功（ErrorCode==1）数据层会 note_ok 并触发恢复；失败就继续拦。
    # 放行频率由时间兜住（每分钟最多一个），不会刷接口。
    if now - _state["pass_at"] >= PASS_EVERY:
        with _lock:
            _state["pass_at"] = now
            _state["pass_n"] += 1
        print(f"[维护] 放行一次探测请求（本轮第 {_state['pass_n']} 次，"
              f"失败会继续拦截）", flush=True)
        return
    raise BungieMaintenanceError(text())


def text() -> str:
    """给用户看的一整段维护提示（QQ 卡片与面板共用同一份口径）"""
    st = _state
    since = st["since"] or st["start"]
    used = ""
    if since:
        m = int((time.time() - since) // 60)
        used = f"（已持续 {m // 60} 小时 {m % 60} 分）" if m >= 60 else f"（已持续 {m} 分钟）"
    head = "Bungie 服务器正在维护" if st["kind"] != "throttled" else "Bungie 服务器繁忙（接口限流）"
    tail = ("维护期间所有查询都用不了，官方恢复后重发一次即可。"
            if st["kind"] != "throttled" else "稍等一两分钟再重发即可。")
    return f"{head}{used}：{tail}"


# ---------- 维护窗口 & 缓存污染判定 ----------
def windows() -> list:
    _load_windows()
    with _lock:
        return [list(w) for w in _windows]


def suspect_at(ts: float) -> bool:
    """这个时刻写入的缓存是否落在某次维护窗口内（=可能被维护污染）"""
    if not ts:
        return False
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return False
    for a, b in windows():
        if a and b and a <= ts <= b:
            return True
    # 维护还没结束（窗口尚未闭合）时写的东西同样可疑；纯限流不算——
    # 那只是请求太密，数据本身没问题，按污染作废会导致反复重拉
    if _state["on"] and _state["kind"] != "throttled" \
            and _state["start"] and ts >= _state["start"]:
        return True
    return False


def suspect_stamp(stamp: str) -> bool:
    """同上，但入参是 "%Y-%m-%d %H:%M:%S" 文本时间戳"""
    if not stamp:
        return False
    try:
        return suspect_at(time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")))
    except Exception:  # noqa: BLE001 格式对不上就当不可疑
        return False


def clean_since() -> float:
    """最近一次维护窗口的终点：早于它写的缓存一律重新核对（没有维护史则 0）"""
    ws = windows()
    return ws[-1][1] if ws else 0.0
