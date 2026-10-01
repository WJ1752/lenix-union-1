"""QQ bot 运行时：在同进程后台线程里跑 NoneBot（OneBot v11 反向 WS 服务端）

NapCat / LLOneBot 等协议端（负责 QQ 扫码登录）以反向 WS 接入：
  ws://127.0.0.1:8901/onebot/v11/ws

群开关等配置存 bot_config.json：
  {"enabled_groups": ["123456"]}   # 空 = 所有群都响应
"""
import json
import os
import socket
import sys
import threading
import time

CONFIG_FILE = "bot_config.json"
BOT_PORT = 8901


def load_config() -> dict:
    try:
        return json.load(open(CONFIG_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg: dict):
    json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def enabled_groups() -> list:
    return load_config().get("enabled_groups") or []


def set_enabled_groups(gids: list):
    cfg = load_config()
    cfg["enabled_groups"] = [str(g) for g in gids]
    save_config(cfg)


def get_bots() -> dict:
    """已连接的协议端 bot {uin: Bot}；nonebot 未启动时返回空"""
    try:
        from nonebot import get_driver
        return dict(get_driver().bots)
    except Exception:  # noqa: BLE001
        return {}


BIND_WAIT_SEC = 300   # 等 8901 可用的上限；Windows 的 TIME_WAIT 约 4 分钟，留够余量

TLS_PORT = 8902       # 与 bungie_auth.TLS_PORT 一致：Bungie OAuth 的回跳口

# 这两个端口是同进程里另外两个服务写死的：8901 被协议端 NapCat 的 onebot11_<QQ>.json
# 指着，8902 写进了 Bungie 后台的回调地址。主界面挑自己端口时必须绕开——
# 一旦抢到 8901，协议端就会连到网页应用上，面板永远"未连接"，且重启也治不好。
RESERVED_PORTS = frozenset({BOT_PORT, TLS_PORT})


def port_usable(port: int) -> bool:
    """这个端口现在能不能拿来监听。分两步，因为 Windows 的 SO_REUSEADDR 允许"抢绑"：

    ① 连得上说明有人在听 —— 那绝不能抢，哪怕技术上绑得上（抢了就是两个监听者抢连接）；
    ② 连不上再试绑，这一步开 SO_REUSEADDR：上个实例留下的 TIME_WAIT 只是残留状态，
       裸 bind 会把它误判成"被占用"，白白把端口一路挤走。
    """
    with socket.socket() as c:
        c.settimeout(0.25)
        if c.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_bindable(port: int, timeout: float = BIND_WAIT_SEC) -> bool:
    """等到 port 真能用为止（协议端每 5 秒重连一次，等得起）。

    只做裸 bind 的老逻辑会把上个实例留下的 TIME_WAIT 当成"端口被占用"，于是
    nonebot 起来即报 10048 退出、协议端从此永远"未连接"；而且它只等 60 秒。
    """
    deadline = time.time() + timeout
    while True:
        if port_usable(port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def _serve(state: dict) -> None:
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    if not _wait_bindable(BOT_PORT, BIND_WAIT_SEC):
        # 等够了还绑不上，说明已经有实例在 8901 上听着（不是 TIME_WAIT 残留）。
        # 这时绝不能硬起：uvicorn 也带 SO_REUSEADDR，硬绑会变成两个监听者抢连接，
        # 协议端会被抢到另一个实例上，正主反而"未连接"。
        print(f"[bot] {BOT_PORT} 已超 {BIND_WAIT_SEC}s 仍被占用：多半已有一个实例在跑，"
              "本实例不启动 QQ bot（网页查询不受影响）")
        return
    nonebot.init(driver="~fastapi", host="127.0.0.1", port=BOT_PORT,
                 log_level="WARNING", command_start={"/"})
    state["inited"] = True          # 到这里之后再失败就不能重来了，见 _run
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    # 打包 exe 时插件目录在 exe 旁边；开发时在源码目录
    if getattr(sys, "frozen", False):
        plugin_dir = os.path.join(os.path.dirname(sys.executable), "nonebot_plugins")
    else:
        plugin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nonebot_plugins")
    # 目录含 __init__.py 时 nonebot 按包导入，需保证父目录在 sys.path
    plugin_parent = os.path.dirname(plugin_dir)
    if plugin_parent not in sys.path:
        sys.path.insert(0, plugin_parent)
    nonebot.load_plugins(plugin_dir)
    nonebot.run()


def _run():
    """启动 QQ bot 线程。

    nonebot.init 之前失败（端口迟迟不可用、驱动起不来）可以整段重来一次；
    init 之后失败（uvicorn 最终没绑上等）不能再 init 一遍，只记日志、等下回开程序。
    端口被别人占着时 _serve 直接返回，不重试——那是"已经有实例在跑"，不是故障。
    """
    state: dict = {}
    for attempt in (1, 2):
        try:
            _serve(state)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] QQ bot 线程第 {attempt} 次启动失败：{exc}")
            if state.get("inited"):
                return
            time.sleep(3)
    print("[bot] QQ bot 线程起不来（不影响网页查询）：端口一直没就绪，重启程序可再试")


def start():
    threading.Thread(target=_run, daemon=True, name="qq-bot").start()
