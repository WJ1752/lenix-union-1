"""Cloudflare 免费临时隧道（cloudflared quick tunnel）：给 /登录 一个公网 https 回跳地址

原理：cloudflared.exe（免安装、免注册账号，仓库根/部署目录放一份）起一条临时隧道指向
本机 127.0.0.1:8903（launcher 里只挂 Bungie 回调路由的 HTTP 小服务），得到
https://xxxx.trycloudflare.com 公网地址。/登录 的授权链接把回跳指到该地址，任何网络的
玩家点完「允许」浏览器直接落回 bot → 自动绑定，不用再手动粘贴回调（小日向同款体验）。

- 临时域名每次起隧道随机；按需拉起（首次 /登录 等 3~15 秒）后进程内常驻复用
- Bungie 换 token 校验的是「发授权码时的 redirect_uri」，与开发者页注册值无关
  （实测 authorize 页也不校验），所以临时域名无需去 Bungie 应用页登记
- 没放 cloudflared.exe / 隧道起不来 → callback_origin() 返回空串，/登录 自动退回
  本机 + 局域网链接与 /回调 粘贴流程，不影响其它功能
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_st: dict = {"proc": None, "url": "", "tried": False, "lock": None}
PORT = 8903                    # 本机 HTTP 回调口（launcher 起 bungie_tls_app）


def _exe_path() -> str:
    """cloudflared.exe 定位：源码目录 → exe 目录"""
    for base in (os.path.dirname(os.path.abspath(__file__)),
                 os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""):
        if base:
            p = os.path.join(base, "cloudflared.exe")
            if os.path.exists(p):
                return p
    return ""


def _reader(proc: subprocess.Popen):
    """读 cloudflared 的 stderr，抓 trycloudflare.com 公网地址"""
    try:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace")
            m = _URL_RE.search(line)
            if m:
                _st["url"] = m.group(0)
                break
    except Exception:  # noqa: BLE001
        pass


def callback_origin(wait: float = 12.0) -> str:
    """确保隧道在跑，返回公网回跳源（https://xxx.trycloudflare.com）；失败返回空串。"""
    if _st["url"]:
        return _st["url"]
    if _st["proc"] is not None and _st["proc"].poll() is not None:
        _st["proc"], _st["url"], _st["tried"] = None, "", False   # 进程死了重试一次
    if _st["tried"]:
        return _st["url"]
    _st["tried"] = True
    exe = _exe_path()
    if not exe:
        return ""
    lock = _st["lock"] or threading.Lock()
    _st["lock"] = lock
    with lock:
        if _st["url"]:
            return _st["url"]
        try:
            proc = subprocess.Popen(
                [exe, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        except Exception:  # noqa: BLE001
            return ""
        _st["proc"] = proc
        threading.Thread(target=_reader, args=(proc,), daemon=True).start()
        deadline = time.time() + wait
        while time.time() < deadline and not _st["url"]:
            if proc.poll() is not None:
                return ""
            time.sleep(0.3)
        return _st["url"]


if __name__ == "__main__":
    print("cloudflared:", _exe_path() or "未找到")
    print("公网回跳源:", callback_origin() or "起不来")
