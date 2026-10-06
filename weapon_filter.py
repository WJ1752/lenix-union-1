"""武器筛选引擎：把一串「框选词」解析成条件，过滤 manifest_index/weapon_filter_index.json

语义：词与词之间是 **与**（全部满足）；任一词只要命中武器的任一维度即算该词满足。
无法识别的词忽略并在结果里回报，一个词都没识别出来时返回空结果提示用法。
全部词都在、但没有任何武器同时满足时，自动放宽成 **或** 并标记 relaxed。
"""
from __future__ import annotations

import json
import os
import re
import sys

import name_i18n

_IDX: dict | None = None
_VER: dict = {}  # hash → {season, event}（weapon_versions.json，缺省为空=全部按首发）


def _versions() -> dict:
    global _VER
    if not _VER:
        base = os.path.dirname(_index_path())
        try:
            _VER = json.load(open(os.path.join(base, "weapon_versions.json"),
                                  encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _VER = {}
    return _VER


def _season_of(w: dict) -> int:
    return int(_versions().get(str(w.get("h", "")), {}).get("season") or 0)


def _index_path() -> str:
    """与 destiny_data._idx_file 同规则：源码目录 → PyInstaller 打包资源 → 当前目录"""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "manifest_index", "weapon_filter_index.json")
    if os.path.exists(here):
        return here
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""),
                           "manifest_index", "weapon_filter_index.json")
    if os.path.exists(bundled):
        return bundled
    return os.path.join("manifest_index", "weapon_filter_index.json")

# 词形归一：把玩家习惯的叫法换成索引里的词，再统一走子串匹配。
# 覆盖三类说法：Manifest 官方译名、社区口语（颜色/位置/绰号）、常见简写。
SYNONYM: dict[str, str] = {
    # —— 武器类型 ——
    "喷子": "霰弹枪", "散弹": "霰弹枪", "散弹枪": "霰弹枪", "sg": "霰弹枪",
    "微冲": "微型冲锋枪", "冲锋枪": "微型冲锋枪", "冲锋": "微型冲锋枪", "smg": "微型冲锋枪",
    "脉冲枪": "脉冲步枪", "脉冲步": "脉冲步枪", "脉冲": "脉冲步枪",
    "自动": "自动步枪", "突突枪": "自动步枪", "全自动": "自动步枪", "ar": "自动步枪",
    "榴弹炮": "榴弹发射器", "榴弹": "榴弹发射器", "gl": "榴弹发射器",
    "狙击枪": "狙击步枪", "狙击": "狙击步枪", "狙": "狙击步枪", "sr": "狙击步枪",
    "侦察步枪": "斥候步枪", "侦察枪": "斥候步枪", "斥候枪": "斥候步枪", "斥候": "斥候步枪",
    "聚变步枪": "融合步枪", "聚变": "融合步枪", "融合枪": "融合步枪", "融合": "融合步枪",
    "fr": "融合步枪",
    "火箭筒": "火箭发射器", "筒子": "火箭发射器", "火箭": "火箭发射器", "rl": "火箭发射器",
    "重机枪": "机枪", "机炮": "机枪", "lmg": "机枪",
    "战弓": "战斗弓箭", "复合弓": "战斗弓箭", "弓": "战斗弓箭",
    "线性融合": "线性融合步枪", "线性": "线性融合步枪", "lfr": "线性融合步枪",
    "枪刃": "偃月", "镰刀": "偃月",
    "追踪枪": "追踪步枪", "追踪": "追踪步枪", "tr": "追踪步枪",
    "hc": "手炮", "pr": "脉冲步枪",

    # —— 弹药：社区按「弹匣颜色 / 武器位」叫 ——
    "主手": "主武器", "主弹": "主武器", "白弹": "主武器", "白弹药": "主武器",
    "白蛋": "主武器", "1号位": "主武器", "一号位": "主武器",
    "副手": "特殊", "绿弹": "特殊", "绿弹药": "特殊", "特殊弹": "特殊",
    "特殊弹药": "特殊", "特武": "特殊", "绿蛋": "特殊", "2号位": "特殊", "二号位": "特殊",
    "重弹": "重武器", "紫弹": "重武器", "紫弹药": "重武器", "重弹药": "重武器",
    "紫蛋": "重武器", "3号位": "重武器", "三号位": "重武器",

    # —— 槽位（Manifest 里叫「威力」，社区一律说「威能」） ——
    "威力": "威能", "威能槽": "威能", "动能槽": "动能", "能量槽": "能量",

    # —— 元素 ——
    "电": "电弧", "雷": "电弧", "闪电": "电弧",
    "火": "烈日", "灼烧": "烈日", "太阳": "烈日",
    "冰": "冰影", "静滞": "冰影", "霜": "冰影",
    "缚影": "缚丝", "丝线": "缚丝",

    # —— 框架：Manifest 用「适配 / 精密 / 攻击型 / 轻质 / 高冲击力」，社区另有说法 ——
    "自适应": "适配", "自适应框架": "适配",
    "精准": "精密", "精准框架": "精密", "精确": "精密", "精确框架": "精密",
    "侵略": "攻击型", "侵略型": "攻击型", "攻击性": "攻击型",
    "轻量": "轻质", "轻量框架": "轻质", "轻型": "轻质",
    "高伤害": "高冲击力", "高冲": "高冲击力", "高冲击": "高冲击力",
}

