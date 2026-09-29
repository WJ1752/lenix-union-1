"""QQ bot 运行时：在同进程后台线程里跑 NoneBot（OneBot v11 反向 WS 服务端）

NapCat / LLOneBot 等协议端（负责 QQ 扫码登录）以反向 WS 接入：
  ws://127.0.0.1:8901/onebot/v11/ws

群开关等配置存 bot_config.json：
  {"enabled_groups": ["123456"]}   # 空 = 所有群都响应
"""
import json
import os
import sys
import threading

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


def _run():
    try:
        import nonebot
        from nonebot.adapters.onebot.v11 import Adapter

        nonebot.init(driver="~fastapi", host="127.0.0.1", port=BOT_PORT,
                     log_level="WARNING", command_start={"/"})
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
    except Exception as exc:  # noqa: BLE001
        print(f"[bot] QQ bot 线程启动失败（不影响网页查询）：{exc}")


def start():
    threading.Thread(target=_run, daemon=True, name="qq-bot").start()
