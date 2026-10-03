"""NapCat 托管：负责下载/启动 QQ 协议端（扫码在面板内完成）
- 自动定位本机 QQ.exe（注册表/进程/常见路径）
- 启动 NapCatWinBootMain.exe（无头 QQ + 注入），日志写 napcat_shell/napcat.log
- 面板内展示登录二维码（NapCat WebUI API），扫码登录后自动写入
  onebot11_<QQ>.json（反向 WebSocket → NoneBot 8901）并重启 NapCat 生效
"""
WEBUI_TOKEN = "d2query"  # 与 config/webui.json 中的 token 保持一致
ONEBOT_WS_URL = "ws://127.0.0.1:8901/onebot/v11/ws"
import atexit
import json
import os
import sys
import re
import subprocess
import threading
import time

import qr_png  # 本地二维码出图（纯标准库）

ROOT = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [os.path.join(ROOT, "napcat_shell"),
               os.path.join(os.path.dirname(sys.executable), "napcat_shell"),
               os.path.join(os.getcwd(), "napcat_shell")]
NAPCAT_DIR = next((c for c in _CANDIDATES
                   if os.path.exists(os.path.join(c, "NapCatWinBootMain.exe"))),
                  _CANDIDATES[0] if not getattr(sys, "frozen", False) else _CANDIDATES[1])
LOG_FILE = os.path.join(NAPCAT_DIR, "napcat.log")
WEBUI_PORT = 6099

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_qq = ""  # 扫码登录成功后记录的 QQ 号
_last_start = 0.0  # 上次启动时间，用于冷却，避免频繁登录触发 QQ 风控
START_COOLDOWN = 300  # 秒：两次启动间隔小于该值直接拒绝


