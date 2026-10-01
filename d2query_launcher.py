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
    port = start
    while port < start + 20:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return start


def _serve(port):
    import bot_runtime
    import webui
    bot_runtime.start()
    uvicorn.run(webui.app, host="127.0.0.1", port=port, log_level="warning")


def _serve_tls():
    """另起一个带自签证书的 https 口，专门收 Bungie 授权回跳（Bungie 只认 https）"""
    try:
        import bungie_auth
        import webui
        cert, key = bungie_auth.cert_files()
        if not (cert and key):
            return
        uvicorn.run(webui.app, host="127.0.0.1", port=bungie_auth.TLS_PORT,
                    ssl_certfile=cert, ssl_keyfile=key, log_level="warning")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    threading.Thread(target=_serve_tls, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            break
        except OSError:
            time.sleep(0.2)
    webview.create_window("D2 查询站", url, width=1220, height=860)
    webview.start()
