"""Bungie 账号授权（OAuth）——给「需要登录才能读」的接口用（当前是光尘商店 GetVendor）。

为什么需要：Bungie 官方接口里，读当前商店内容（Destiny2.GetVendor）需要**用户授权**，
只带 API Key 会返回 InsufficientPrivileges。授权一次即可（refresh_token 长期有效，过期自动续）。

两种客户端都能用，**推荐「公开」**（不需要 client_secret，少抄一串容易出错的字符）：

    A. 公开客户端（推荐）
       Bungie 应用里「开放授权客户端类型」选 **公开**，Redirect URL 填
       https://127.0.0.1:8902/bungie/callback
       .env 只需要：BUNGIE_CLIENT_ID=你的 Client ID
       （公开客户端走 PKCE，代码里自动生成 code_challenge/code_verifier）

    B. 机密客户端
       「开放授权客户端类型」选 **机密**，再把 Client Secret 一起填进 .env
       BUNGIE_CLIENT_ID / BUNGIE_CLIENT_SECRET（机密类型必须有 secret，否则 Bungie 会回
       "Confidential client must authenticate with a client secret."）

为什么 React 地址是 https 却是 127.0.0.1：Bungie 只收 https 开头的重定向地址，不接受 http。
本机没有证书，所以跳回来时浏览器会报"连接不安全"——这不影响授权，**地址栏里那串
`.../bungie/callback?code=XXXX&state=YYYY` 已经带上了授权码**，把它整条复制回面板的
「粘贴回调地址」输入框点完成即可（面板里也会提示）。这是官方限制下的常规做法。
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time

import httpx

import bungie_status as bst
from jsonio import dump_json

BASE = "https://www.bungie.net"
AUTHORIZE_URL = BASE + "/en/OAuth/Authorize"
TOKEN_URL = BASE + "/Platform/App/OAuth/Token/"


def _writable_path(name: str) -> str:
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v:
        return v
    # 源码直跑时 .env 可能还没加载进环境（destiny_data 会加载），这里兜底读一次
    for base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
                 os.path.dirname(sys.executable)):
        p = os.path.join(base, ".env")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if "=" in line and not line.startswith("#"):
                    k, _, val = line.strip().partition("=")
                    if k == name and val:
                        return val
    return default


CLIENT_ID = lambda: _env("BUNGIE_CLIENT_ID")
CLIENT_SECRET = lambda: _env("BUNGIE_CLIENT_SECRET")
# Bungie 只接受 https 的重定向地址（填 http 会报"必须使用 http 以外的通信架构"）。
# 所以程序另起一个**带自签证书的 https 口**专门收授权回跳（见 TLS_PORT），
# 这样点完"同意"浏览器直接落到我们的"授权成功"页，不用再复制粘贴。
TLS_PORT = 8902
REDIRECT_URI = lambda: _env("BUNGIE_REDIRECT_URI",
                            f"https://127.0.0.1:{TLS_PORT}/bungie/callback")


def _asset(*parts: str) -> str:
    """资源文件定位：源码目录 → PyInstaller 打包资源 → cwd → exe 目录"""
    for base in (os.path.dirname(os.path.abspath(__file__)),
                 getattr(sys, "_MEIPASS", ""), os.getcwd(),
                 os.path.dirname(sys.executable)):
        if base:
            p = os.path.join(base, *parts)
            if os.path.exists(p):
                return p
    return ""


def cert_files() -> tuple[str, str]:
    """自签 https 证书 (cert, key)；没有则返回两个空串（此时回跳只能手动粘贴）"""
    cert = _asset("certs", "localhost.pem")
    key = _asset("certs", "localhost-key.pem")
    return (cert, key) if cert and key else ("", "")

TOKEN_FILE = "bungie_token.json"
USER_TOKEN_FILE = "bungie_tokens.json"   # 多用户：QQ → token（/登录 授权，/配装数字 /仓库 用）
_state: dict = {"v": ""}          # 防 CSRF 的 state（内存即可，进程内完成回跳）
_pkce: dict = {"verifier": ""}    # 公开客户端用的 PKCE verifier
_mem: dict = {"tok": None}        # token 缓存
_flows: dict = {}                 # state → {"qq", "verifier", "at"}（面板与 /登录 共用）
_utok: dict = {"data": None}      # 多用户 token 缓存


def has_secret() -> bool:
    return bool(CLIENT_SECRET())


def configured() -> bool:
    """只要填了 Client ID 就能用（公开客户端不需要 secret）"""
    return bool(CLIENT_ID())


def _load() -> dict:
    if _mem["tok"] is not None:
        return _mem["tok"]
    try:
        _mem["tok"] = json.load(open(_writable_path(TOKEN_FILE), encoding="utf-8"))
    except FileNotFoundError:
        _mem["tok"] = {}           # 还没授权过是常态
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] {TOKEN_FILE} 读取失败（按未授权处理）：{type(exc).__name__}: {exc}")
        _mem["tok"] = {}
    return _mem["tok"]


def _save(tok: dict):
    _mem["tok"] = tok
    try:
        dump_json(_writable_path(TOKEN_FILE), tok, indent=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] {TOKEN_FILE} 写盘失败（本次会话内 token 仍可用）："
              f"{type(exc).__name__}: {exc}")


def authorized() -> bool:
    t = _load()
    return bool(t.get("refresh_token") or t.get("access_token"))


def status() -> dict:
    t = _load()
    return {"configured": configured(), "authorized": authorized(),
            "has_secret": has_secret(),
            "membership_id": t.get("membership_id", ""),
            "display_name": t.get("display_name", ""),
            "expires_at": t.get("expires_at", 0),
            "redirect_uri": REDIRECT_URI()}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def lan_ip() -> str:
    """本机局域网 IP（UDP connect 探测，不实际发包）；拿不到返回空串。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        s.close()
    return "" if ip.startswith("127.") else ip