def _find_qq() -> str:
    """定位本机 QQ.exe：注册表 → 运行中进程 → 常见路径"""
    try:
        import winreg
        for key_path in (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
                         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
                         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{B377DBA0-329B-4FD9-8A9D-15B19ED5AC3A}"):
            try:
                v, _ = winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path), "UninstallString")
                p = os.path.join(os.path.dirname(v), "QQ.exe")
                if os.path.exists(p):
                    return p
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    for p in (r"D:\1\Bin\QQ.exe", r"C:\Program Files\Tencent\QQNT\QQ.exe",
              r"C:\Program Files (x86)\Tencent\QQNT\QQ.exe"):
        if os.path.exists(p):
            return p
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='QQ.exe'", "get", "ExecutablePath"],
            text=True, errors="replace", timeout=10)
        for line in out.splitlines():
            line = line.strip()
            if line.lower().endswith("qq.exe"):
                return line
    except Exception:  # noqa: BLE001
        pass
    return ""


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def _tree_kill(pid: int) -> bool:
    """taskkill 连子进程一起杀：NapCatWinBootMain 拉起的 QQ.exe 是它的子进程，
    只 kill 主进程会留下僵尸 QQ 继续占着 WebUI 端口"""
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _kill_webui_holder() -> bool:
    """杀掉占着 WebUI 端口(6099)的残留 QQ/NapCat 进程（上一轮崩溃/退出没带走的）。
    只认 QQ.exe / NapCatWinBootMain.exe，不会误伤用户自己的其他程序"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, errors="replace",
                             timeout=15).stdout
    except Exception:  # noqa: BLE001
        return False
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{WEBUI_PORT}"):
            pids.add(parts[4])
    killed = False
    for pid in pids:
        try:
            q = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, errors="replace",
                               timeout=15).stdout
        except Exception:  # noqa: BLE001
            continue
        name = q.split(",")[0].strip('"').strip() if q else ""
        if name.lower() in ("qq.exe", "napcatwinbootmain.exe"):
            killed = _tree_kill(int(pid)) or killed
    return killed


def webui_url() -> str:
    return f"http://127.0.0.1:{WEBUI_PORT}/webui"


def _missing_napcat_files() -> list[str]:
    """napcat.mjs 是打包产物，会 import 同目录的分包文件（conout-*.js）。
    缺一个就会在 loader 里静默 import 失败——QQ 照常启动但 NapCat 永远不初始化，
    面板二维码永远"加载中"。启动前把引用的相对文件全部查一遍，缺了直接报出来。"""
    mjs = os.path.join(NAPCAT_DIR, "napcat.mjs")
    if not os.path.exists(mjs):
        return ["napcat.mjs"]
    missing = []
    try:
        body = open(mjs, "rb").read().decode("utf-8", "ignore")
    except OSError:
        return ["napcat.mjs(不可读)"]
    for ref in sorted(set(re.findall(r'(?:from\s*"\./|import\s*\("\./)([^"\')]+?\.js)', body))):
        if not os.path.exists(os.path.join(NAPCAT_DIR, ref)):
            missing.append(ref)
    return missing


def start(qq_path: str = "") -> dict:
    """启动 NapCat（幂等）。返回 {started, qq, log}；5 分钟内重复启动会被拒绝"""
    global _proc, _last_start
    with _lock:
        if is_running():
            return {"started": True, "log": LOG_FILE, "webui": webui_url()}
        if login_qr_ready():  # NapCat 已在跑（如面板服务重启后）
            try:
                healthy = bool(login_status().get("webui"))
            except Exception:  # noqa: BLE001
                healthy = False
            if healthy:  # WebUI 有响应：认领，不重复拉起
                threading.Thread(target=_watch_login, daemon=True).start()
                return {"started": True, "log": LOG_FILE, "webui": webui_url()}
            # WebUI 没响应＝残留僵尸 QQ 占着端口：清掉后走全新启动
            _kill_webui_holder()
            time.sleep(2)
        wait = START_COOLDOWN - (time.time() - _last_start)
        if wait > 0:  # 冷却：频繁登录会被 QQ 判定为异常登录
            return {"started": False,
                    "error": f"请稍候 {int(wait)} 秒再启动（短时间内反复登录会触发 QQ 风控）"}
        if not os.path.exists(os.path.join(NAPCAT_DIR, "NapCatWinBootMain.exe")):
            return {"started": False, "error": "napcat_shell 目录不存在（先运行 setup_napcat.py）"}
        miss = _missing_napcat_files()
        if miss:
            return {"started": False,
                    "error": f"NapCat 文件不完整，缺少 {', '.join(miss)}（从仓库 napcat_shell 同步后重试）"}
        qq = qq_path or _find_qq()
        if not qq or not os.path.exists(qq):
            return {"started": False, "error": "找不到本机 QQ.exe，请安装 QQ NT 版后重试"}
        # WebUI 固定端口/token，供面板内嵌扫码
        cfg_dir = os.path.join(NAPCAT_DIR, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        webui_cfg = os.path.join(cfg_dir, "webui.json")
        if not os.path.exists(webui_cfg):
            json.dump({"port": WEBUI_PORT, "token": WEBUI_TOKEN, "login": True},
                      open(webui_cfg, "w", encoding="utf-8"))
        loader = os.path.join(NAPCAT_DIR, "loadNapCat.js")
        mjs = os.path.join(NAPCAT_DIR, "napcat.mjs").replace(os.sep, "/")
        with open(loader, "w", encoding="utf-8") as f:
            f.write(f'(async () => {{await import("file:///{mjs}")}})()')
        env = os.environ.copy()
        env.update({
            "NAPCAT_PATCH_PACKAGE": os.path.join(NAPCAT_DIR, "qqnt.json"),
            "NAPCAT_LOAD_PATH": loader,
            "NAPCAT_INJECT_PATH": os.path.join(NAPCAT_DIR, "NapCatWinBootHook.dll"),
            "NAPCAT_MAIN_PATH": os.path.join(NAPCAT_DIR, "napcat.mjs"),
        })
        log = open(LOG_FILE, "w", encoding="utf-8", errors="ignore")
        cmd = [os.path.join(NAPCAT_DIR, "NapCatWinBootMain.exe"), qq,
               os.path.join(NAPCAT_DIR, "NapCatWinBootHook.dll")]
        uin = _saved_uin()
        if uin:  # 快速登录免扫码：uin 需裸传，launcher 会自动补 -q
            cmd.append(uin)
        _proc = subprocess.Popen(
            cmd, cwd=NAPCAT_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        _last_start = time.time()
        threading.Thread(target=_watch_login, daemon=True).start()
        return {"started": True, "qq": qq, "log": LOG_FILE, "webui": webui_url()}


def _kill_orphan_boot() -> bool:
    """兜底杀掉仍在跑的 NapCatWinBootMain（exe 崩溃退出、或 NapCat 被认领后 _proc 为空的场景）。
    本机的 NapCatWinBootMain 只可能来自本程序（与 _cleanup.ps1 同口径），不会碰到用户自己的 QQ"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq NapCatWinBootMain.exe",
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace",
                             timeout=15).stdout
    except Exception:  # noqa: BLE001
        return False
    killed = False
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "napcatwinbootmain.exe":
            try:
                killed = _tree_kill(int(parts[1])) or killed
            except ValueError:
                continue
    return killed


