"""构建 manifest_index/raid_metrics.json：突袭/地牢的「完成数 / 导师」指标与图标

数据源：线上 manifest（zh-chs）的 DestinyMetricDefinition + DestinyPresentationNodeDefinition；
印章徽章用本地 records.json / presentation_nodes.json（/称号 同一套索引，不用再下）。
- 每个副本（突袭 10 个 / 地牢 9 个）取 metric：完成数（…完成数/…完成次数）；有「…导师」的补上；
  同名有「职业生涯/赛季」两个变体时取职业生涯（来源标注也由节点祖先链解析，统计数据→职业生涯）。
- 图标口径（用户 2026-10-02）：
  · **完成数 = metric 自带的通用「突袭/地牢」图标**（clear.icon，本来就是全副本共用的那枚）；
  · **导师 = 对应副本的成就徽章（印章 seal record 的 displayProperties.icon）**——
    印章按结构找：记录树里与副本同名的展示节点，其 children.records 里属于「称号/传承称号」
    两个根之下的 completionRecordHash 即该副本印章；找不到时退回收藏页同名节点图标。
  · entry.icon 仍是收藏页同名节点图标，作为两者的兜底。
取值在运行时走 Profile 组件 1100 的 metrics.data.metrics[hash].objectiveProgress.progress。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import destiny_data as d2  # noqa: E402

IDX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest_index")
OUT = os.path.join(IDX, "raid_metrics.json")

# 当前在役突袭 + 地牢（均按发售先后排序，与 /raid /地牢 卡片顺序一致）
RAIDS = ["最后一愿", "救赎花园", "深岩墓室", "玻璃拱顶", "门徒誓约",
         "国王的陨落", "梦魇根源", "克洛塔的末日", "救赎的边缘", "永恒沙漠"]
DUNGEONS = ["预言", "贪婪之握", "二象性", "守望者尖塔", "深渊机灵",
            "战争领主的废墟", "晚星之主", "分离教义", "平衡"]

_STRIP = str.maketrans("", "", "“”\"'「」")
_PREFER = ("突袭", "地牢", "收藏")


def _base(name: str, suffix: tuple[str, ...]) -> str:
    for s in suffix:
        if name.endswith(s):
            return name[: -len(s)].translate(_STRIP).strip()
    return ""


async def _table(name: str, lang: str = "zh-chs") -> dict:
    r = await d2.client().get("/Platform/Destiny2/Manifest/")
    m = json.loads(r.content.decode("utf-8-sig"))["Response"]
    r2 = await d2.client().get(
        d2.BASE + m["jsonWorldComponentContentPaths"][lang][name], timeout=180)
    return json.loads(r2.content.decode("utf-8-sig"))


def _node_icon(nodes: dict, name: str) -> str:
    """同名展示节点里挑图标：父链含 突袭/地牢/收藏 的优先（收藏页的副本节点）"""
    best = None
    for v in nodes.values():
        dp = (v or {}).get("displayProperties") or {}
        if (dp.get("name") or "") != name or not dp.get("icon"):
            continue
        chain, cur, depth = [], (v.get("parentNodeHashes") or [None])[0], 0
        while cur and depth < 6:
            pv = nodes.get(str(cur)) or {}
            chain.append(((pv.get("displayProperties") or {}).get("name") or ""))
            cur, depth = (pv.get("parentNodeHashes") or [None])[0], depth + 1
        score = sum(1 for p in _PREFER if p in chain)
        if best is None or score > best[0]:
            best = (score, dp["icon"])
    return best[1] if best else ""


def _source_of(nodes: dict, parents: list) -> str:
    """metric → 祖先链里找「突袭/地牢」节点，取其父节点名 → 「职业生涯//突袭」"""
    cur, depth = (parents or [None])[0], 0
    while cur and depth < 8:
        nd = nodes.get(str(cur)) or {}
        nm = ((nd.get("displayProperties") or {}).get("name") or "").strip()
        if nm in ("突袭", "地牢"):
            for p in (nd.get("parentNodeHashes") or [])[:1]:
                pn = ((nodes.get(str(p)) or {}).get("displayProperties") or {}).get("name") or ""
                pn = (pn or "").strip().replace("统计数据", "职业生涯")
                return f"{pn}//{nm}" if pn else nm
            return nm
        cur, depth = (nd.get("parentNodeHashes") or [None])[0], depth + 1
    return ""


