# -*- coding: utf-8 -*-
"""武器社区使用率数据管线：给武器卡片提供 light.gg 风格的大众选取率（pct%）。

对外接口
--------
``await get_usage(item_hash) -> dict | None``
任何异常都在这里吞掉（返回 None），绝不抛到指令层。返回契约（bot_cards.weapon_card
的 usage 参数）::

    {
      "fetched_at": "2026-10-01", "source": "snapshot|endpoint|cdp|d2foundry|lightgg",
      "cols": [ {"title": "发射管", "plugs": [{"hash":123,"name":"高爆弹药",
                "icon":"https://...","pct":24.0}, ...]}, ... ],   # 列数可少，plug 按 pct 降序
      "masterworks": [{"name":...,"icon":...,"pct":...}, ...],    # 可为 []
      "mods":        [{"name":...,"icon":...,"pct":...}, ...],    # 可为 []
      "combos":      [{"names":["回转弹药","诱导推销"],"icons":[..,..],"pct":20.4}, ...]  # 最多8
    }
combos 是「特性1 x 特性2」热门组合：light.gg 有真实组合统计（trait-combos）时直接用真实
pct，没有时退化为两列 pct 的独立乘积估算（卡片页脚会注明非精确概率）。

提供者链（按序，命中即回）
--------------------------
1. 本地快照 manifest_index/weapon_usage_snapshot.json（可能不存在，不存在则跳过）；
2. 自定义端点：GET {endpoint}/{item_hash}，返回体是完整契约直接用，否则当作 raw 数据解析；
3. CDP 接入用户已验证浏览器（**无需任何配置**）：探测 http://127.0.0.1:9222/json/version
   （3s 超时，不通立即跳过），通了就 connect_over_cdp 挂进用户真实浏览器会话
   （独立调试 profile + --remote-debugging-port=9222；新版 Edge 对默认配置目录忽略调试端口，
   所以必须用独立 profile），在其里开 light.gg 页面等 Turnstile 放行后取
   HTML —— 用户浏览器已人工过 Cloudflare 验证，这是唯一能稳定穿过 light.gg 防线的通道；
   失败进 10 分钟冷却，避免每条指令都去连；
4. d2foundry.gg/w/{hash}（httpx + Edge UA；503/超时跳过；防御式解析页面内嵌 JSON）；
5. light.gg（**仅当配置了 proxy** 才尝试）：先 httpx 走代理抓，失败再用 Playwright
   launch(channel="msedge", proxy=...) 抓；防御式解析（内嵌 JSON → DOM 启发式），
   解析失败把原始 HTML 落盘到 cwd/logs/usage_debug/ 便于日后调解析器。

配置方法
--------
- CDP 通道（提供者③）：零配置。只要 Edge 带 --remote-debugging-port=9222 开着即可
  （标签页自动恢复、登录态/验证状态全保留）。没开也不用管：面板点「全库刷新」会调用
  ``ensure_channel()`` 用独立调试 profile 自己起一个 Edge 实例（与用户正在用的 Edge 并存，
  不关人家窗口），起不来才需要手动双击 start_edge_debug.bat 兜底。
- cwd 下 ``usage_config.json``：``{"endpoint": "https://host/api/usage", "proxy": "http://127.0.0.1:7890"}``
  （endpoint 优先级高于环境变量；proxy 同理；两字段均可省略）
- 环境变量：``D2_USAGE_ENDPOINT`` / ``D2_USAGE_PROXY``
- 快照批量生成：CDP 模式 ``python build_weapon_usage.py --via cdp``（推荐，需 Edge 调试端口）；
  代理模式 ``python build_weapon_usage.py --proxy http://127.0.0.1:7890 [--limit N] [--hashes h1,h2]``

缓存与节流
----------
- cwd/weapon_usage_cache.json：{hash: {"ts": iso, "data": {...}}}，数据 TTL 7 天；
- 抓取失败写负缓存（{"ts": iso, "neg": true}）TTL 24h，期间直接返回 None 不发外网请求；
- 同 hash 并发单飞（asyncio.Lock），全局节流每分钟 <= 10 次真实外网请求；
- 缓存/快照/配置文件读写全部 try/except 容错，坏文件当不存在。

列标题口径
----------
固定中文，按 socket 顺序对齐：发射管/弹匣/特性 1/特性 2/起源特性（缺列则少一列，
异域武器没有起源特性列也能处理）。若 weapons_full 里该武器的列标题是特殊槽位
（瞄具/剑刃/护手/弓弦/箭矢/固定配件），保留其专名，其余归一为上面的固定标题。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque

import httpx

import name_i18n

__all__ = ["get_usage", "parse_lightgg_html", "harvest_pct_lists", "pw_fetch_html"]

# ---------------------------------------------------------------- 基础设施

TTL_DATA = 7 * 86400
TTL_NEG = 86400
RATE_MAX = 10          # 每分钟真实外网请求上限
RATE_WINDOW = 60.0
EDGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
_HTTP_TIMEOUT = httpx.Timeout(12.0, connect=5.0)

_PCT_KEYS = {"percentage", "pct", "popularity", "useRate", "usageRate", "usage",
             "equippedPercent", "popularityPercent", "pickRate", "pickrate", "percent"}
_HASH_KEYS = {"hash", "plugHash", "perkHash", "itemHash", "item_id", "id"}


_TZ8 = datetime.timezone(datetime.timedelta(hours=8))     # 全盘时钟口径：中国北京时间


def _now_iso() -> str:
    """带 +08 偏移的 ISO 时间：`[:10]` 就是北京日期，`fromisoformat().timestamp()` 也准"""
    return datetime.datetime.now(_TZ8).isoformat(timespec="seconds")


def _idx_file(name: str) -> str:
    """manifest_index 定位：源码目录 → PyInstaller 打包资源 → cwd（同 destiny_data.py）"""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest_index", name)
    if os.path.exists(here):
        return here
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "manifest_index", name)
    if os.path.exists(bundled):
        return bundled
    return os.path.join("manifest_index", name)


def _snap_path() -> str:
    """weapon_usage_snapshot.json 的读路径：cwd 副本优先（后台全库刷新的运行时产物，
    打包版 exe 的 _MEIPASS 捆绑副本是构建时旧数据，必须能被它盖过），否则常规定位。"""
    local = os.path.join("manifest_index", "weapon_usage_snapshot.json")
    if os.path.exists(local):
        return local
    return _idx_file("weapon_usage_snapshot.json")


def _snap_write_path() -> str:
    """快照写路径：永远写 cwd 下的 manifest_index（源码跑 = 仓库目录；exe 跑 = exe 旁）。"""
    d = os.path.join("manifest_index", "")
    if not os.path.isdir(d):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return os.path.join(d, "weapon_usage_snapshot.json")


def _cwd_file(name: str) -> str:
    return os.path.join(os.getcwd(), name)


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _dump_json(path: str, data) -> bool:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 缓存 / 节流 / 单飞

_cache_lock = asyncio.Lock()
_net_lock = asyncio.Lock()
_net_times: deque[float] = deque()
_inflight: dict[int, asyncio.Lock] = {}


async def _rate_limit() -> None:
    """全局节流：每分钟 <= RATE_MAX 次真实外网请求（在请求发出前 await）。"""
    while True:
        async with _net_lock:
            now = time.monotonic()
            while _net_times and now - _net_times[0] > RATE_WINDOW:
                _net_times.popleft()
            if len(_net_times) < RATE_MAX:
                _net_times.append(now)
                return
            wait = RATE_WINDOW - (now - _net_times[0]) + 0.05
        await asyncio.sleep(wait)


def _cache_load() -> dict:
    return _load_json(_cwd_file("weapon_usage_cache.json"), {}) or {}


async def _cache_store(cache: dict) -> None:
    async with _cache_lock:
        _dump_json(_cwd_file("weapon_usage_cache.json"), cache)


def _cache_get(cache: dict, h: int, force: bool):
    """返回 ('hit', data) / ('neg', None) / ('miss', None)"""
    if force:
        return "miss", None
    e = cache.get(str(h))
    if not isinstance(e, dict):
        return "miss", None
    ts = e.get("ts")
    try:
        age = time.time() - datetime.datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return "miss", None
    if e.get("neg"):
        return ("neg", None) if age < TTL_NEG else ("miss", None)
    if age < TTL_DATA and isinstance(e.get("data"), dict):
        return "hit", e["data"]
    return "miss", None


# ---------------------------------------------------------------- 配置

def _load_cfg() -> dict:
    cfg = _load_json(_cwd_file("usage_config.json"), {}) or {}
    out = {"endpoint": str(cfg.get("endpoint") or "").strip() or None,
           "proxy": str(cfg.get("proxy") or "").strip() or None}
    out["endpoint"] = out["endpoint"] or os.environ.get("D2_USAGE_ENDPOINT") or None
    out["proxy"] = out["proxy"] or os.environ.get("D2_USAGE_PROXY") or None
    return out


# ---------------------------------------------------------------- 中文名 / 图标 join

_wf_pools: dict | None = None      # {hash: {"cols": [(title, set(hashes))...], "mw": set, "mods": set}}
_plug_names: dict | None = None    # {hash(str): {"name","icon"}}
_icon_map: dict | None = None      # {icon文件名: hash(str)}


def _load_weapons_full():
    return _load_json(_idx_file("weapons_full.json"), {}) or {}


def _wf(w_hash: int) -> dict | None:
    return _WEAPONS_FULL.get(str(w_hash))


# 惰性初始化的全局索引（首用时加载，加载失败按空处理）
_WEAPONS_FULL: dict = {}


def _ensure_indexes():
    global _wf_pools, _plug_names, _icon_map, _WEAPONS_FULL
    if _wf_pools is not None:
        return
    _wf_pools = {}
    try:
        _WEAPONS_FULL = _load_weapons_full()
    except Exception:  # noqa: BLE001
        _WEAPONS_FULL = {}
    names: dict[str, dict] = {}
    icons: dict[str, str] = {}

    def take(o):
        h = str(o.get("hash") or "")
        if not h:
            return
        if o.get("n") and h not in names:
            names[h] = {"name": o["n"], "icon": o.get("i") or ""}
        ic = o.get("i") or ""
        if ic and h not in icons:
            icons[ic.rsplit("/", 1)[-1].lower()] = h

    for h, w in _WEAPONS_FULL.items():
        p = (w.get("plugs") or {})
        cols = []
        for c in p.get("cols") or []:
            hs = {str(i.get("hash")) for i in (c.get("items") or []) if i.get("hash")}
            for i in (c.get("items") or []):
                take(i)
            cols.append((c.get("t") or "", hs))
        mw = {str(i.get("hash")) for i in (p.get("masterworks") or []) if i.get("hash")}
        mods = {str(i.get("hash")) for i in (p.get("mods") or []) if i.get("hash")}
        for lst in (p.get("masterworks") or []), (p.get("mods") or []):
            for i in lst:
                take(i)
        _wf_pools[h] = {"cols": cols, "mw": mw, "mods": mods}
    _plug_names = names
    try:  # 兜底词典 plug_meta.json（build_plug_meta.py 产物，可能不存在）
        meta = _load_json(_idx_file("plug_meta.json"), {}) or {}
        for h, v in meta.items():
            if h not in names and isinstance(v, dict) and v.get("name"):
                names[h] = {"name": v["name"], "icon": v.get("icon") or ""}
                ic = v.get("icon") or ""
                if ic:
                    icons.setdefault(ic.rsplit("/", 1)[-1].lower(), h)
    except Exception:  # noqa: BLE001
        pass
    _icon_map = icons


def _join_plug(h) -> dict | None:
    """hash → {name, icon}（中文，宁缺毋滥：解析不到返回 None 由调用方丢弃该行）"""
    _ensure_indexes()
    rec = (_plug_names or {}).get(str(h))
    if rec and rec.get("name") and rec.get("icon"):
        return {"name": rec["name"], "icon": rec["icon"]}
    return None


def _col_title(orig_title: str, pos: int) -> str:
    """weapons_full 列标题 → 卡片固定中文标题（其余专名原样保留）"""
    t = (orig_title or "").strip()
    if t:
        if "起源" in t:
            return "起源特性"
        if "特性" in t:                    # 特性 1/特性 2 已是最终名
            return t
        if "枪管" in t or "发射" in t:
            return "发射管"
        if "弹匣" in t or "电池" in t:
            return "弹匣"
        return t                           # 剑刃/护手/握把/弓弦/瞄具/枪托/核心强化… 直接用本地列名
    return ["发射管", "弹匣", "特性 1", "特性 2", "起源特性"][pos if pos < 5 else 4]


# ---------------------------------------------------------------- raw 解析

class _ParseFail(Exception):
    pass


def _to_hash(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and 0 < v < 2 ** 32:
        return v
    if isinstance(v, str) and v.isdigit() and 4 <= len(v) <= 10:
        return int(v)
    return None


def _to_pct(v) -> float | None:
    if isinstance(v, str):
        m = re.match(r"\s*(\d{1,3}(?:\.\d+)?)\s*%", v)
        if m:
            v = float(m.group(1))
        else:
            return None
    if isinstance(v, (int, float)) and 0 < float(v) <= 100:
        return round(float(v), 2)
    return None


def _pair_from_dict(d: dict):
    """单个 dict 里同时有 hash 类键与 pct 类键 → (hash, pct)"""
    h = p = None
    for k, v in d.items():
        if h is None and k in _HASH_KEYS:
            h = _to_hash(v)
        elif p is None and k in _PCT_KEYS:
            p = _to_pct(v)
        if h and p:
            return h, p
    return None


def harvest_pct_lists(obj, out_lists: list | None = None):
    """递归遍历内嵌 JSON，收集 (hash, pct) 成对数据。

    返回 (pairs, col_lists)：pairs 为文档序去重 flat 列表，
    col_lists 为「每个元素都能配对」的列表（近似 socket 顺序，供列切分）。
    """
    pairs, lists = [], out_lists if out_lists is not None else []

    def walk(o, key=None):
        if isinstance(o, dict):
            got = _pair_from_dict(o)
            if not got and key is not None:      # {"123456": {"percentage": 24}} 形态
                h = _to_hash(key)
                p = None
                for k, v in o.items():
                    if k in _PCT_KEYS and p is None:
                        p = _to_pct(v)
                if h and p:
                    got = (h, p)
            if got:
                pairs.append(got)
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            sub: list = []

            def collect(e):
                got = _pair_from_dict(e) if isinstance(e, dict) else None
                if got:
                    sub.append(got)
                return got is not None

            if o and all(isinstance(e, (dict, list)) for e in o) and \
               sum(1 for e in o if collect(e)) >= 2 and len(sub) == len(o):
                lists.append(list(sub))  # 整列都能配对 → 列候选
            for v in o:
                walk(v, key)

    walk(obj)
    dedup = {}
    for h, p in pairs:
        dedup[h] = max(dedup.get(h, 0.0), p)
    return list(dedup.items()), lists


_ICON_RE = re.compile(r"icons/([0-9a-f]{32})\.(?:png|jpg)", re.I)
_ITEM_LINK_RE = re.compile(r"/db/items/(\d{4,10})")
# 注意：light.gg 有省略前导零的写法（".88% of Rolls" = 0.88%），必须允许 ".88" 形态，
# 再用 _pct_num 补零。此前用 (\d{1,3}...) 会把 .88% 匹配成 88%，冷门组合被放大 100 倍
# 排到榜首（2026-10-07 散射信号「丰盈满溢+柔缓 85%」实为 0.85% 实证）。
_NUM_PCT_RE = re.compile(r"(\d*\.?\d+)\s*%")

# ---- light.gg 真实页面结构（按 _lgg_real.html 校准；别的 ul 都带 enhanced/random 等附加类）----
_LGG_UL_COLS = re.compile(r'<ul class="list-unstyled sockets">(.*?)</ul>', re.S)
_LGG_LI = re.compile(r"<li>(.*?)</li>", re.S)
_LGG_PERCENT = re.compile(r'class="percent">\s*([\d.]+)\s*%')
_LGG_DATA_ID = re.compile(r'data-id="(\d+)"')
_LGG_COMBO_BLOCK = re.compile(r'class="perk-container.*?class="combo-percent">(.*?)</div>', re.S)


def _pct_num(s: str) -> float:
    """'28.9%' / '.0%' → 28.9 / 0.0（light.gg 有省略前导 0 的写法）"""
    s = (s or "").strip()
    return round(float("0" + s if s.startswith(".") else s), 2)


def _parse_lightgg_dom(html: str) -> dict | None:
    """light.gg 服务端直出结构 → raw dict；不是 light.gg 页面（无标记）返回 None。

    - <div id="community-average">：每列一个 <ul class="list-unstyled sockets">，
      每个 <li> = percent + data-id(hash)；列序=发射管/弹匣/特性1/特性2/起源；
    - <div id="trait-combos">：真实组合 pct（17.42% of Rolls 这种），直接透传；
    - <div id="masterwork-stats">：大师杰作统计，全 0 就丢弃留空，不硬凑。
    """
    ca = html.find('id="community-average"')
    if ca < 0:
        return None
    cols: list[list[tuple[int, float]]] = []
    for m in _LGG_UL_COLS.finditer(html, ca):        # pos=ca：只认 community-average 之后的列
        col = []
        for li in _LGG_LI.finditer(m.group(1)):
            pm, hm = _LGG_PERCENT.search(li.group(1)), _LGG_DATA_ID.search(li.group(1))
            if pm and hm:
                p = _pct_num(pm.group(1))
                if p > 0:
                    col.append((int(hm.group(1)), p))
        if col:
            cols.append(col)
    if not cols:
        return None
    raw: dict = {"cols": cols,
                 "plugs": [p for c in cols for p in c],
                 "mw": [], "mods": [], "combos": []}
    tc = html.find('id="trait-combos"')
    if tc >= 0:
        seg = html[tc:ca] if ca > tc else html[tc:tc + 200000]
        for m in _LGG_COMBO_BLOCK.finditer(seg):
            hashes = _LGG_DATA_ID.findall(m.group(0))
            pm = _NUM_PCT_RE.search(m.group(1))
            if len(hashes) == 2 and pm:
                p = _pct_num(pm.group(1))
                if p > 0:
                    raw["combos"].append((int(hashes[0]), int(hashes[1]), p))
        raw["combos"].sort(key=lambda c: -c[2])
        raw["combos"] = raw["combos"][:8]
    ms = html.find('id="masterwork-stats"')
    if ms >= 0:
        for m in _LGG_COMBO_BLOCK.finditer(html, ms, ms + 100000):
            hashes = _LGG_DATA_ID.findall(m.group(0))
            pm = _NUM_PCT_RE.search(m.group(1))
            if len(hashes) == 1 and pm:
                p = _pct_num(pm.group(1))
                if p > 0:
                    raw["mw"].append((int(hashes[0]), p))
    return raw


def parse_lightgg_html(html: str) -> dict:
    """light.gg / d2foundry 页面 → raw dict，失败抛 _ParseFail。

    策略：① light.gg 真实 DOM 结构（community-average/trait-combos/masterwork-stats，
    带 cols 列序与真实 combos）；② 页面内嵌 JSON（d2foundry 等，<script> 里的可解析块，
    找 hash+pct 成对数据）；③ DOM 启发式：bungie 图标 / /db/items/{hash} 链接附近找百分比。
    raw 格式：{"cols": [[(hash,pct),...],...]（可无）, "plugs": [(hash,pct),...],
    "mw": [...], "mods": [...], "combos": [(hash1,hash2,pct),...]（可无）}
    """
    _ensure_indexes()
    # ① light.gg 真实结构（服务端直出，最准）
    raw = _parse_lightgg_dom(html)
    if raw:
        return raw
    # ② 内嵌 JSON（容忍 var x = {...}; 之类 JS 包裹：取首 { 到尾 } 再试）
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S | re.I):
        body = m.group(1) or ""
        if len(body) < 40 or ("{" not in body and "[" not in body):
            continue
        candidates = [body.strip()]
        b1, b2 = body.find("{"), body.rfind("}")
        if 0 <= b1 < b2:
            candidates.append(body[b1:b2 + 1])
        b1, b2 = body.find("["), body.rfind("]")
        if 0 <= b1 < b2:
            candidates.append(body[b1:b2 + 1])
        for cand in candidates:
            try:
                data = json.loads(cand)
            except Exception:  # noqa: BLE001
                continue
            pairs, col_lists = harvest_pct_lists(data)
            if pairs:
                return {"cols": col_lists[:5] if len(col_lists) >= 2 else None, "plugs": pairs,
                        "mw": [], "mods": []}
    # ③ DOM 启发式：pct 往回找最近的 item hash / bungie 图标
    pairs: dict[int, float] = {}
    for m in _NUM_PCT_RE.finditer(html):
        try:
            pct = _pct_num(m.group(1))
        except ValueError:  # noqa: BLE001
            continue
        if not (0 < pct <= 100):
            continue
        window = html[max(0, m.start() - 600):m.start()]
        h = None
        links = _ITEM_LINK_RE.findall(window)
        if links:
            h = _to_hash(links[-1])
        if not h:
            icons = _ICON_RE.findall(window)
            if icons and _icon_map:
                h = _to_hash(_icon_map.get(icons[-1] + ".png") or _icon_map.get(icons[-1] + ".jpg")
                             or _icon_map.get(icons[-1]))
        if h:
            pairs[h] = max(pairs.get(h, 0.0), pct)
    if pairs:
        return {"plugs": sorted(pairs.items()), "mw": [], "mods": []}
    raise _ParseFail("no perk pct found in html")


def _raw_from_payload(payload) -> dict:
    """端点返回体 → raw dict（完整契约 / 我们的 raw 格式 / 任意 JSON / HTML 文本）"""
    if isinstance(payload, str):
        return parse_lightgg_html(payload)
    if not isinstance(payload, dict):
        raise _ParseFail("endpoint payload not dict")
    # 完整契约（有中文 cols）直接标走
    cols = payload.get("cols")
    if isinstance(cols, list) and cols and isinstance(cols[0], dict) and "plugs" in cols[0]:
        return {"contract": payload}
    # 我们自己的 raw 格式 / 端点简化格式：cols=[[{hash,pct}..]..] 或 plugs=[{hash,pct}..]
    if isinstance(cols, list) and cols and isinstance(cols[0], (list, dict)):
        norm = []
        for c in cols:
            c = c if isinstance(c, list) else [c]
            prs = [(hh, pp) for hh, pp in (_pair_from_dict(e) for e in c if isinstance(e, dict)) if hh]
            if prs:
                norm.append(prs)
        if norm:
            return {"cols": norm, "mw": [], "mods": []}
    pairs, col_lists = harvest_pct_lists(payload)
    if pairs or col_lists:
        return {"cols": col_lists[:5] if len(col_lists) >= 2 else None,
                "plugs": pairs or [], "mw": [], "mods": []}
    raise _ParseFail("endpoint payload unparsable")


# ---------------------------------------------------------------- 提供者

async def _fetch_endpoint(cfg: dict, h: int) -> dict | None:
    if not cfg.get("endpoint"):
        return None
    await _rate_limit()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers={"User-Agent": EDGE_UA},
                                     proxy=cfg.get("proxy"), follow_redirects=True) as cli:
            r = await cli.get(f"{cfg['endpoint'].rstrip('/')}/{h}")
            if r.status_code != 200:
                return None
            return _raw_from_payload(r.json())
    except Exception:  # noqa: BLE001
        return None


async def _fetch_d2foundry(h: int) -> dict | None:
    await _rate_limit()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers={"User-Agent": EDGE_UA},
                                     follow_redirects=True) as cli:
            r = await cli.get(f"https://d2foundry.gg/w/{h}")
            if r.status_code != 200 or len(r.text) < 5000:
                return None
            return parse_lightgg_html(r.text)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------- ③ CDP：接入用户已验证的浏览器

CDP_URL = "http://127.0.0.1:9222"   # start_edge_debug.bat 以 --remote-debugging-port=9222 重启 Edge
CDP_PROBE_TIMEOUT = 3.0              # 端口探测超时（秒），不通秒回 None
CDP_CONNECT_TIMEOUT_MS = 8000        # connect_over_cdp 超时
CDP_GOTO_TIMEOUT_MS = 30000          # 单页 goto 超时
CDP_CHALLENGE_WAIT = 25.0            # 等 Turnstile 挑战消失的最长时间（秒）
_CDP_FAIL_TTL = 600.0                # CDP 失败后的冷却（秒）：期间不再探测，避免每条指令都去连
_CHALLENGE_TITLE = ("Just a moment", "请稍候", "Attention Required")   # 挑战页标题特征
_cdp_fail_until = 0.0                # 冷却截止（time.monotonic()）


async def _cdp_probe() -> bool:
    """探测 127.0.0.1:9222 有没有 CDP 服务（用户浏览器带调试端口开着才通）。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CDP_PROBE_TIMEOUT),
                                     trust_env=False) as cli:  # 本地端口，绝不能走系统代理
            r = await cli.get(f"{CDP_URL}/json/version")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------- 通道自启：面板点刷新时自己把调试 Edge 拉起来