def stop():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _tree_kill(_proc.pid)
        _proc = None
        _kill_webui_holder()  # 残留的 QQ.exe 不清掉，下次启动会误认领僵尸进程
        _kill_orphan_boot()   # 被认领/遗留的 NapCat 引导进程也要连根拔掉


# 面板窗口关闭 = webview.start() 返回、解释器正常收尾 → NapCat/QQ 跟程序一起退出。
# 部署脚本用 taskkill /F 停 exe，不走 atexit，NapCat 存活，重启 exe 后原会话直接重连免扫码。
# 收尾阶段任何异常都不能外抛（否则退出时打一屏 traceback，还可能断在半路）


def _atexit_stop():
    try:
        stop()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_atexit_stop)


def reset() -> dict:
    """手动退出登录并重置：杀掉 NapCat/QQ 进程树，回到未启动态（面板可重新扫码）。
    注意：重置后再扫码＝一次重新登录，短时间反复操作可能触发 QQ 风控"""
    stop()
    _cred_cache["v"] = ""
    return {"reset": True, "webui": webui_url()}


def login_qr_ready() -> bool:
    """WebUI 端口是否已起（登录二维码可访问）"""
    try:
        import httpx
        r = httpx.get(f"http://127.0.0.1:{WEBUI_PORT}/", timeout=3, verify=False)
        return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


# ---------- 面板内扫码：NapCat WebUI API ----------
_cred_cache = {"v": ""}


def _cred(force: bool = False) -> str:
    """WebUI 登录换 Bearer 凭证（缓存复用；登录接口有频率限制，不能每次都调）"""
    import hashlib
    import httpx
    if _cred_cache["v"] and not force:
        return _cred_cache["v"]
    digest = hashlib.sha256(f"{WEBUI_TOKEN}.napcat".encode()).hexdigest()
    try:
        r = httpx.post(f"http://127.0.0.1:{WEBUI_PORT}/api/auth/login",
                       json={"hash": digest}, timeout=5)
        d = (r.json().get("data") or {}).get("Credential", "")
    except Exception:  # noqa: BLE001
        return ""
    if d:
        _cred_cache["v"] = d
    return d


def _api_post(path: str, payload=None):
    import httpx
    last_exc: Exception | None = None
    for attempt in range(2):
        cred = _cred(force=attempt > 0)
        if not cred:
            break
        try:
            r = httpx.post(f"http://127.0.0.1:{WEBUI_PORT}/api{path}",
                           headers={"Authorization": f"Bearer {cred}"},
                           json=payload, timeout=5)
            body = {}
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                pass
            # NapCat 凭证失效时返回 HTTP 200 + {"code":-1}，不是 401：
            # 必须按业务码判定，否则坏凭证会被永久缓存，面板永远显示"等待扫码登录"
            if body.get("code") not in (None, 0):
                if attempt == 0:  # 换新凭证重试一次
                    _cred_cache["v"] = ""
                    continue
                raise RuntimeError(f"WebUI {path} 返回错误: {body.get('message')}")
            return body.get("data") or {}
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc or RuntimeError(f"WebUI {path} 调用失败")


