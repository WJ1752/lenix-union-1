"""D2 查询站：原生窗口启动器（exe 入口）
- 窗口内直接使用查询站（不再弹 cmd/浏览器）
- QQ bot 后台线程随程序启动，NapCat 反向WS接入后在"Bot面板"里管理群聊
"""
import atexit
import os
import socket
import sys
import threading
import time

_LOG_LIMIT = 8 * 1024 * 1024            # 单个日志文件的上限，超过就轮转
_SESSION_BEGIN = "===== 会话开始"
_SESSION_END = "===== 会话正常结束"


def _read_tail(path, lines=15, max_bytes=65536):
    """取日志末尾若干行，用来回答「上次跑到哪断的」。
    从尾部 seek 读而不是整份读：日志随运行时长一直涨，整份读进内存是白吃。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            start = max(0, fh.tell() - max_bytes)
            fh.seek(start)
            rows = fh.read().splitlines()
        if start > 0 and rows:
            rows = rows[1:]              # 开头那行多半是被字节窗口截断的半行，留着只会误导诊断
        return [r.decode("utf-8", "replace") for r in rows[-lines:]]
    except OSError:
        return []                        # 文件不存在/读不到：当没有历史


def _rotate(path, limit=_LOG_LIMIT, keep=2):
    """日志超过 limit 才轮转：exe_stdout.log → exe_stdout.1.log（旧的 .1 → .2 …），
    只留 keep 份。轮转而不是清空，是为了重启后还能从备份里翻更早的输出。"""
    try:
        if os.path.getsize(path) <= limit:
            return

        base, ext = os.path.splitext(path)

        def numbered(n):
            return f"{base}.{n}{ext}"

        oldest = numbered(keep)
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(keep - 1, 0, -1):
            if os.path.exists(numbered(i)):
                os.replace(numbered(i), numbered(i + 1))
        os.replace(path, numbered(1))
    except OSError:
        pass                             # 轮转失败只是损失旧日志，不能拦住启动


def _ended_normally(tail):
    """判断上一次会话是不是带着结束标记正常收的尾：标记必须是尾部最后一行非空内容。
    不能在尾部里简单「找标记有没有出现过」——崩溃的那次会把更早会话的尾部连同它的
    结束标记原样回放进日志，出现过≠这次写的，那样会把崩溃误报成正常退出。"""
    for line in reversed(tail):
        if line.strip():
            return line.startswith(_SESSION_END)
    return False


def _write_exit_mark(stream):
    """atexit 收尾：写下正常退出标记，下次启动才能分辨上次是收工还是被强杀。
    写失败要静默——退出阶段再抛异常只会刷一屏没人会看的 traceback。"""
    try:
        stream.write(f"{_SESSION_END} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        stream.flush()
    except Exception:  # noqa: BLE001
        pass


def _open_log_stream(path):
    """以追加模式打开运行日志，并写清会话边界（分隔行 + 回放上次尾部 + 强杀提示）。
    顺序要紧：先读旧文件尾部再轮转——轮转之后 path 已经是新文件，读不到上次的断点了。
    任何异常都退到 devnull：日志功能再出岔子也不能拦住启动。"""
    stream = None
    try:
        previous = _read_tail(path)
        _rotate(path)
        stream = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        stream.write(f"{_SESSION_BEGIN} {stamp}（pid {os.getpid()}）=====\n\n")
        if previous:
            stream.write(f"----- 上次会话最后 {len(previous)} 行（诊断断点用）-----\n")
            for line in previous:
                stream.write(line + "\n")
            if not _ended_normally(previous):
                stream.write("[日志] 上次会话未正常结束（没写正常退出标记，"
                             "多半是被强杀/崩溃），上面就是它的最后输出\n")
            # 回放块的收尾线不能省：旧尾部很可能以更早会话的结束标记结尾，
            # 有这一行挡着，_ended_normally 才不会把它当成这次会话的标记
            stream.write("----- 上次会话回放结束 -----\n\n")
        atexit.register(_write_exit_mark, stream)
        return stream
    except Exception:  # noqa: BLE001  连日志都开不了就静默跑
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        return open(os.devnull, "w", encoding="utf-8")


# windowed exe（console=False）没有控制台，stdout/stderr 是 None，
# loguru/uvicorn 往 None 写日志会直接崩——重定向到 exe 同目录的日志文件
if sys.stdout is None or sys.stderr is None:
    try:
        _log = _open_log_stream(os.path.join(os.path.dirname(sys.executable), "exe_stdout.log"))
    except Exception:  # noqa: BLE001  连日志都开不了就静默跑
        _log = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr = _log

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
