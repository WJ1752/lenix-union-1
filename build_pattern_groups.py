"""建立「锻造图案 → 小日向式分组」索引

小日向的锻造页把可锻造图案按**副本 / 赛季 / DLC / 活动**分成固定 24 组（见 README），
分组顺序、组名、每把武器归属都对齐她那页；本脚本产出：

  manifest_index/pattern_groups.json
  {
    "order": ["侠盗赛季", "救赎花园", ...],          # 小日向页的静态组顺序
    "release": ["最后一愿", "救赎花园", ...],         # 按内容上线时间排的「出的顺序」
    "groups": {"侠盗赛季": ["褪色毅力", ...], ...},  # 每组包含的图案名
    "exclude_slots": ["异域催化"]                    # 这些槽位不算"锻造图案"
  }

规则：先用藏品来源串（pattern_sources.json 的 raw）查表落到组；
来源串是「季票奖励 / 空 / 由利维坦…」这类无信息量的，用 NAME_OVERRIDE 逐把指定。
两把武器同时属于两个组（小日向页里就是重复列出的）在 groups 里各出现一次。

用法：.venv\\Scripts\\python build_pattern_groups.py
"""
import json
import os
import re

IDX = "manifest_index"

# 小日向页的组顺序（1:1 抄）
ORDER = [
    "侠盗赛季", "救赎花园", "前兆 | 宿怨赛季", "光陨之秋DLC", "邪姬魅影DLC",
    "抗战赛季", "玻璃拱顶", "国王的陨落", "异域任务", "救赎的边缘",
    "克洛塔的末日", "最后一愿", "梦魇根源", "门徒誓约", "深岩墓室",
    "篇章：回响", "终焉之形DLC", "终愿赛季", "奇巫赛季", "深渊赛季",
    "30周年纪念DLC", "炽天使之盾 | 炽天使赛季", "暗屋之声 | 苏生赛季", "世界掉落系列",
]

# 「出的顺序」：按内容上线时间从早到晚排（旧 → 新）。
# 卡片按这个顺序展示分组：未全部集齐的组排前面、已全部集齐的组垫底，组内仍按本表顺序。
# 想改成「新的在前」把下面整表倒过来即可；不在这张表里的组排到最后。
RELEASE_ORDER = [
    "最后一愿",                    # 2018-09  遗落之族
    "救赎花园",                    # 2019-10  暗影要塞
    "深岩墓室",                    # 2020-11  凌光之刻
    "玻璃拱顶",                    # 2021-05  第 14 赛季
    "30周年纪念DLC",               # 2021-12
    "邪姬魅影DLC",                 # 2022-02  邪姬魅影
    "暗屋之声 | 苏生赛季",          # 2022-02  第 16 赛季
    "门徒誓约",                    # 2022-03
    "前兆 | 宿怨赛季",             # 2022-05  第 17 赛季
    "侠盗赛季",                    # 2022-08  第 18 赛季
    "国王的陨落",                  # 2022-08
    "炽天使之盾 | 炽天使赛季",      # 2022-12  第 19 赛季
    "光陨之秋DLC",                 # 2023-02  光陨之秋
    "抗战赛季",                    # 2023-02  第 20 赛季
    "梦魇根源",                    # 2023-03
    "深渊赛季",                    # 2023-05  第 21 赛季
    "克洛塔的末日",                # 2023-08
    "奇巫赛季",                    # 2023-08  第 22 赛季
    "终愿赛季",                    # 2023-11  第 23 赛季
    "终焉之形DLC",                 # 2024-06  终焉之形
    "救赎的边缘",                  # 2024-06
    "篇章：回响",                  # 2024-06  第 1 篇章
    "异域任务",                    # 跨赛季，垫底
    "世界掉落系列",                # 无固定出处，垫底
]

# 来源串 → 组。键做子串匹配（来源串常带书名号/句号等噪声）
SOURCE_RULES = [
    ("侠盗赛季", "侠盗赛季"),
    ("救赎花园", "救赎花园"),
    ("宿怨赛季", "前兆 | 宿怨赛季"),
    ("二象性", "前兆 | 宿怨赛季"),
    ("遗落利维坦", "前兆 | 宿怨赛季"),
    ("光陨之秋战役", "光陨之秋DLC"),
    ("探索内欧姆那", "光陨之秋DLC"),
    ("邪姬魅影战役", "邪姬魅影DLC"),
    ("探索王座世界", "邪姬魅影DLC"),
    ("泉源首领", "邪姬魅影DLC"),
    ("抗战赛季", "抗战赛季"),
    ("玻璃拱顶", "玻璃拱顶"),
    ("国王的陨落", "国王的陨落"),
    # 异域任务：各赛季的异域任务武器统一归一组（小日向页就是这么并的）
    ("炽天使之盾", "异域任务"),   # 要排在"炽天使赛季"之前
    ("篇章：异端", "异域任务"),   # 要排在"篇章：回响"之前
    ("异域任务", "异域任务"),
    ("高塔异域档案", "异域任务"),
    ("苦命鸳鸯", "异域任务"),
    ("超控", "异域任务"),
    ("亡魂要塞", "异域任务"),
    ("救赎的边缘", "救赎的边缘"),
    ("克洛塔的末日", "克洛塔的末日"),
    ("最后一愿", "最后一愿"),
    ("梦魇根源", "梦魇根源"),
    ("门徒誓约", "门徒誓约"),
    ("深岩墓室", "深岩墓室"),
    ("篇章：回响", "篇章：回响"),
    ("探索苍白之心", "终焉之形DLC"),
    ("终愿赛季", "终愿赛季"),
    ("奇巫赛季", "奇巫赛季"),
    ("深渊赛季", "深渊赛季"),
    ("深渊机灵", "深渊赛季"),
    ("仄的永恒宝藏窖藏", "30周年纪念DLC"),
    ("炽天使赛季", "炽天使之盾 | 炽天使赛季"),
    ("苏生赛季", "暗屋之声 | 苏生赛季"),
    ("开启传说记忆水晶", "世界掉落系列"),
]