def login_status() -> dict:
    """{webui, isLogin, isOffline, uin}；WebUI 未就绪时 {webui: False}"""
    st = {"webui": False, "isLogin": False, "isOffline": True, "uin": ""}
    try:
        d = _api_post("/QQLogin/CheckLoginStatus")
    except Exception:  # noqa: BLE001
        return st
    st.update({"webui": True,
               "isLogin": bool(d.get("isLogin")),
               "isOffline": bool(d.get("isOffline")),
               "uin": str(d.get("uin") or d.get("Uin") or "")})
    if st["isLogin"] and not st["uin"]:  # CheckLoginStatus 不带 uin，另查一次
        try:
            info = _api_post("/QQLogin/GetQQLoginInfo")
            st["uin"] = str(info.get("uin") or "")
        except Exception:  # noqa: BLE001
            pass
    return st


_qr_cache = {"url": "", "data": ""}   # 按 URL 缓存渲染结果，避免每 5 秒轮询重复编码
_qr_seen = {"url": "", "since": 0.0}  # 当前 URL 首次出现的时间，用于判断是否该续期
_qr_refreshed = 0.0                   # 上次主动刷新的时间（限频）


def _qr_url() -> str:
    """NapCat 当前登录二维码内容（txz.qq.com 登录 URL）。

    优先 /QQLogin/GetQQLoginQrcode；未登录时它才返回码，已登录/服务重启中会报错，
    这时退回 CheckLoginStatus——实测它同样带 qrcodeurl 字段。
    """
    try:
        d = _api_post("/QQLogin/GetQQLoginQrcode") or {}
        if d.get("qrcode"):
            return d["qrcode"]
    except Exception:  # noqa: BLE001
        pass
    try:
        return (_api_post("/QQLogin/CheckLoginStatus") or {}).get("qrcodeurl") or ""
    except Exception:  # noqa: BLE001
        return ""


def _render_qr(url: str) -> str:
    """把二维码 URL 渲染成 data URL 并按 URL 缓存（URL 变了才重新编码）"""
    if not url:
        return ""
    if url.startswith("data:image"):  # 某些 NapCat 版本直接返回图片
        _qr_cache.update(url=url, data=url)
        return url
    if _qr_cache["url"] != url or not _qr_cache["data"]:
        data = ""
        for level in ("Q", "M", "L"):  # Q 容量足够；兜底降级应对异常长的 URL
            try:
                data = qr_png.data_url(url, level, scale=4)
                break
            except Exception:  # noqa: BLE001
                data = ""
        _qr_cache.update(url=url, data=data)
    return _qr_cache["data"]


def refresh_qr(force: bool = False, timeout: float = 20.0) -> str:
    """让 NapCat 重新生成登录二维码（等价于 WebUI 的刷新按钮），返回**新码**的 data URL。

    关键点（此前"点刷新但不刷新"的成因）：
    - NapCat 的 RefreshQRcode 在登录服务需要重启时只返回 {restarting:true}（没有 qrcodeurl），
      这时必须轮询等新码出现，不能直接拿 GetQQLoginQrcode 的旧码当结果；
    - 刷新后 GetQQLoginQrcode 短时间可能仍返回旧 URL，用它渲染就会"看着没变"。
      所以这里直接采用 RefreshQRcode 返回的新 URL，并清掉本地渲染缓存强制重新出图。
    """
    global _qr_refreshed
    now = time.time()
    if not force and now - _qr_refreshed < 30:  # 限频，避免刷接口
        return ""
    _qr_refreshed = now
    old = _qr_seen["url"] or _qr_url()
    restarting = False
    new = ""
    try:
        d = _api_post("/QQLogin/RefreshQRcode") or {}
        new = d.get("qrcodeurl") or ""
        restarting = bool(d.get("restarting"))
    except Exception:  # noqa: BLE001
        pass
    if new and new != old:
        _qr_seen.update(url=new, since=now)
        return _render_qr(new)
    # restarting（登录服务重启中）或返回同码：轮询等新码出现
    deadline = now + (timeout if restarting or not old else 6.0)
    while time.time() < deadline:
        time.sleep(1.0)
        cur = _qr_url_safe()
        if cur and cur != old:
            _qr_seen.update(url=cur, since=now)
            return _render_qr(cur)
    # 仍没变化：至少刷新时间戳并重新出图，前端不会一直显示"过期"
    cur = _qr_url_safe() or old
    if cur:
        _qr_seen.update(url=cur, since=now)
        return _render_qr(cur)
    return ""


