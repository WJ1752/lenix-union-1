"""Bot 消息日志：内存环形缓冲，记录群/私聊收到的消息与机器人回复

面板（webui.py）与插件（nonebot_plugins/destiny2.py）在同一进程内共享本模块。
不落盘，最多保留 MAX_LEN 条，进程重启即清空。
"""
import threading
import time
from collections import deque

MAX_LEN = 500

_lock = threading.Lock()
_logs: deque = deque(maxlen=MAX_LEN)


def add(direction: str, event=None, text: str = "", group_id="", user_id="",
        nickname: str = "", extra: dict | None = None) -> dict:
    """direction: "in" 收到 / "out" 回复"""
    entry = {
        "ts": time.time(),
        "time": time.strftime("%H:%M:%S"),
        "dir": direction,
        "group_id": str(group_id or ""),
        "user_id": str(user_id or ""),
        "nickname": nickname or "",
        "text": text or "",
    }
    if extra:
        entry.update(extra)
    with _lock:
        _logs.append(entry)
    return entry


def recent(limit: int = 200, group_id: str = "") -> list:
    """最新在前；group_id 非空则只看该群（私聊用 "private"）"""
    with _lock:
        items = list(_logs)
    if group_id:
        if group_id == "private":
            items = [x for x in items if not x["group_id"]]
        else:
            items = [x for x in items if x["group_id"] == str(group_id)]
    return items[-limit:][::-1]


def groups() -> list:
    """出现过的群号（用于面板筛选），按最近活跃排序"""
    with _lock:
        seen: dict = {}
        for i, x in enumerate(_logs):
            if x["group_id"]:
                seen[x["group_id"]] = i
    return [g for g, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def clear():
    with _lock:
        _logs.clear()
