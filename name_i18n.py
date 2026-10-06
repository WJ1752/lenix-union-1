"""英文 / 繁体中文查询索引（build_locale_index.py 产出的 manifest_index/name_i18n.json）

指令触发词保持中文（/武器查询、/perk查询 …）：本模块只让**查询用的名字**认英文与台服繁体
—— 武器名（Fatebringer）、perk 名（Incandescent）、副本名（Crota's End）、护甲套装名
（Seventh Seraph）、筛选词（Hand Cannon）、仓库关键词（Vex Mythoclast）——都命中原有的
中文数据，匹配之后的逻辑一行不改。

**不做繁简字形转换**：台服叫法是词形差异（克洛塔/克羅塔、突袭/掠夺），只有 Bungie 官方
zh-cht 名字靠得住，所以索引里存的就是官方三语名。

不 import destiny_data / weapon_filter（它们反过来 import 本模块）：自己复制一份 _idx_file
三级定位（源码目录 → PyInstaller 打包资源 → 当前目录）；缺文件时全部返回空结果 / 原词，
原有功能不受影响。
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata

_DATA: dict | None = None
_ITEM_IDX: dict[str, list[tuple[str, list[str]]]] = {}


def _path(name: str) -> str:
    """与 destiny_data._idx_file 同规则：源码目录 → PyInstaller 资源 → 当前目录"""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest_index", name)
    if os.path.exists(here):
        return here
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "manifest_index", name)
    if os.path.exists(bundled):
        return bundled
    return os.path.join("manifest_index", name)


def _norm(s: str) -> str:
    """与 destiny_data.norm_key 同规则：NFKC → 小写 → 剔除所有非文字字符"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^\w]+", "", s)


def _data() -> dict:
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.load(open(_path("name_i18n.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001  没建过索引就整体降级
            _DATA = {}
    return _DATA


def ready() -> bool:
    """多语言索引是否可用（构建脚本没跑过时为 False）"""
    return bool(_data())


def _names(kind: str, q: str, limit: int) -> list[str]:
    """三语名 → hash/ref 列表：归一化精确命中优先，其次前缀命中，最后子串命中"""
    idx = _data().get(kind) or {}
    nk = _norm(q)
    if not idx or not nk:
        return []
    hit = idx.get(nk)
    if hit:
        return list(hit)[:limit]
    pre: list[str] = []
    sub: list[str] = []
    for k, v in idx.items():
        if k.startswith(nk):
            pre.extend(v)
        elif nk in k:
            sub.extend(v)
    return (pre + sub)[:limit]


def match_weapons(q: str, limit: int = 24) -> list[str]:
    """武器名（英文/繁体/简体）→ weapons_full 的 hash"""
    return _names("weapons", q, limit)


def match_perks(q: str, limit: int = 20) -> list[str]:
    """perk 名（英文/繁体/简体）→ perks.json 的 hash"""
    return _names("perks", q, limit)


def match_activities(q: str, limit: int = 8) -> list[str]:
    """活动名（英文/繁体/简体）→ activities.json 的 ref id"""
    return _names("activities", q, limit)


def _flat(kind: str, q: str) -> str:
    """平表（归一化名 → 简体词）查值：精确优先，其次前缀，最后子串
    —— 中文路径本来就支持子串，外来名字要同样的宽容度（Nezarec → 奈扎雷克的梦魇）"""
    idx = _data().get(kind) or {}
    nk = _norm(q)
    if not idx or not nk:
        return ""
    hit = idx.get(nk)
    if hit:
        return hit
    pre = sub = ""
    for k, v in idx.items():
        if k.startswith(nk):
            pre = v
            break
    if pre:
        return pre
    for k, v in sorted(idx.items()):
        if nk in k:
            sub = v
            break
    return sub


def set_name(q: str) -> str:
    """护甲套装英文/繁体名 → 简体套装名（无命中返回空串）"""
    return _flat("sets", q)


def chart_key(q: str) -> str:
    """副本英文/繁体名 → raid_loot 掉落表 key（无命中返回空串）"""
    return _flat("charts", q)


def weapon_names(h) -> tuple[str, str]:
    """武器 hash → (英文名, 繁体名)，缺则空串"""
    v = (_data().get("wname") or {}).get(str(h)) or ["", ""]
    return (v[0] or "", v[1] or "")


def name_hit(h, q: str) -> bool:
    """该 hash 的英文/繁体名是否「像」查询词（相等或前缀）—— 卡片命中判定用：
    中文名走原名匹配，英文/繁体名命中时同样该出详情卡而不是候选列表"""
    nk = _norm(q)
    if not nk:
        return False
    for nm in weapon_names(h):
        n = _norm(nm)
        if n and (n == nk or n.startswith(nk)):
            return True
    return False


def matched_name(h, q: str) -> str:
    """查询词命中的那个英文/繁体名（卡片上补一行，让用英文/繁体查的人能对上号）"""
    nk = _norm(q)
    if not nk:
        return ""
    for nm in weapon_names(h):
        if nm and nk in _norm(nm):
            return nm
    return ""


def translate(word: str) -> str:
    """筛选词归一：英文/繁体词 → 简体词（Hand Cannon → 手炮）；无命中返回原词"""
    return (_data().get("terms") or {}).get(_norm(word)) or word


def terms_map() -> dict:
    """整张筛选词表（归一化英文/繁体词 → 简体词）：面板图鉴页要注入给前端 JS 用"""
    return _data().get("terms") or {}


def _item_index(kind: str) -> list[tuple[str, list[str]]]:
    """item_en.json / item_cht.json：{名字: [hash]} → [(归一化名, hashes)]（懒加载 + 缓存）"""
    if kind not in _ITEM_IDX:
        try:
            raw = json.load(open(_path(f"item_{kind}.json"), encoding="utf-8"))
            _ITEM_IDX[kind] = [(_norm(n), h) for n, h in raw.items()]
        except Exception:  # noqa: BLE001
            _ITEM_IDX[kind] = []
    return _ITEM_IDX[kind]


def item_hashes(q: str, cap: int = 400) -> set[str]:
    """全物品名（英文 / 繁体）子串命中 → hash 集合（/仓库 关键词用）。
    单个字符会命中大半个仓库，直接放弃。"""
    low = str(q or "").strip().lower()
    nk = _norm(q)
    if not low and not nk:
        return set()
    out: set[str] = set()
    for kind in ("en", "cht"):
        for name, hashes in _item_index(kind):
            if (low and low in name) or (nk and nk in name):
                out.update(hashes)
                if len(out) >= cap:
                    return out
    return out