# 这些词不是子串，而是布尔标记
SPECIAL = {"锻造": "craft", "可锻造": "craft", "图案": "craft", "图纸": "craft",
           "刻印": "craft", "异域": "exotic", "金枪": "exotic", "金色": "exotic",
           "金武器": "exotic"}

def index() -> dict:
    global _IDX
    if _IDX is None:
        with open(_index_path(), encoding="utf-8") as f:
            _IDX = json.load(f)
    return _IDX


def _pred(token: str):
    """把一个词编译成谓词：(标签, 判定函数)"""
    if re.fullmatch(r"\d{2,4}", token):  # 射速
        rpm = int(token)
        return (["射速"], lambda w, rpm=rpm: w["r"] == rpm)

    # 英文 / 繁体词先归一到简体（Hand Cannon → 手炮、脈衝步槍 → 脉冲步枪），再走原来的词形表
    token = name_i18n.translate(token)

    if token in SPECIAL:
        if SPECIAL[token] == "craft":
            return (["可锻造"], lambda w: bool(w["cr"]))
        return (["异域"], lambda w: bool(w["x"]))

    low = SYNONYM.get(token.lower(), token).lower()  # 缩写统一按小写查（ar/SMG…）

    # 结构性字段：命中这里说明词本身是类型/弹药/槽位/元素/框架
    def strong(w, low=low):
        return any(low in (w[k] or "").lower() for k in ("t", "a", "c", "e", "f"))

    # 内容字段：武器名（含英文/繁体名）、来源、可选特性名
    def weak(w, low=low):
        if any(low in (w[k] or "").lower() for k in ("n", "g", "en", "cht")):
            return True
        return any(low in p.lower() for p in w["p"])

    # 「轻质」这类词既可能是框架（轻质框架），也散落在特性名里（轻质弹匣）——
    # 只要它在结构性字段里有命中，就按结构性字段算，不然会从 222 条涨到 627 条。
    if any(strong(w) for w in index().values()):
        return (["关键字"], strong)
    return (["关键字"], weak)


def _merge_words(words: list[str]) -> list[str]:
    """英文/繁体多词条先连读成一个词再翻译：「hand cannon」「adaptive frame」
    「pulse rifle」拆开逐词都不成立，连读才认得出是类型/框架"""
    out: list[str] = []
    i = 0
    while i < len(words):
        for n in (3, 2):  # 最多连读三个词
            if i + n <= len(words):
                joined = " ".join(words[i:i + n])
                if name_i18n.translate(joined) != joined:
                    out.append(joined)
                    i += n
                    break
        else:
            out.append(words[i])
            i += 1
    return out


def filter_weapons(query: str, limit: int = 120) -> dict:
    words = _merge_words([t for t in re.split(r"[\s,，、·]+", (query or "").strip()) if t])

    preds, unknown = [], []
    for w in words:
        p = _pred(w)
        if p:
            preds.append((w, p))
        else:
            unknown.append(w)  # 子串兜底永远返回谓词，这里只防未来分支

    if not preds:
        return {"items": [], "total": 0, "raw_total": 0, "shown": 0, "words": words,
                "unknown": words, "dropped": [], "labels": [],
                "relaxed": False, "empty": True}

    items = [dict(w, h=h) for h, w in index().items()]

    def run(sub):
        return [w for w in items if all(fn(w) for _, (_, fn) in sub)]

    hits = run(preds)
    dropped: list[str] = []
    if hits:
        relaxed = False
    else:
        # 词与词互斥（如「副手 白弹」）时先试去掉一个词，取仍然能命中的最小结果集
        relaxed = False
        best = None
        for i in range(len(preds)):
            sub = preds[:i] + preds[i + 1:]
            h = run(sub) if sub else []
            if h and (best is None or len(h) < len(best[1])):
                best = (sub, h)
        if best:
            preds, hits = best
            dropped = [w for w in words if w not in [x[0] for x in preds]]
        else:
            relaxed = True
            hits = [w for w in items if any(fn(w) for _, (_, fn) in preds)]

    # 同名武器在 Manifest 里按 perk 池/赛季分多个 hash：全部列出（同参考图那样逐版本展示），
    # 只有名字+赛季+水印都相同的才算同一把的重复条目
    hits.sort(key=lambda w: (not w["x"], w["a"], w["t"], w["n"], _season_of(w)))
    seen, uniq = set(), []
    for w in hits:
        key = (w["n"], _season_of(w), w.get("w") or "")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(dict(w, s=_season_of(w)))

    return {
        "items": uniq[:limit],
        "total": len(uniq),
        "raw_total": len(hits),
        "shown": min(len(uniq), limit),
        "words": words,
        "unknown": unknown,
        "dropped": dropped,
        "labels": [label for _, (labels, _) in preds for label in labels],
        "relaxed": relaxed,
        "empty": False,
    }