#
# 9222 上没通道时不再只丢一句报错给用户：用**独立调试 profile** 再起一个 Edge 实例
# （与用户正在用的 Edge 两个进程并存，实测 2 秒端口就绪，全程不 taskkill、不关人家窗口），
# 端口就绪后照旧走 CDP。profile 默认沿用 F:\edge_debug_profile——cf_clearance 就在里面，
# 拉起后通常不用重新过人机验证。只有连 msedge.exe 都找不到才当场报错，让用户走
# start_edge_debug.bat 兜底。
CDP_PORT = 9222                      # 与 CDP_URL 同源：--remote-debugging-port 的值
CDP_PROFILE_ENV = "D2_EDGE_DEBUG_PROFILE"        # 想换调试 profile 目录就设这个环境变量
CDP_PROFILE_DEFAULT = r"F:\edge_debug_profile"   # 与 start_edge_debug.bat 同一个目录
CDP_BOOT_WAIT = 45.0                 # 拉起后等 9222 就绪的最长秒数


def _find_edge_exe() -> str | None:
    """定位 msedge.exe：注册表 App Paths（最权威）→ 常见安装目录 → PATH。"""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                p = winreg.QueryValueEx(k, "")[0]
            if p and os.path.exists(p):
                return p
        except Exception:  # noqa: BLE001
            pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(p):
                return p
    return shutil.which("msedge")