def lan_redirect_origin() -> str:
    """局域网回跳源（https://内网IP:端口）；配置了固定 REDIRECT_URI 的端口沿用其端口。"""
    ip = lan_ip()
    if not ip:
        return ""
    from urllib.parse import urlparse
    port = urlparse(REDIRECT_URI()).port or TLS_PORT
    return f"https://{ip}:{port}"


def auth_url(qq: str = "", origin: str = "") -> str:
    """授权跳转地址（带一次性 state；公开客户端再带 PKCE）。

    qq 非空 = 群里 /登录 发起的授权：state 记住发起者，回跳/回调后 token 落到
    bungie_tokens.json[qq]（多用户），不影响面板授权的主账号。
    origin 非空 = 回跳指向该源（如 https://内网IP:8902），供同局域网设备点完
    「允许」直接落回 bot 完成授权；换 token 时用同一个地址即可。
    """
    from urllib.parse import urlencode
    state = secrets.token_urlsafe(12)
    verifier = "" if has_secret() else _b64url(secrets.token_bytes(48))
    redirect_uri = (origin.rstrip("/") + "/bungie/callback") if origin else REDIRECT_URI()
    _flows[state] = {"qq": qq or "", "verifier": verifier, "at": time.time(),
                     "redirect_uri": redirect_uri}
    params = {"client_id": CLIENT_ID(), "response_type": "code",
              "state": state, "redirect_uri": redirect_uri}
    # 不要带 scope 参数：Bungie 现在直接报 invalid_scope
    # 「Scope is always configured value. Do not specify scope parameter.」
    # ——权限只认开发者应用页注册的 scope，链接里传什么都不行
    if verifier:
        params["code_challenge"] = _b64url(hashlib.sha256(verifier.encode()).digest())
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def check_state(state: str) -> bool:
    return bool(state) and state in _flows


