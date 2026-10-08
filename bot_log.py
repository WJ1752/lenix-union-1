"""Bot 消息日志：内存环形缓冲，记录群/私聊收到的消息与机器人回复

面板（webui.py）与插件（nonebot_plugins/destiny2.py）在同一进程内共享本模块。
不落盘，最多保留 MAX_LEN 条，进程重启即清空。

例外：**发不出去的图片**要落盘（unsent_images/）——卡片是现渲染现发的，进程里
没有第二份，QQ 侧发送失败（被动回复窗口过期/掉线/风控）后用户就再也看不到那张
卡了。落盘后面板能直接预览、重发，也能打开文件夹人工检查。
"""
import datetime
import os
import re
import threading
import time
from collections import deque

# 全盘时钟口径：中国北京时间（本机时区变了也不会把日志时间写成别的时区）
_TZ8 = datetime.timezone(datetime.timedelta(hours=8))


def _cn_now(fmt: str) -> str:
    return datetime.datetime.now(_TZ8).strftime(fmt)

MAX_LEN = 500
UNSENT_DIR = "unsent_images"     # 发送失败的图片存这里（与 bot_config.json 同目录约定）
UNSENT_KEEP = 120                # 只留最近这么多张，避免越积越多

_lock = threading.Lock()
_logs: deque = deque(maxlen=MAX_LEN)


def unsent_dir(create: bool = True) -> str:
    """发送失败图片的目录（相对当前工作目录，与 bot_config.json 同口径）"""
    p = os.path.abspath(UNSENT_DIR)
    if create:
        try:
            os.makedirs(p, exist_ok=True)
        except Exception:  # noqa: BLE001 建不出来就让调用方拿不到路径
            return ""
    return p


def _safe(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(name or "")).strip(" ._")
    return name[:60] or "card"


def save_unsent(png: bytes, label: str = "", group_id: str = "",
                user_id: str = "") -> str:
    """把发送失败的图片存盘 → 返回文件名（供面板预览/重发；存不下返回空串）

    文件名带时间与收件人，面板上「打开文件夹」后一眼能认出是哪条指令的哪张卡。
    """
    d = unsent_dir()
    if not d or not png:
        return ""
    stamp = _cn_now("%m%d_%H%M%S")
    who = (f"群{group_id}" if group_id else (f"用户{user_id}" if user_id else "未知"))
    fn = f"{stamp}_{_safe(who)}_{_safe(label)}.png"
    try:
        with open(os.path.join(d, fn), "wb") as f:
            f.write(png)
    except Exception:  # noqa: BLE001 写不进去只是没得预览，不影响主流程
        return ""
    _prune_unsent(d)
    return fn


def unsent_path(name: str) -> str:
    """文件名 → 绝对路径（只认目录内的文件名，挡掉路径穿越）"""
    d = unsent_dir(create=False)
    if not d or not name:
        return ""
    p = os.path.abspath(os.path.join(d, os.path.basename(str(name))))
    return p if os.path.dirname(p) == d and os.path.exists(p) else ""


def unsent_list() -> list[dict]:
    """已存下的失败图片（新→旧）：文件名 / 大小 / 时间"""
    d = unsent_dir(create=False)
    out: list[dict] = []
    if not d:
        return out
    try:
        for fn in os.listdir(d):
            if not fn.lower().endswith(".png"):
                continue
            st = os.stat(os.path.join(d, fn))
            out.append({"name": fn, "size": st.st_size, "ts": st.st_mtime})
    except Exception:  # noqa: BLE001
        return out
    return sorted(out, key=lambda x: -x["ts"])


def _prune_unsent(d: str) -> None:
    files = unsent_list()
    if len(files) <= UNSENT_KEEP:
        return
    for it in files[UNSENT_KEEP:]:
        try:
            os.remove(os.path.join(d, it["name"]))
        except Exception:  # noqa: BLE001
            pass


def add(direction: str, event=None, text: str = "", group_id="", user_id="",
        nickname: str = "", extra: dict | None = None) -> dict:
    """direction: "in" 收到 / "out" 回复"""
    entry = {
        "ts": time.time(),
        "time": _cn_now("%H:%M:%S"),
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