def _debug_profile_dir() -> str:
    """调试 profile 目录：环境变量 > start_edge_debug.bat 里写的值 > F:\edge_debug_profile。

    必须沿用同一个目录：cf_clearance 存在里面，换目录等于每次都要重新过人机验证。"""
    env = (os.environ.get(CDP_PROFILE_ENV) or "").strip()
    if env:
        return env
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__)),
                 os.path.dirname(sys.executable)):
        try:
            with open(os.path.join(base, "start_edge_debug.bat"), encoding="utf-8",
                      errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        m = re.search(r'set\s+"EDGE_PROFILE=([^"]*)"', txt, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
    if os.path.isdir(CDP_PROFILE_DEFAULT):
        return CDP_PROFILE_DEFAULT
    return os.path.join(os.getcwd(), "edge_debug_profile")


def _spawn_flags() -> int:
    """子进程与本体脱钩：面板/本体关掉重启，调试 Edge 继续开着，下次直接复用。"""
    if os.name != "nt":
        return 0
    return (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


async def ensure_channel(patience_s: float = CDP_BOOT_WAIT, cancel=None) -> dict:
    """确保 127.0.0.1:9222 上有可用的调试 Edge：已在→直接回；不在→起一个再等端口。

    返回 {"ok": bool, "started": bool, "detail": str, "cancelled"?: bool}；
    **只新增一个 Edge 实例（独立 user-data-dir），绝不关用户正在用的 Edge**。"""
    global _cdp_fail_until
    if await _cdp_probe():
        _cdp_fail_until = 0.0          # 通道回来了：顺手解除 CDP 冷却，指令立刻能再走 CDP
        return {"ok": True, "started": False, "detail": "调试 Edge 已在运行"}
    exe = _find_edge_exe()
    if not exe:
        return {"ok": False, "started": False,
                "detail": "找不到 msedge.exe；请手动双击 start_edge_debug.bat 后再刷新"}
    profile = _debug_profile_dir()
    try:
        os.makedirs(profile, exist_ok=True)
    except OSError as e:
        return {"ok": False, "started": False,
                "detail": f"调试 profile 目录不可用（{profile}）：{e}"}
    args = [exe, f"--user-data-dir={profile}", f"--remote-debugging-port={CDP_PORT}",
            "--restore-last-session", "--no-first-run", "--no-default-browser-check"]
    try:
        subprocess.Popen(args, cwd=profile, close_fds=True, creationflags=_spawn_flags(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"ok": False, "started": False, "detail": f"拉起调试 Edge 失败：{e}"}
    deadline = time.monotonic() + patience_s
    while time.monotonic() < deadline:
        if cancel is not None and cancel():
            return {"ok": False, "started": True, "cancelled": True,
                    "detail": "已取消（端口没等到就点了停止）"}
        await asyncio.sleep(0.8)
        if await _cdp_probe():
            _cdp_fail_until = 0.0
            return {"ok": True, "started": True,
                    "detail": f"已拉起调试 Edge（profile={profile}）"}
    return {"ok": False, "started": True,
            "detail": f"调试 Edge 起了但 {int(patience_s)}s 内 9222 没就绪：该 profile 可能已被"
                      f"另一个 Edge 占着（关掉那个调试窗口再试），或 Edge 启动被安全软件拦了"}


class _CdpSession:
    """一条 connect_over_cdp 连接上批量/单发开页取 HTML（get_usage 与 build 脚本共用）。

    只 ``new_page``、只 close 自己开的页，绝不动用户已开的标签页；
    对 connect_over_cdp 的 browser.close() 只断开连接，不会结束用户浏览器进程。
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self._ctx = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(
                CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
            self._ctx = (self._browser.contexts[0] if self._browser.contexts
                         else await self._browser.new_context())
        except Exception:
            await self.aclose()
            raise
        return self

    async def fetch(self, url: str, challenge_wait: float = CDP_CHALLENGE_WAIT) -> str:
        """在用户真实会话里开新标签页取 HTML；标题是挑战页则轮询等放行（最长 challenge_wait 秒）。"""
        page = await self._ctx.new_page()
        try:
            await page.goto(url, timeout=CDP_GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
            deadline = time.monotonic() + challenge_wait
            while time.monotonic() < deadline:
                try:
                    title = await page.title()
                except Exception:  # noqa: BLE001
                    break
                if title and not any(m in title for m in _CHALLENGE_TITLE):
                    break
                await page.wait_for_timeout(1000)
            await page.wait_for_timeout(3000)   # domcontentloaded 后固定等 3s（networkidle 会被广告请求拖死，别用）
            return await page.content()
        finally:
            try:
                await page.close()              # 只关自己开的这一页
            except Exception:  # noqa: BLE001
                pass

    async def aclose(self):
        try:
            if self._browser is not None:
                await self._browser.close()     # CDP 场景：只断开，不杀用户浏览器
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = self._pw = None

    async def __aexit__(self, *exc):
        await self.aclose()
        return False


async def _fetch_via_cdp(h: int) -> dict | None:
    """提供者③：借用户已验证的浏览器开 light.gg 取 HTML → 现有解析器。失败返回 None。"""
    global _cdp_fail_until
    if time.monotonic() < _cdp_fail_until:      # 冷却期内直接跳过（零开销）
        return None
    html = ""
    try:
        if await _cdp_probe():
            await _rate_limit()                 # 开真实外网页面，计入全局限速
            async with _CdpSession() as sess:
                html = await sess.fetch(f"https://www.light.gg/db/items/{h}/")
    except Exception:  # noqa: BLE001
        html = ""
    if not html:
        _cdp_fail_until = time.monotonic() + _CDP_FAIL_TTL   # 失败冷却，避免每条指令都去连
        return None
    try:
        return parse_lightgg_html(html)
    except Exception:  # noqa: BLE001
        _dump_debug_html(h, html)               # 页面到手但解析不动 → 落盘调试
        _cdp_fail_until = time.monotonic() + _CDP_FAIL_TTL
        return None


def _pw_fetch_sync(url: str, proxy: str | None, timeout_ms: int = 45000) -> str:
    """Playwright + 真 Edge 抓页面（同步实现，供 asyncio.to_thread / build 脚本复用）"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True,
                              proxy={"server": proxy} if proxy else None)
        try:
            ctx = b.new_context(user_agent=EDGE_UA, locale="zh-CN")
            pg = ctx.new_page()
            pg.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            pg.wait_for_timeout(6000)  # 留给 Turnstile/异步渲染
            html = pg.content()
            ctx.close()
            return html
        finally:
            b.close()


async def _fetch_lightgg(cfg: dict, h: int) -> dict | None:
    proxy = cfg.get("proxy")
    if not proxy:            # 本机实测：无代理连不上 light.gg，别浪费请求
        return None
    url = f"https://www.light.gg/db/items/{h}/"
    html = ""
    try:
        await _rate_limit()
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers={"User-Agent": EDGE_UA},
                                     proxy=proxy, follow_redirects=True) as cli:
            r = await cli.get(url)
            if r.status_code == 200:
                html = r.text
    except Exception:  # noqa: BLE001
        pass
    if not html:
        try:  # httpx 过不去（Cloudflare）再用真 Edge
            await _rate_limit()
            html = await asyncio.to_thread(_pw_fetch_sync, url, proxy)
        except Exception:  # noqa: BLE001
            html = ""
    if not html:
        return None
    try:
        return parse_lightgg_html(html)
    except Exception:  # noqa: BLE001
        _dump_debug_html(h, html)   # 解析失败落盘，便于日后调解析器
        return None


def _dump_debug_html(h: int, html: str) -> None:
    try:
        d = os.path.join(os.getcwd(), "logs", "usage_debug")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"lightgg_{h}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- raw → 契约

def _pick_cols(h: int, raw: dict):
    """把 raw 里的 (hash, pct) 对归到列：优先用 raw 自带列序，否则按武器 plug 池归类。

    返回 (cols, mw_pairs, mod_pairs)。
    """
    pools = (_wf_pools or {}).get(str(h))
    if not pools:
        pools = {"cols": [], "mw": set(), "mods": set()}
    mw, mods = list(raw.get("mw") or []), list(raw.get("mods") or [])
    flat = list(raw.get("plugs") or [])
    if raw.get("cols"):
        cols = [(i, list(c)) for i, c in enumerate(raw["cols"])]
    else:
        cols = [(i, []) for i in range(len(pools["cols"]))]
        for hsh, pct in flat:
            for j, (_, hs) in enumerate(pools["cols"]):
                if str(hsh) in hs:
                    cols[j][1].append((hsh, pct))
                    break
            else:
                if str(hsh) in pools["mw"]:
                    mw.append((hsh, pct))
                elif str(hsh) in pools["mods"]:
                    mods.append((hsh, pct))
        cols = [(j, c) for j, c in cols if c]  # 保留原列号，标题按原 socket 位置对齐
    return cols, mw, mods


def _build_contract(h: int, raw: dict, source: str) -> dict | None:
    _ensure_indexes()
    try:
        cols, mw_pairs, mod_pairs = _pick_cols(h, raw)
    except Exception:  # noqa: BLE001
        return None
    wf_cols = ((_wf_pools or {}).get(str(h)) or {}).get("cols") or []
    # 异域内在 perk 池：light.gg 社区统计最前面多一列「异域特性变体」，本地列没有，
    # 按位置硬对齐会整体错位一列，因此各列先按 hash 重叠匹配本地列
    intr = {str(i.get("hash")) for i in (((_wf(h) or {}).get("plugs") or {}).get("intrinsic") or [])
            if isinstance(i, dict) and i.get("hash")} if _wf(h) else set()
    used: set[int] = set()

    out_cols = []
    trait_cols: list[list[dict]] = []
    for j, pairs in cols:
        if not pairs:
            continue
        plugs = []
        for hsh, pct in pairs:
            meta = _join_plug(hsh)
            if not meta:
                continue
            plugs.append({"hash": int(hsh), "name": meta["name"], "icon": meta["icon"], "pct": pct})
        if not plugs:
            continue
        # 同一 perk 常被原版/重制版两个 hash 描述（枯骨鳞片催化、脉搏监控…），按名去重留高出现率
        by_name: dict = {}
        for p in plugs:
            if p["name"] not in by_name or (p["pct"] or 0) > (by_name[p["name"]]["pct"] or 0):
                by_name[p["name"]] = p
        plugs = list(by_name.values())
        plugs.sort(key=lambda x: -x["pct"])
        best, bestn = None, 0
        for k, (_, hs) in enumerate(wf_cols):
            if k in used:
                continue
            n = sum(1 for hsh, _ in pairs if str(hsh) in hs)
            if n > bestn:
                best, bestn = k, n
        if best is not None and bestn:
            used.add(best)
            title = _col_title(wf_cols[best][0], best)
        elif intr and sum(1 for hsh, _ in pairs if str(hsh) in intr) >= max(1, len(pairs) // 2):
            title = "异域特性"
        else:
            # 本地列匹配不上：按插件名的槽位后缀识别（要求 ≥60% 命中，避免把
            # 「枪管收缩装置」这类特性误判成发射管），再退位置默认
            names = [p["name"] or "" for p in plugs]
            n = max(1, len(names))

            def _ratio(suffixes, contains=()):
                hit = sum(1 for s in names
                          if s.endswith(suffixes) or any(c in s for c in contains))
                return hit / n

            if _ratio(("枪托",)) >= 0.6:
                title = "枪托"
            elif _ratio(("弹匣", "弹药", "子弹")) >= 0.6:
                title = "弹匣"
            elif _ratio(("膛线", "枪管", "枪膛", "制退器", "收束器", "枪口")) >= 0.6:
                title = "发射管"
            else:
                title = _col_title("", j)
        # 框架插槽（异域刀剑可选框架等）名字都带「框架」，本地列却按兜底标成「特性 N」
        if title.startswith("特性") and sum(1 for p in plugs if "框架" in (p["name"] or "")) >= max(1, -(-len(plugs) * 3 // 5)):
            title = "框架"
        out_cols.append({"title": title, "plugs": plugs})
        if title.startswith("特性"):
            trait_cols.append(plugs)

    # 光.gg 没有统计的可选列（催化改装：单人合唱/零号修订等塑形异域的催化插槽）
    # 也注入进卡片，否则 perk 区少一列、组合配不到它
    for k, (t, hs) in enumerate(wf_cols):
        if k in used or t != "催化":
            continue
        plugs = []
        for hsh in hs:
            meta = _join_plug(hsh)
            if meta and not meta["name"].startswith("空"):
                plugs.append({"hash": int(hsh), "name": meta["name"], "icon": meta["icon"]})
        # 原版/重制版催化常是两个 hash 同名（枯骨鳞片催化），按名去重后不足两选项就不注入
        seen_n: set = set()
        plugs = [p for p in plugs if not (p["name"] in seen_n or seen_n.add(p["name"]))]
        if len(plugs) >= 2:
            col = {"title": "催化", "plugs": plugs}
            # 游戏内催化插槽在特性之后、枪托之前
            idx = next((i for i, c in enumerate(out_cols) if c["title"] == "枪托"), len(out_cols))
            out_cols.insert(idx, col)
            break

    def side(pairs):
        rows = []
        for hsh, pct in pairs or []:
            meta = _join_plug(hsh)
            if meta:
                rows.append({"name": meta["name"], "icon": meta["icon"], "pct": pct})
        by_name: dict = {}
        for r in rows:
            if r["name"] not in by_name or (r["pct"] or 0) > (by_name[r["name"]]["pct"] or 0):
                by_name[r["name"]] = r
        rows = list(by_name.values())
        rows.sort(key=lambda x: -x["pct"])
        return rows

    combos = []
    for c in raw.get("combos") or []:                  # light.gg 真实组合统计（优先）
        try:
            ha, hb, pct = int(c[0]), int(c[1]), float(c[2])
        except Exception:  # noqa: BLE001
            continue
        ma, mb = _join_plug(ha), _join_plug(hb)
        if not (ma and mb):                            # join 不到中文名 → 丢行
            continue
        combos.append({"names": [ma["name"], mb["name"]], "icons": [ma["icon"], mb["icon"]],
                       "pct": round(pct, 2)})
    combos.sort(key=lambda x: -x["pct"])
    # 组合若配在「固定 100% 单选项列」上（如单人合唱的狂热长矛，唯一选项必然 100%），
    # 这些 pct 实际只是另一列的边际分布；武器若还有无统计的可选催化列，
    # 按游戏内真实的可变列重算为 催化 × 该列（均分，无真实联合数据）
    single_cols = [c for c in out_cols if len(c["plugs"]) == 1 and (c["plugs"][0].get("pct") or 0) >= 100]
    cat_col = next((c for c in out_cols if c["title"] == "催化" and len(c["plugs"]) >= 2), None)
    if combos and single_cols and cat_col:
        fixed_names = {p["name"] for c in single_cols for p in c["plugs"]}
        if all(c["names"][0] in fixed_names or c["names"][1] in fixed_names for c in combos):
            combo_names = {n for cc in combos for n in cc["names"]}
            other = next((c for c in out_cols
                          if c is not cat_col and len(c["plugs"]) >= 2
                          and any(p["name"] in combo_names for p in c["plugs"])), None)
            if other and other["plugs"] and all(p.get("pct") is not None for p in other["plugs"]):
                n_cat = len(cat_col["plugs"])
                combos = [{"names": [cp["name"], op["name"]],
                           "icons": [cp["icon"], op["icon"]],
                           "pct": round(op["pct"] / n_cat, 2)}
                          for op in other["plugs"] for cp in cat_col["plugs"]]
                combos.sort(key=lambda x: -x["pct"])
    # 同名组合只留一条（原版/重制版双 hash 去重前可能生成重复行），留出现率最高的
    seen_c: set = set()
    combos = [c for c in combos
              if not (tuple(c["names"]) in seen_c or seen_c.add(tuple(c["names"])))]
    combos = combos[:8]
    if not combos and len(trait_cols) >= 2:            # 没有真实数据 → 两列 pct 独立乘积估算
        prods = []
        for a in trait_cols[0]:
            for b in trait_cols[1]:
                prods.append({"names": [a["name"], b["name"]], "icons": [a["icon"], b["icon"]],
                              "pct": round(a["pct"] * b["pct"] / 100, 2)})
        prods.sort(key=lambda x: -x["pct"])
        combos = prods[:8]

    if not out_cols:
        return None
    return {"fetched_at": _now_iso()[:10], "source": source, "cols": out_cols,
            "masterworks": side(mw_pairs), "mods": side(mod_pairs), "combos": combos}


# ---------------------------------------------------------------- 主入口

async def get_usage(item_hash: int, *, force: bool = False) -> dict | None:
    """查询武器社区使用率；失败返回 None（含负缓存期内），绝不抛异常。"""
    h = None
    try:
        h = int(item_hash)
    except Exception:  # noqa: BLE001
        return None
    try:
        lock = _inflight.setdefault(h, asyncio.Lock())
        async with lock:
            return await _impl(h, force)
    except Exception:  # noqa: BLE001
        return None


async def _impl(h: int, force: bool) -> dict | None:
    cache = _cache_load()
    state, data = _cache_get(cache, h, force)
    if state == "hit":
        return data
    if state == "neg":
        return None

    raw = None
    source = ""
    try:
        snap = _load_json(_snap_path(), {}) or {}
        if isinstance(snap.get(str(h)), dict):            # ① 本地快照（无网络）
            raw, source = snap[str(h)], "snapshot"
    except Exception:  # noqa: BLE001
        pass

    cfg = _load_cfg()
    if raw is None:                                        # ② 自定义端点
        raw = await _fetch_endpoint(cfg, h)
        source = "endpoint"
    if raw is None:                                        # ③ CDP（用户已验证浏览器，无需配置）
        raw = await _fetch_via_cdp(h)
        source = "cdp"
    if raw is None:                                        # ④ d2foundry
        raw = await _fetch_d2foundry(h)
        source = "d2foundry"
    if raw is None:                                        # ⑤ light.gg（需 proxy）
        raw = await _fetch_lightgg(cfg, h)
        source = "lightgg"
    if raw is None:
        cache[str(h)] = {"ts": _now_iso(), "neg": True}
        await _cache_store(cache)
        return None

    try:
        if isinstance(raw.get("contract"), dict):          # 端点已给完整契约
            c = raw["contract"]
            c.setdefault("fetched_at", _now_iso()[:10])
            c.setdefault("source", source)
            for col in c.get("cols") or []:
                col.get("plugs", []).sort(key=lambda x: -x.get("pct", 0))
            data = c
        else:
            data = _build_contract(h, raw, source)
    except Exception:  # noqa: BLE001
        data = None
    if not data:
        cache[str(h)] = {"ts": _now_iso(), "neg": True}    # raw 拿到但 join 不出 → 负缓存
        await _cache_store(cache)
        return None
    cache[str(h)] = {"ts": _now_iso(), "data": data}
    await _cache_store(cache)
    return data


# ---------------------------------------------------------------- WebUI 后台全库刷新
#
# 面板「武器使用率数据」卡片的后端：借 CDP 通道（start_edge_debug.bat 起的调试 Edge）
# 在已过 Cloudflare 的会话里用页内 fetch 批量重抓 light.gg，重写本地快照并失效派生缓存。
# 与 build_weapon_usage_fast.py 同一套路，但常驻进程内、面板可点、可看进度、可中途停止。

_REFRESH_SEED = "https://www.light.gg/db/items/42435996/"
_CF_TITLES = ("Just a moment", "请稍候", "Attention Required")

_refresh_state: dict = {"running": False, "done": 0, "total": 0, "ok": 0, "fail": 0,
                        "scope": "", "message": "待机", "started": "", "finished": "",
                        "error": "", "stop": False, "rate": 0.0, "eta_s": 0.0}
_refresh_task: asyncio.Task | None = None


def _challenged(html: str) -> bool:
    head = html[:4000]
    return ("Just a moment" in head) or ("请稍候" in head) or ("cf-chl-" in head)


def _refresh_targets() -> list[int]:
    """全库目标：weapons_full 里带特性列的武器（与 build_weapon_usage_fast 同口径）"""
    _ensure_indexes()
    return [int(h) for h, w in _WEAPONS_FULL.items()
            if any("特性" in (c.get("t") or "") for c in ((w.get("plugs") or {}).get("cols") or []))]


def resolve_weapon_hashes(q: str) -> list[int]:
    """手动校准入口：武器名（支持模糊/英文名/繁体名）/ hash → 待刷新 hash 列表（最多 10 个）"""
    _ensure_indexes()
    q = (q or "").strip().lower()
    if not q:
        return []
    if q.isdigit():
        return [int(q)] if q in _WEAPONS_FULL else []
    exact = [int(h) for h, w in _WEAPONS_FULL.items() if (w.get("name") or "").lower() == q]
    if exact:
        return exact[:10]
    part = [int(h) for h, w in _WEAPONS_FULL.items() if q in (w.get("name") or "").lower()]
    if part:
        return part[:10]
    return [int(h) for h in name_i18n.match_weapons(q, limit=10) if h in _WEAPONS_FULL]


def refresh_state() -> dict:
    s = {k: v for k, v in _refresh_state.items() if k != "stop"}
    total = s.get("total") or 0
    # pct 永远按 done/total 算（跑完/中途停/失败都如实反映），别再"没跑完就归零"
    s["pct"] = round(s.get("done", 0) * 100.0 / total, 1) if total else 0.0
    return s


def usage_status() -> dict:
    """面板卡片一览：快照/目标/缺失/缓存条数 + 最新数据日期 + 刷新任务进度"""
    try:
        targets = _refresh_targets()
    except Exception:  # noqa: BLE001
        targets = []
    snap = _load_json(_snap_path(), {}) or {}
    dates = [v.get("fetched_at") for v in snap.values() if isinstance(v, dict) and v.get("fetched_at")]
    return {"snapshot": len(snap), "targets": len(targets),
            "missing": sum(1 for h in targets if str(h) not in snap),
            "newest": max(dates) if dates else None,
            "cache": len(_cache_load()),
            "refresh": refresh_state()}


def _drop_contract_cache(hashes: list[int] | None) -> None:
    """清掉派生契约缓存：None=全清。让下一条指令起用新快照重建（负缓存一并失效）。"""
    try:
        cache = _cache_load()
        if hashes is None:
            cache = {}
        else:
            for h in hashes:
                cache.pop(str(h), None)
        _dump_json(_cwd_file("weapon_usage_cache.json"), cache)
    except Exception:  # noqa: BLE001
        pass


def _wrap_raw(raw: dict) -> dict:
    return {"source": "lightgg", "fetched_at": _now_iso()[:10],
            "cols": [[list(p) for p in col] for col in raw.get("cols") or []],
            "plugs": [list(p) for p in raw.get("plugs") or []],
            "mw": [list(p) for p in raw.get("mw") or []],
            "mods": [list(p) for p in raw.get("mods") or []],
            "combos": [list(c) for c in raw.get("combos") or []]}


async def start_refresh(scope: str = "all", hashes: list[int] | None = None) -> dict:
    """启动后台刷新任务。scope: all=全库重抓 / missing=只补缺失；hashes 给定时只刷这些。"""
    global _refresh_task
    if _refresh_state["running"]:
        return {"ok": False, "error": "已有刷新任务在进行中", "state": refresh_state()}
    if not hashes:
        targets = _refresh_targets()
        if scope == "missing":
            snap = _load_json(_snap_path(), {}) or {}
            # 快照键是字符串、targets 是 int：必须 str(h) 比较，否则"只补缺失"永远等于全库
            hashes = [h for h in targets if str(h) not in snap]
        else:
            hashes, scope = targets, "all"
    hashes = [int(h) for h in (hashes or [])]
    if not hashes:
        return {"ok": False, "error": "没有需要抓取的武器"}
    # 通道没开就自己拉起（面板点刷新 = 自动开调试 Edge）：这里只拦「连 Edge 都找不到」的硬失败，
    # 拉起过程放进任务里做，状态卡能显示进度（不至于 HTTP 请求卡 45 秒）
    need_boot = not await _cdp_probe()
    if need_boot and not _find_edge_exe():
        return {"ok": False, "error": "没有可用的 light.gg 通道，本机也找不到 Edge（msedge.exe）："
                                      "请先双击仓库根的 start_edge_debug.bat 启动调试模式 Edge"
                                      "（端口 9222），过一次验证后再点刷新"}
    # 立刻失效这批 hash 的旧契约缓存（含负缓存），爬的过程中查询也能落到快照旧值而不是坏缓存
    _drop_contract_cache(hashes if len(hashes) <= 64 else None)
    _refresh_state.update(running=True, done=0, total=len(hashes), ok=0, fail=0,
                          scope=scope or "custom", error="", stop=False, rate=0.0, eta_s=0.0,
                          message=("正在启动调试浏览器…" if need_boot else "连接调试浏览器…"),
                          started=_now_iso(), finished="")
    _refresh_task = asyncio.create_task(_refresh_job(hashes, boot=need_boot))
    return {"ok": True, "total": len(hashes)}


def stop_refresh() -> dict:
    if _refresh_state["running"]:
        _refresh_state["stop"] = True
        _refresh_state["message"] = "正在停止…（等当前页完成）"
        return {"ok": True}
    return {"ok": False, "error": "没有在跑的刷新任务"}


async def _open_crawl_page(ctx, wid: int, patience_s: float = 90.0):
    """开一页到 light.gg 并等挑战放行（标题正常 且 页面含 community-average）。"""
    page = await ctx.new_page()
    try:
        await page.goto(_REFRESH_SEED, timeout=45000, wait_until="domcontentloaded")
    except Exception:  # noqa: BLE001
        pass
    deadline = time.monotonic() + patience_s
    raised = False
    while time.monotonic() < deadline:
        if _refresh_state["stop"]:
            break
        try:
            html = await page.content()
        except Exception:  # noqa: BLE001
            html = ""
        if 'id="community-average"' in html and not _challenged(html):
            return page
        if not raised and _challenged(html):
            # 撞上人机验证：把调试 Edge 窗口置前 + 在面板上说清楚，让用户知道要点一下
            raised = True
            _refresh_state["message"] = "等 light.gg 人机验证放行：调试 Edge 窗口已置前，点一下验证就行"
            try:
                await page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        try:
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            break
    try:
        await page.close()
    except Exception:  # noqa: BLE001
        pass
    return None


async def _refresh_job(hashes: list[int], boot: bool = False) -> None:
    global _refresh_state
    workers_n = 3 if len(hashes) > 30 else 1
    t0 = time.monotonic()
    snap = _load_json(_snap_path(), {}) or {}
    ok = fail = done = 0
    pw = browser = None
    try:
        if boot:                             # 通道离线：先自己把调试 Edge 拉起来（不关用户正在用的）
            ch = await ensure_channel(cancel=lambda: _refresh_state["stop"])
            if not ch.get("ok"):
                if ch.get("cancelled"):
                    _refresh_state.update(running=False, finished=_now_iso(), message="已停止")
                else:
                    _refresh_state.update(running=False, finished=_now_iso(),
                                          message="没能打开 light.gg 通道",
                                          error=ch.get("detail") or "")
                return
            _refresh_state["message"] = "连接调试浏览器…"
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        q: asyncio.Queue = asyncio.Queue()
        for h in hashes:
            q.put_nowait(h)
        lock = asyncio.Lock()

        async def fetch_item(page, h: int) -> str:
            html = ""
            try:
                html = await page.evaluate(
                    "h => fetch('/db/items/' + h + '/').then(r => r.text())", str(h))
            except Exception:  # noqa: BLE001
                html = ""
            return html if (html and not _challenged(html)) else ""

        async def worker(wid: int):
            nonlocal ok, fail, done
            await asyncio.sleep(wid * 2.0)      # 错峰开页，避免并发冲刚过验证的域
            page = await _open_crawl_page(ctx, wid)
            if page is None:
                return
            while not _refresh_state["stop"]:
                try:
                    h = q.get_nowait()
                except asyncio.QueueEmpty:
                    return
                html = await fetch_item(page, h)
                if not html:                     # 会话失效/页崩了：重开页再试一次
                    try:
                        await page.close()
                    except Exception:  # noqa: BLE001
                        pass
                    page = await _open_crawl_page(ctx, wid)
                    if page is not None:
                        html = await fetch_item(page, h)
                raw = None
                if html:
                    try:
                        raw = parse_lightgg_html(html)
                    except Exception:  # noqa: BLE001
                        raw = None
                async with lock:
                    if raw:
                        snap[str(h)] = _wrap_raw(raw)
                        ok += 1
                    else:
                        fail += 1
                    done += 1
                    rate = done / max(time.monotonic() - t0, 0.1)      # 条/秒
                    eta = max(0.0, (total - done) / max(rate, 0.01))
                    # 具体进度交给面板的进度条（pct/rate/eta_s 都在 state 里），消息只报阶段
                    _refresh_state.update(done=done, ok=ok, fail=fail, rate=round(rate, 3),
                                          eta_s=round(eta, 1), message="抓取中…")
                    if done % 25 == 0 or done == total:
                        _dump_json(_snap_write_path(), snap)   # 周期落盘，中途崩溃也保留进度

        total = len(hashes)
        await asyncio.gather(*(worker(i) for i in range(workers_n)))
    except Exception as e:  # noqa: BLE001
        _refresh_state["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            if browser is not None:
                await browser.close()            # CDP：只断开，不杀调试 Edge
        except Exception:  # noqa: BLE001
            pass
        try:
            if pw is not None:
                await pw.stop()
        except Exception:  # noqa: BLE001
            pass
    if done >= len(hashes) and not _refresh_state["stop"]:
        _dump_json(_snap_write_path(), snap)
        _drop_contract_cache(None)               # 全清派生缓存，下条指令全部按新快照重建
    stopped = _refresh_state["stop"]
    _refresh_state.update(running=False,
                          finished=_now_iso(),
                          message=("已停止" if stopped else
                                   ("已中止" if _refresh_state["error"] else
                                    f"完成：成功 {ok} · 失败 {fail} · 共 {done}/{len(hashes)}（耗时 "
                                    f"{(time.monotonic() - t0) / 60:.1f} 分钟）")))