def parse_code(text: str) -> tuple[str, str]:
    """从粘贴内容里取 (code, state)。

    用户粘的多半是整条回调地址（浏览器报"不安全"时地址栏里那串），
    也允许直接粘裸 code。"""
    s = (text or "").strip()
    if not s:
        return "", ""
    if "code=" in s:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(s).query or s.split("?", 1)[-1])
        return (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    return s, ""


def redirect_from_text(text: str) -> str:
    """从粘贴的回调地址里取 redirect_uri（scheme://host[:port]/path）。

    Bungie 对 redirect_uri 的校验只发生在换 token 这一步，且必须与**发 code 那次
    授权**实际用的地址完全一致——地址栏里那条回调地址的 origin+path 就是它
    （可能是旧会话/旧配置留下的，与当前 BUNGIE_REDIRECT_URI 不同）。取不到返回空串，
    exchange 会退回配置值。
    """
    import re
    from urllib.parse import urlparse
    m = re.search(r"https?://[^\s?'&<>]+", (text or ""))
    if not m:
        return ""
    u = urlparse(m.group(0))
    return f"{u.scheme}://{u.netloc}{u.path}"


async def _token_call(data: dict, verifier: str = "") -> dict:
    """换/续 token。有 secret 走 Basic 认证；没有则把 client_id（和 PKCE verifier）放 body。"""
    d = dict(data)
    auth = None
    if has_secret():
        auth = (CLIENT_ID(), CLIENT_SECRET())
    else:
        d["client_id"] = CLIENT_ID()
        if verifier and d.get("grant_type") == "authorization_code":
            d["code_verifier"] = verifier
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data=d, auth=auth,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        msg = (r.text or "").strip()[:200]
        # 维护期实测就是这个响应（10-06 01:08）：
        #   HTTP 400 {"error":"server_error","error_description":"DestinyThrottledByGameServer"}
        # 以前它会被当成「换 token 失败」，日志里看不出是官方维护，用户还会被
        # 提示去重新授权（其实授权本身没问题，等维护结束就好）
        if hit := bst.classify_throttle_body(msg):
            bst.trip(*hit)
            raise bst.BungieMaintenanceError(bst.text())
        if "Confidential client must authenticate" in msg:
            raise RuntimeError(
                "Bungie 说这个应用是「机密」类型、必须带 client_secret。"
                "去应用页把「开放授权客户端类型」改成「公开」保存后再授权，"
                "或在 .env 里补上 BUNGIE_CLIENT_SECRET。")
        raise RuntimeError(f"Bungie 换 token 失败（HTTP {r.status_code}）：{msg or '无返回内容'}")
    return r.json()


async def _membership_for(tok: dict) -> dict:
    """用指定 token（而非主账号缓存）查命运2成员关系。"""
    headers = {"X-API-Key": _env("BUNGIE_API_KEY"),
               "Authorization": f"Bearer {tok.get('access_token', '')}"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(BASE + "/Platform/User/GetMembershipsForCurrentUser/", headers=headers)
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        bst.note_http(r.status_code, (r.content or b"")[:4000])
        return {}
    if hit := bst.classify_json(d):
        bst.trip(*hit)
        raise bst.BungieMaintenanceError(bst.text())
    resp = d.get("Response") or {}
    des = resp.get("destinyMemberships") or []
    if not des:
        return {}
    primary = resp.get("primaryMembershipId")
    pick = next((m for m in des if m.get("membershipId") == primary), des[0])
    code = pick.get("bungieGlobalDisplayNameCode")
    return {"display_name": (pick.get("bungieGlobalDisplayName") or "") +
            (f"#{code:04d}" if code else ""),
            "membership_type": pick.get("membershipType"),
            "membership_id": pick.get("membershipId")}


async def exchange(code: str, redirect_uri: str = "", state: str = "") -> dict:
    """用授权码换 token 并落盘（redirect_uri 必须与授权时一致，故一并带上）。

    state 能对上 _flows 时按那次授权的 PKCE verifier 换，且：
    - state 绑定了 qq → token 存 bungie_tokens.json[qq]（多用户），返回里带 qq
    - 否则存主账号 bungie_token.json（面板授权，原行为）

    redirect_uri 校验只发生在 token 这一步（authorize 那步不拦），所以「浏览器
    拿到 code、换 token 却报 mismatch」= 发 code 的授权页用的地址与当前配置不同
    （典型：旧会话/旧配置留下的授权页，Bungie 应用里登记的还是旧地址）。
    传入 redirect_uri（从粘贴的回调地址或实际落地 URL 里取）后先试它，不行再退
    回当前配置值，两边各试一次，哪个匹配用哪个。
    """
    flow = _flows.pop(state, None) if state else None
    verifier = (flow or {}).get("verifier", "") or _pkce.get("verifier", "")
    candidates = []
    for ru in ((flow or {}).get("redirect_uri"), redirect_uri, REDIRECT_URI()):
        if ru and ru not in candidates:
            candidates.append(ru)
    last_err: Exception | None = None
    d = None
    for ru in candidates:
        try:
            d = await _token_call({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": ru}, verifier)
            break
        except RuntimeError as exc:
            last_err = exc
            if "does not match" not in str(exc):
                raise
    else:
        raise last_err or RuntimeError("Bungie 换 token 失败：所有 redirect_uri 都被拒绝")
    tok = {"access_token": d.get("access_token", ""),
           "refresh_token": d.get("refresh_token", ""),
           "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60,
           "membership_id": d.get("membership_id", "")}
    info = await _membership_for(tok)
    if info:
        tok.update(display_name=info.get("display_name", ""),
                   membership_type=info.get("membership_type", 0),
                   membership_id=info.get("membership_id", ""))
    if flow and flow.get("qq"):
        tok["qq"] = flow["qq"]
        _users_store({**_users_load(), flow["qq"]: tok})
        tok["is_user"] = True
        return tok
    _save(tok)
    return tok


_refresh_lock: dict = {"lock": None}


def _lock():
    import asyncio
    if _refresh_lock["lock"] is None:
        _refresh_lock["lock"] = asyncio.Lock()
    return _refresh_lock["lock"]


async def access_token(refresh_ahead: int = 0) -> str:
    """取一个有效的 access token（过期就地刷新）；未授权返回空串。

    refresh_ahead：剩余寿命不足该秒数就提前刷新（0=用到最后一刻）。
    调度器传 3600 做「临期 1 小时保活」——否则本函数会一直返回缓存旧 token，
    调度器的提前续期等于白调，面板上 token 总是拖到过期才恢复。

    刷新失败不再吞掉：Bungie 的 refresh_token 是一次性的，且偶发返回非 JSON 的
    错误页，此前吞掉会让上层误报「未授权」；这里抛出真实原因，并把失败时正在
    并发刷新的多个请求用锁串起来，避免互相把对方的 refresh_token 作废。
    """
    t = _load()
    if not t:
        return ""
    if t.get("access_token") and time.time() + refresh_ahead < t.get("expires_at", 0):
        return t["access_token"]
    if not t.get("refresh_token"):
        return t.get("access_token", "")
    async with _lock():
        t = _load()          # 锁内重读：别的请求可能刚刷新完
        if t.get("access_token") and time.time() + refresh_ahead < t.get("expires_at", 0):
            return t["access_token"]
        try:
            d = await _token_call({"grant_type": "refresh_token",
                                   "refresh_token": t["refresh_token"]})
        except Exception as exc:  # noqa: BLE001
            await asyncio.sleep(2)   # Bungie 偶发抖动：隔 2 秒重试一次再放弃
            try:
                d = await _token_call({"grant_type": "refresh_token",
                                       "refresh_token": t["refresh_token"]})
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(f"Bungie token 刷新失败：{exc2}") from exc2
    t.update(access_token=d.get("access_token", t.get("access_token", "")),
             refresh_token=d.get("refresh_token", t.get("refresh_token", "")),
             expires_at=time.time() + int(d.get("expires_in", 3600)) - 60)
    _save(t)
    return t.get("access_token", "")


async def authorized_get(path: str, params: dict | None = None) -> dict:
    """带 Bearer 调用 Bungie 接口，返回 Response 字段"""
    tok = await access_token()
    if not tok:
        raise RuntimeError("未授权 Bungie 账号")
    return await _auth_get_with(tok, path, params)


async def _auth_get_with(tok: str, path: str, params: dict | None = None,
                         timeout: float = 20) -> dict:
    """带 Bearer 的 GET + 维护判定（authorized_get / authorized_get_as 共用）"""
    await bst.guard()                # 维护中不打接口：省一次必失败的往返
    headers = {"X-API-Key": _env("BUNGIE_API_KEY"), "Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(BASE + path, params=params or {}, headers=headers)
    try:
        d = r.json()
    except ValueError:
        # Bungie 偶发返回 HTML 错误页（维护/风控/5xx）——维护期最常见，点亮维护态
        bst.note_http(r.status_code, (r.content or b"")[:4000])
        if bst.is_down():
            raise bst.BungieMaintenanceError(bst.text())
        raise RuntimeError(
            f"Bungie 返回了非 JSON 响应（HTTP {r.status_code}），多为官方临时故障，稍后再试")
    if hit := bst.classify_json(d):
        bst.trip(*hit)
        raise bst.BungieMaintenanceError(bst.text())
    if d.get("ErrorCode") != 1:
        raise RuntimeError(f"Bungie: {d.get('ErrorStatus')} {d.get('Message')}")
    bst.note_ok()
    return d.get("Response") or {}


async def membership() -> dict:
    """当前授权账号的命运2成员关系（含跨存档主账号）"""
    try:
        resp = await authorized_get("/Platform/User/GetMembershipsForCurrentUser/")
    except Exception:  # noqa: BLE001
        return {}
    des = (resp.get("destinyMemberships") or [])
    if not des:
        return {}
    primary = resp.get("primaryMembershipId")
    pick = next((m for m in des if m.get("membershipId") == primary), des[0])
    code = pick.get("bungieGlobalDisplayNameCode")
    return {"display_name": (pick.get("bungieGlobalDisplayName") or "") +
            (f"#{code:04d}" if code else ""),
            "membership_type": pick.get("membershipType"),
            "membership_id": pick.get("membershipId")}


def logout():
    _save({})


# ---------- 多用户 token：群里 /登录 授权，/配装数字 与 /仓库 用 ----------
# 玩家级私有数据（游戏内配装 206、完整库存 102/201）官方只对「token 本人」开放
# （DIM 同款限制），所以每个 QQ 用户各自授权自己的账号，token 按 QQ 存。

def _users_load() -> dict:
    if _utok["data"] is not None:
        return _utok["data"]
    try:
        _utok["data"] = json.load(open(_writable_path(USER_TOKEN_FILE), encoding="utf-8"))
    except FileNotFoundError:
        _utok["data"] = {}
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] {USER_TOKEN_FILE} 读取失败（按未授权处理）：{type(exc).__name__}: {exc}")
        _utok["data"] = {}
    return _utok["data"]


def _users_store(d: dict):
    _utok["data"] = d
    try:
        dump_json(_writable_path(USER_TOKEN_FILE), d, indent=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] {USER_TOKEN_FILE} 写盘失败：{type(exc).__name__}: {exc}")


def user_status(qq: str) -> dict:
    t = _users_load().get(str(qq)) or {}
    return {"authorized": bool(t.get("refresh_token") or t.get("access_token")),
            "display_name": t.get("display_name", ""),
            "membership_id": t.get("membership_id", ""),
            "membership_type": t.get("membership_type", 0),
            "expires_at": t.get("expires_at", 0)}


def user_logout(qq: str):
    d = _users_load()
    if str(qq) in d:
        d.pop(str(qq))
        _users_store(d)


_user_locks: dict = {}


async def user_access_token(qq: str) -> str:
    """取该 QQ 用户的有效 access token（过期就地刷新）；未授权抛 RuntimeError。

    refresh_token 一次性，失败重试一次，与主账号同样的口径。"""
    t = _users_load().get(str(qq))
    if not t or not (t.get("refresh_token") or t.get("access_token")):
        raise RuntimeError("你还没登录 Bungie 账号：先发 /登录 完成授权，"
                           "再回来用这个功能（游戏内配装/仓库是官方仅本人可见的数据）")
    if t.get("access_token") and time.time() < t.get("expires_at", 0):
        return t["access_token"]
    if not t.get("refresh_token"):
        return t.get("access_token", "")
    lock = _user_locks.setdefault(str(qq), asyncio.Lock())
    async with lock:
        t = _users_load().get(str(qq)) or {}
        if t.get("access_token") and time.time() < t.get("expires_at", 0):
            return t["access_token"]
        try:
            d = await _token_call({"grant_type": "refresh_token",
                                   "refresh_token": t["refresh_token"]})
        except Exception as exc:  # noqa: BLE001
            await asyncio.sleep(2)
            try:
                d = await _token_call({"grant_type": "refresh_token",
                                       "refresh_token": t["refresh_token"]})
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(f"Bungie token 刷新失败：{exc2}") from exc2
        t.update(access_token=d.get("access_token", t.get("access_token", "")),
                 refresh_token=d.get("refresh_token", t.get("refresh_token", "")),
                 expires_at=time.time() + int(d.get("expires_in", 3600)) - 60)
        _users_store({**_users_load(), str(qq): t})
        return t["access_token"]


async def authorized_get_as(qq: str, path: str, params: dict | None = None) -> dict:
    """以某个 QQ 用户授权的 token 调 Bungie 接口，返回 Response 字段。"""
    tok = await user_access_token(qq)
    return await _auth_get_with(tok, path, params, timeout=25)


if __name__ == "__main__":
    print(status())
    if configured():
        print(auth_url())