def _qr_url_safe() -> str:
    """_qr_url 的不抛错版本（登录服务重启中接口会连不上，不该把面板二维码接口打挂）"""
    try:
        return _qr_url()
    except Exception:  # noqa: BLE001
        return ""


_qr_restart_at = 0.0  # 上次"码卡死自动重启登录服务"的时间（10 分钟内只来一次，防风控）


def _restart_login_for_qr():
    """二维码长时间不更新（RefreshQRcode 一直 restarting/回旧码）时，
    重启 NapCat 让登录服务重新出码。这是唯一能真正换新码的办法；
    限 10 分钟一次，且已登录时绝不动。"""
    global _last_start, _qr_restart_at
    if time.time() - _qr_restart_at < 600:
        return False
    if login_status().get("isLogin"):
        return False
    _qr_restart_at = time.time()
    try:
        with _lock:
            if _proc and _proc.poll() is None:
                _tree_kill(_proc.pid)
            _proc = None
            _last_start = 0.0  # 卡死重启不受 5 分钟冷却限制（这不是用户反复登录）
            _kill_webui_holder()  # 僵尸 QQ 不清掉，start() 会把它当"已在跑"认领回去
        start()
        return True
    except Exception:  # noqa: BLE001
        return False


def qr_data_url() -> str:
    """登录二维码 data URL。

    注意：NapCat 只在启动时写一次 cache/qrcode.png，之后刷新二维码不会再更新该文件，
    直接读文件会一直显示过期码。所以改为用 WebUI API 拿实时 URL 后本地出图
    （NapCat 的 /QQLogin/GetQQLoginQrcode 只返回 URL，不出图）。
    """
    raw = _qr_url_safe()
    if raw.startswith("data:image"):  # 某些 NapCat 版本直接返回图片
        return raw
    if raw:
        now = time.time()
        if _qr_seen["url"] != raw:
            _qr_seen.update(url=raw, since=now)
        elif now - _qr_seen["since"] > 110:  # 同一张码挂太久：主动续期
            fresh = refresh_qr()
            if fresh:
                return fresh
            # 续期拿不到新码：登录服务多半卡死了，重启它重新出码
            if _restart_login_for_qr():
                time.sleep(6.0)  # 等登录服务起来
                raw = _qr_url_safe()
                if raw and raw != _qr_seen["url"]:
                    _qr_seen.update(url=raw, since=time.time())
                    return _render_qr(raw)
            _qr_seen.update(url=_qr_seen["url"], since=now)
        return _render_qr(raw)
    # 兜底：旧版 NapCat 落盘的图片。只认 2 分钟内的，旧码扫了必报"二维码超时"，
    # 显示过期码反而误导用户
    p = os.path.join(NAPCAT_DIR, "cache", "qrcode.png")
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 120:
            import base64
            b = open(p, "rb").read()
            if b:
                return "data:image/png;base64," + base64.b64encode(b).decode()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _onebot_cfg_path(uin: str) -> str:
    return os.path.join(NAPCAT_DIR, "config", f"onebot11_{uin}.json")