# 来源串无信息量（季票奖励/空/利维坦奇珍…）或会误导的，逐把点名
NAME_OVERRIDE = {
    # 季票奖励：按该赛季归组
    "烈火惊骇": "前兆 | 宿怨赛季",
    "义无反顾": "前兆 | 宿怨赛季",
    "心灵碎片": "暗屋之声 | 苏生赛季",
    "无念": "暗屋之声 | 苏生赛季",
    "标量潜能": "终愿赛季",
    "极欲": "终愿赛季",
    "盗亦有道": "侠盗赛季",
    "普朗克的步伐": "侠盗赛季",
    "克尔格拉斯的审判": "炽天使之盾 | 炽天使赛季",
    "改型冒险": "炽天使之盾 | 炽天使赛季",
    # 来源串为空
    "目标修订": "深渊赛季",
    "不同时代": "深渊赛季",
    "远方吸引": "深渊赛季",
    "纤薄断崖": "深渊赛季",
    "狼毒": "终愿赛季",
    # 利维坦：奇珍异兽园出的分属两个组
    "帝国法令": "前兆 | 宿怨赛季",
    "黄金长牙": "抗战赛季",
    "死神剃刀": "抗战赛季",
    "王座切割者": "抗战赛季",
    # 来源串写着别的副本/活动，但小日向按武器本季归组
    "天堂暴政": "最后一愿",
    "隐士": "奇巫赛季",
    "鉴定梦魇": "邪姬魅影DLC",
    # 小日向页里没有这一条（来源"探索开普勒"的遗留图案），置 OMIT 与其 24 组的条数保持一致。
    # 想让卡片把它列出来，把下面这行的值改成 "光陨之秋DLC" 即可。
    "引力子尖刺": "OMIT",
}

# 跨组重复：这些武器小日向页里在两个组都列了
DUP_IN = {
    "枯骨鳞片": ["邪姬魅影DLC", "异域任务"],
    "青龙协同之刃": ["邪姬魅影DLC", "异域任务"],
    "玄武行动之刃": ["邪姬魅影DLC", "异域任务"],
    "朱雀意图之刃": ["邪姬魅影DLC", "异域任务"],
    "零号修订": ["炽天使之盾 | 炽天使赛季", "异域任务"],
}


def _roots():
    R = json.load(open(os.path.join(IDX, "records.json"), encoding="utf-8"))
    P = json.load(open(os.path.join(IDX, "presentation_nodes.json"), encoding="utf-8"))
    S = json.load(open(os.path.join(IDX, "pattern_sources.json"), encoding="utf-8"))
    return R, P, S


def _kids(P, h):
    n = P.get(str(h)) or {}
    return [c["presentationNodeHash"] for c in (n.get("children") or {}).get("presentationNodes", [])]


def _recs(P, h):
    n = P.get(str(h)) or {}
    return [c["recordHash"] for c in (n.get("children") or {}).get("records", [])]


def patterns(R, P):
    """(name, slot, type, source_raw)，跳过"异域催化"槽位"""
    out = []
    for mid in _kids(P, "2642502414"):
        for slot in _kids(P, mid):
            sname = (P[str(slot)]["displayProperties"].get("name") or "")
            if "催化" in sname:
                continue
            for t in _kids(P, slot):
                tname = P[str(t)]["displayProperties"].get("name", "")
                for rh in _recs(P, t):
                    nm = ((R.get(str(rh)) or {}).get("displayProperties") or {}).get("name")
                    if nm:
                        out.append((nm, sname, tname))
    return out


def pick_group(name: str, raw: str, src_map: dict) -> str | None:
    if name in NAME_OVERRIDE:
        g = NAME_OVERRIDE[name]
        return None if g == "OMIT" else g
    s = re.sub(r"^来源\s*[：:]\s*", "", (raw or "").strip())
    s = re.sub(r"[“”\"']", "", s)
    for key, grp in SOURCE_RULES:
        if key in s:
            return grp
    return None


def main():
    R, P, S = _roots()
    rows = patterns(R, P)
    groups: dict[str, list[str]] = {g: [] for g in ORDER}
    unmatched, omitted = [], []
    for nm, slot, typ in rows:
        raw = (S.get(nm) or {}).get("raw", "")
        if NAME_OVERRIDE.get(nm) == "OMIT":
            omitted.append(nm)
            continue
        g = pick_group(nm, raw, S)
        if not g:
            unmatched.append((nm, slot, typ, raw))
            continue
        for gg in DUP_IN.get(nm, [g]):
            if gg not in groups:
                groups[gg] = []
            if nm not in groups[gg]:
                groups[gg].append(nm)
    out = {"order": ORDER, "release": RELEASE_ORDER, "groups": groups,
           "exclude_slots": ["异域催化"]}
    json.dump(out, open(os.path.join(IDX, "pattern_groups.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    total = 0
    for g in ORDER:
        total += len(groups[g])
        print(f"{len(groups[g]):3d}  {g}")
    print(f"卡片条目 {total}（唯一图案 {len(rows) - len(unmatched) - len(omitted)}）")
    if omitted:
        print("略过（小日向页未列）:", "、".join(omitted))
    if unmatched:
        print("!! 未匹配:")
        for nm, slot, typ, raw in unmatched:
            print(f"   {nm} | {slot}/{typ} | {raw}")


if __name__ == "__main__":
    main()