# /称号 的两个根：其一级子节点 = 每个称号（印章）。实测印章名与副本名完全一致
# （「深岩墓室」「分离教义」…17/17 全同名），徽章图 = 印章**节点**的 displayProperties.icon
# （部分印章记录本身没有 icon，不能拿记录图标当准）。
_SEAL_ROOTS = ("616318467", "1881970629")


def _seal_map() -> dict[str, str]:
    """印章名 → 徽章图标"""
    out: dict[str, str] = {}
    for root in _SEAL_ROOTS:
        rn = d2._pnodes.get(root) or {}
        for c in (rn.get("children") or {}).get("presentationNodes") or []:
            nd = d2._pnodes.get(str(c.get("presentationNodeHash"))) or {}
            dp = nd.get("displayProperties") or {}
            nm = (dp.get("name") or "").strip()
            ic = dp.get("icon") or ""
            rec = d2._records.get(str(nd.get("completionRecordHash") or "")) or {}
            ic = ic or (rec.get("displayProperties") or {}).get("icon") or ""
            if nm and ic:
                out[nm] = ic
    return out


async def main():
    metrics = await _table("DestinyMetricDefinition")
    nodes = await _table("DestinyPresentationNodeDefinition")
    seals = _seal_map()
    print(f"metrics={len(metrics)} nodes={len(nodes)} seals={len(seals)}")

    def collect(names: list[str]) -> list[dict]:
        buckets: dict[str, dict] = {n: {} for n in names}
        default_src = "职业生涯//地牢" if names is DUNGEONS else "职业生涯//突袭"
        for h, v in metrics.items():
            name = ((v or {}).get("displayProperties") or {}).get("name") or ""
            mic = (v.get("displayProperties") or {}).get("icon") or ""
            for suf, kind in ((("完成数", "完成次数"), "clear"), (("导师",), "sherpa")):
                base = _base(name, suf)
                if base in buckets:
                    buckets[base].setdefault(kind, []).append(
                        {"hash": int(h), "icon": mic,
                         "source": _source_of(nodes, v.get("parentNodeHashes")) or default_src})
        out = []
        for n in names:
            d = buckets[n]
            if not d.get("clear"):
                print(f"  !! {n} 没有完成数 metric")
                continue

            def pick(cands: list[dict]) -> dict:
                career = [c for c in cands
                          if "统计数据" in c["source"] or "职业生涯" in c["source"]
                          or c["source"].startswith("职业生涯")]
                return (career or cands)[0]

            clear = pick(d["clear"])
            s_icon = seals.get(n) or ""
            entry = {"name": n, "clear": clear,
                     # 副本图标 = 收藏页节点图标；没有就退回 metric 自己的（通用）图标
                     "icon": _node_icon(nodes, n) or clear.get("icon") or "",
                     # 导师砖图标 = 该副本印章徽章；找不到退回收藏页节点图标
                     "seal_icon": s_icon or (_node_icon(nodes, n) or clear.get("icon") or "")}
            if d.get("sherpa"):
                entry["sherpa"] = pick(d["sherpa"])
            out.append(entry)
            print(f"  {n} 印章={'有' if s_icon else '—'}")
        return out

    raids, dungeons = collect(RAIDS), collect(DUNGEONS)
    os.makedirs(IDX, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"raids": raids, "dungeons": dungeons}, f, ensure_ascii=False,
                  separators=(",", ":"))
    print(f"写出 {OUT}: 突袭 {len(raids)} / 地牢 {len(dungeons)}")
    for e in raids + dungeons:
        print(f"  {e['name']:12s} icon={'有' if e['icon'] else '无'} "
              f"clear={e['clear']['hash']} sherpa={e.get('sherpa', {}).get('hash', '—')} "
              f"| {e['clear']['source']}")


asyncio.run(main())