def ensure_onebot_config(uin: str) -> bool:
    """写入/合并反向 WS 配置（指向 NoneBot 8901）。无需修改返回 False"""
    p = _onebot_cfg_path(uin)
    cfg = {}
    if os.path.exists(p):
        try:
            cfg = json.load(open(p, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
    net = cfg.setdefault("network", {})
    existing = net.get("websocketClients") or []
    mine = [c for c in existing if isinstance(c, dict) and c.get("url") == ONEBOT_WS_URL]
    if mine and all(c.get("enable") for c in mine):
        return False
    entry = {"enable": True, "name": "d2query-panel", "url": ONEBOT_WS_URL,
             "reportSelfMessage": False, "messagePostFormat": "array",
             "token": "", "debug": False,
             "heartInterval": 30000, "reconnectInterval": 5000}
    rest = [c for c in existing if not (isinstance(c, dict) and c.get("url") == ONEBOT_WS_URL)]
    net["websocketClients"] = rest + [entry]
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return True


def ensure_ob11_via_api() -> bool:
    """通过 WebUI API 给当前登录账号加反向 WS 客户端（运行时即生效，
    避免改文件被 NapCat 退出回写覆盖）。返回是否做了修改"""
    cfg = _api_post("/OB11Config/GetConfig")
    net = (cfg or {}).get("network")
    if not isinstance(net, dict):
        return False
    existing = net.get("websocketClients") or []
    mine = [c for c in existing if isinstance(c, dict) and c.get("url") == ONEBOT_WS_URL]
    if mine and all(c.get("enable") for c in mine):
        return False  # 已配置且启用
    entry = {"enable": True, "name": "d2query-panel", "url": ONEBOT_WS_URL,
             "reportSelfMessage": False, "messagePostFormat": "array",
             "token": "", "debug": False,
             "heartInterval": 30000, "reconnectInterval": 5000}
    rest = [c for c in existing if not (isinstance(c, dict) and c.get("url") == ONEBOT_WS_URL)]
    net["websocketClients"] = rest + [entry]
    _api_post("/OB11Config/SetConfig", {"config": json.dumps(cfg, ensure_ascii=False)})
    return True


def _uin_file() -> str:
    return os.path.join(NAPCAT_DIR, "config", "panel_uin.txt")


def _saved_uin() -> str:
    """上次登录的 QQ 号：优先状态文件，其次 config/napcat_<uin>.json 文件名"""
    try:
        v = open(_uin_file(), encoding="utf-8").read().strip()
        if v.isdigit():
            return v
    except Exception:  # noqa: BLE001
        pass
    return _uin_from_files()


def _remember_uin(uin: str):
    if not uin:
        return
    try:
        with open(_uin_file(), "w", encoding="utf-8") as f:
            f.write(uin)
    except Exception:  # noqa: BLE001
        pass


def _uin_from_files() -> str:
    """从 config/napcat_<uin>.json 文件名提取当前账号 uin"""
    import glob
    import re
    for f in glob.glob(os.path.join(NAPCAT_DIR, "config", "napcat_*.json")):
        m = re.search(r"napcat_(\d+)\.json$", os.path.basename(f))
        if m:
            return m.group(1)
    return ""


def _watch_login():
    """检测到登录后下发反向 WS 配置。注意 CheckLoginStatus 不返回 uin，
    API 路径不需要 uin；文件回退路径才从配置文件名补 uin"""
    global _qq
    for _ in range(300):  # 最长等 ~15 分钟扫码
        if not is_running() and not login_qr_ready():
            return
        st = login_status()
        if st.get("isLogin"):
            _qq = st.get("uin") or _uin_from_files() or _qq
            _remember_uin(_qq)
            try:
                ensure_ob11_via_api()
            except Exception:  # noqa: BLE001
                # 不自动重启（反复登录会触发 QQ 风控）：写好配置，下次启动生效
                if _qq:
                    ensure_onebot_config(_qq)
            return
        time.sleep(3)


def status() -> dict:
    alive = is_running() or login_qr_ready()
    st = {"running": alive, "webui": False, "isLogin": False,
          "uin": "", "qq": _qq, "log": LOG_FILE, "webui_url": webui_url()}
    if alive:
        st.update(login_status())
        st["qq"] = st["uin"] or _qq
    return st


if __name__ == "__main__":
    import httpx
    res = start()
    print(res)
    for _ in range(15):
        time.sleep(2)
        ok = login_qr_ready()
        print("WebUI:", ok)
        if ok:
            break
    if os.path.exists(LOG_FILE):
        tail = open(LOG_FILE, encoding="utf-8", errors="ignore").read()
        tail = re.sub(r"\x1b\[[0-9;]*m", "", tail)
        print("--- 日志尾部 ---")
        print("\n".join(tail.splitlines()[-15:]))
