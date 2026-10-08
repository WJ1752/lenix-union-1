"""D2 查询站：原生窗口启动器（exe 入口）
- 窗口内直接使用查询站（不再弹 cmd/浏览器）
- QQ bot 后台线程随程序启动，NapCat 反向WS接入后在"Bot面板"里管理群聊
"""
import socket
import sys
import threading
import time

# windowed exe（console=False）没有控制台，stdout/stderr 是 None，
# loguru/uvicorn 往 None 写日志会直接崩——重定向到 exe 同目录的日志文件
if sys.stdout is None or sys.stderr is None:
    try:
        import os
        _log = open(os.path.join(os.path.dirname(sys.executable), "exe_stdout.log"),
                    "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = _log
    except Exception:  # noqa: BLE001  连日志都开不了就静默跑
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn
import webview


def _free_port(start=8900):
    """挑主界面端口。端口契约（哪些能用、怎么算"可用"）统一放在 bot_runtime：
    8901/8902 是同进程另外两个服务写死的口，必须绕开，否则会把协议端坑成"未连接"。"""
    from bot_runtime import RESERVED_PORTS, port_usable
    for port in range(start, start + 20):
        if port in RESERVED_PORTS:
            continue
        if port_usable(port):
            return port
    return start


def _serve(port):
    import bot_runtime
    import webui
    bot_runtime.start()
    threading.Thread(target=_autostart_napcat, daemon=True).start()
    uvicorn.run(webui.app, host="127.0.0.1", port=port, log_level="warning")


def _autostart_napcat():
    """启动时自动重连 NapCat：上次登录过就走快速登录，不用手点「启动并扫码登录」。
    首次使用 / 手动重置过则不动（napcat_runtime.autostart 里判断）。
    随后挂上看门狗：NapCat 崩了（进程树整个没了、反向 WS 也回不来）自动重新拉起，
    否则机器人会一直"离线"到人工重启为止。"""
    try:
        time.sleep(3)  # 等面板端口先起来，扫码页/状态轮询才正常
        import napcat_runtime
        r = napcat_runtime.autostart()
        print(f"[napcat] 自动重连：{'已拉起' if r.get('started') else r}")
        napcat_runtime.start_watchdog()
    except Exception as exc:  # noqa: BLE001  自动重连失败不影响主程序
        print(f"[napcat] 自动重连失败：{exc}")


def _serve_tls():
    """带自签证书的 https 口，专门收 Bungie 授权回跳（Bungie 只认 https）。

    绑 0.0.0.0：/登录 的局域网链接把回跳指到本机内网 IP，同一 Wi-Fi 下的玩家
    点完「允许」直接落到授权成功页，不用再手动粘贴回调。这里只挂 bungie_tls_app
    （仅回调路由），不把整个面板暴露给局域网。"""
    try:
        import bungie_auth
        import webui
        cert, key = bungie_auth.cert_files()
        if not (cert and key):
            return
        uvicorn.run(webui.bungie_tls_app, host="0.0.0.0", port=bungie_auth.TLS_PORT,
                    ssl_certfile=cert, ssl_keyfile=key, log_level="warning")
    except Exception:  # noqa: BLE001
        pass


def _serve_tunnel_http():
    """本机 8903：给 cloudflared 隧道转发的 HTTP 回调口（只挂 Bungie 回调路由，
    只听 127.0.0.1，公网流量经 Cloudflare 进来也只能碰到授权回调这一条路由）"""
    try:
        import webui
        uvicorn.run(webui.bungie_tls_app, host="127.0.0.1", port=8903,
                    log_level="warning")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    threading.Thread(target=_serve_tls, daemon=True).start()
    threading.Thread(target=_serve_tunnel_http, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            break
        except OSError:
            time.sleep(0.2)
    webview.create_window("D2 查询站", url, width=1220, height=860)
    webview.start()
