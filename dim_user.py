"""DIM 板块的本地数据：物品标签/备注 + 自建配装（DIM 的「DIM 配装」）。

为什么存本地：Bungie 官方 API 没有标签接口（实测 SetTag/SetItemTag 都是 404，只有
SetItemLockState / SetQuestTrackedState），DIM 自己也是把标签和备注存在浏览器本地。
我们既然有服务端，就存在服务端文件里，浏览器换设备也能用同一份。

数据文件 dim_user.json 放在「程序所在目录」（源码目录 / exe 所在目录），跟
manifest_index 只读索引分开，升级只覆盖索引不会动用户数据。

写入用「临时文件 + os.replace」原子替换，避免写一半断电把文件写坏。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

FILE_NAME = "dim_user.json"
_LOCK = asyncio.Lock()
_DATA: dict | None = None

EMPTY = {"tags": {}, "loadouts": []}


def _base_dir() -> str:
    """可写目录：源码目录优先；打包后 exe 就是自己的目录（启动时 cwd 也在这里）"""
    here = os.path.dirname(os.path.abspath(__file__))
    if not getattr(sys, "frozen", False):
        return here
    return os.path.dirname(os.path.abspath(sys.executable))


def path() -> str:
    return os.path.join(_base_dir(), FILE_NAME)


def load() -> dict:
    global _DATA
    if _DATA is None:
        try:
            with open(path(), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa: BLE001  文件不存在/损坏都不该让页面挂掉
            d = {}
        _DATA = {"tags": d.get("tags") or {}, "loadouts": d.get("loadouts") or []}
    return _DATA


def save() -> None:
    d = load()
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path())


def tid(iid: str, item_hash) -> str:
    """标签/备注的键：有实例 id 用实例（同一件装备的不同副本可以分开标），
    没有实例的堆叠物品（材料/消耗品）退回按物品 hash 标。"""
    return str(iid) if iid else "h" + str(item_hash)


# ---------- 标签 / 备注 ----------


def tags() -> dict:
    return load()["tags"]


def set_tag(iid: str, item_hash, tag: str, note: str | None = None) -> dict:
    k = tid(iid, item_hash)
    t = load()["tags"]
    cur = dict(t.get(k) or {})
    if tag is not None:
        if tag:
            cur["t"] = tag
        else:
            cur.pop("t", None)
    if note is not None:
        if note:
            cur["n"] = note[:500]
        else:
            cur.pop("n", None)
    if cur:
        t[k] = cur
    else:
        t.pop(k, None)
    save()
    return t.get(k) or {}


# ---------- 自建配装 ----------


def loadouts() -> list:
    return load()["loadouts"]


def _new_id() -> str:
    return "l%d" % int(time.time() * 1000)


def _clean_item(it: dict) -> dict | None:
    if not it:
        return None
    h = it.get("h")
    if h in (None, "", 0, "0"):
        return None
    try:
        h = int(h)
    except (TypeError, ValueError):
        return None
    out = {"h": h, "i": str(it.get("i") or "")}
    plugs = [int(p) for p in (it.get("plugs") or []) if str(p).isdigit()]
    if plugs:
        out["plugs"] = plugs
    return out


def _clean(lo: dict, prev: dict | None = None) -> dict:
    items, seen = [], set()
    for it in lo.get("items") or []:
        c = _clean_item(it)
        if not c or c["h"] in seen:
            continue
        seen.add(c["h"])
        items.append(c)
    now = int(time.time())
    return {
        "id": (prev or {}).get("id") or lo.get("id") or _new_id(),
        "name": (lo.get("name") or "未命名配装").strip()[:60],
        "cls": int(lo.get("cls", 3)),
        "icon": str(lo.get("icon") or ""),
        "color": str(lo.get("color") or ""),
        "notes": str(lo.get("notes") or "")[:500],
        "params": lo.get("params") or None,     # 配装器存下来的搜索条件，方便二次调整
        "items": items,
        "created": (prev or {}).get("created") or now,
        "updated": now,
    }


def put_loadout(lo: dict) -> dict:
    ls = load()["loadouts"]
    lid = lo.get("id")
    prev = None
    if lid:
        prev = next((x for x in ls if x["id"] == lid), None)
    rec = _clean(lo, prev)
    if prev:
        ls[ls.index(prev)] = rec
    else:
        ls.insert(0, rec)
    save()
    return rec


def dup_loadout(lid: str) -> dict | None:
    ls = load()["loadouts"]
    src = next((x for x in ls if x["id"] == lid), None)
    if not src:
        return None
    copy = dict(src)
    copy["id"] = ""
    copy["name"] = src["name"] + " 副本"
    return put_loadout(copy)


def del_loadout(lid: str) -> bool:
    ls = load()["loadouts"]
    n = len(ls)
    load()["loadouts"] = [x for x in ls if x["id"] != lid]
    if len(load()["loadouts"]) == n:
        return False
    save()
    return True


def reorder(ids: list) -> list:
    ls = load()["loadouts"]
    by = {x["id"]: x for x in ls}
    out = [by[i] for i in ids if i in by] + [x for x in ls if x["id"] not in set(ids)]
    load()["loadouts"] = out
    save()
    return out


def export_code(lid: str) -> str:
    """分享码：DIM 用 dim.gg 短链，我们自用就把配装本体 base64 出去"""
    import base64
    lo = next((x for x in load()["loadouts"] if x["id"] == lid), None)
    if not lo:
        return ""
    body = {"name": lo["name"], "cls": lo["cls"], "icon": lo["icon"], "color": lo["color"],
            "notes": lo.get("notes") or "", "items": [{"h": i["h"], "i": i.get("i") or ""}
                                                       for i in lo["items"]]}
    return base64.urlsafe_b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()


def import_code(code: str) -> dict:
    import base64
    try:
        raw = base64.urlsafe_b64decode(code.encode() + b"=" * (-len(code) % 4))
        d = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("分享码无效（解析失败）") from exc
    if not isinstance(d, dict) or "items" not in d:
        raise RuntimeError("分享码内容不对")
    d["id"] = ""
    return put_loadout(d)
