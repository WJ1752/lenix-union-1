# -*- coding: utf-8 -*-
"""突袭/地牢掉落表图片查询。

数据源：Sayalarry 制作的掉落和收集列表图（B站专栏，见 CHARTS_SRC）。
原图整理在 raid_images/（构建期），切块长图在 raid_images_proc/（随 exe 分发）。
没有 Saya 图的副本登记在 MISSING，给用户明确提示而不是装死。
"""
import os
import re
import sys

# key -> (显示名, 别名小写列表, 来源文章)
CHARTS: dict[str, tuple[str, list[str], str]] = {
    "crota": ("克洛塔的末日", ["ce", "克洛塔", "末日", "crota"],
              "https://www.bilibili.com/read/cv26304838"),
    "ron": ("梦魇根源", ["ron", "梦魇", "根源", "根部"],
            "https://www.bilibili.com/read/cv22374354"),
    "salvation_edge": ("救赎边缘", ["se", "救边", "边缘"],
                       "Sayalarry 图 V1.1（群文件收录）"),
    "warlords": ("战争领主的废墟", ["wr", "废墟", "战废", "战争废墟", "战争领主"],
                 "https://www.bilibili.com/read/cv28266408"),
    "gotd": ("深渊机灵", ["gotd", "深渊"],
             "https://www.bilibili.com/read/cv23970626"),
    "spire": ("守护者尖塔", ["spire", "尖塔", "看守者"],
              "https://www.bilibili.com/read/cv20406718"),
    "kings_fall": ("国王的陨落", ["kf", "王陨", "国王"],
                   "https://www.bilibili.com/read/cv18338130"),
    "vog": ("玻璃穹顶(VOG)", ["vog", "玻璃", "琉璃宝库"],
            "https://www.bilibili.com/read/cv11398914"),
    "vesper": ("晚星之主", ["vh", "晚星"],
               "Sayalarry 图 S25 V1.0（群文件收录）"),
    "vow": ("门徒誓约", ["vow", "门徒"], "Sayalarry 图 V2.0（群文件收录）"),
    "gos": ("救赎花园", ["gos", "花园"], "Sayalarry 图 V1.0（群文件收录）"),
    # 沙漠分普通/史诗两个难度：普通用 Saya 官方图（2025.07.25 V1.0），
    # 史诗用群文件图（2026.01.10），两图关卡掉落不同，别混
    "eternal_desert": ("永恒沙漠（普通）", ["沙漠", "永恒", "永恒沙漠", "普通沙漠"],
                       "Sayalarry 图 V1.0（群文件收录）"),
    "eternal_desert_epic": ("永恒沙漠（史诗）", ["史诗沙漠", "沙漠史诗", "永恒沙漠史诗"],
                            "Sayalarry 图 史诗难度（群文件收录）"),
    "lw": ("最后一愿", ["lw", "遗愿"], "Sayalarry 图 V1.0（群文件收录）"),
    "dsc": ("深岩墓室", ["dsc", "墓室"], "Sayalarry 图 V1.0（群文件收录）"),
    "duality": ("二象性", ["duality"], "Sayalarry 图 V1.2（群文件收录）"),
    "shattered_throne": ("破碎王座", ["st", "王座"], "Sayalarry 图 V1.0（群文件收录）"),
    "prophecy": ("预言", ["prophecy"], "Sayalarry 图 V2.0（群文件收录）"),
}

# Sayalarry 尚未做图的副本：别名 -> 显示名（走「暂无图」提示）。目前已清空，
# 后续发现新缺口再往这里登记。
MISSING: dict[str, str] = {}


def _base() -> str:
    if getattr(sys, "frozen", False):
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "raid_images_proc")
        if os.path.isdir(bundled):
            return bundled
        return os.path.join(os.path.dirname(sys.executable), "raid_images_proc")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "raid_images_proc")


def _norm(text: str) -> str:
    return re.sub(r"[\s/]+", "", text).lower()


def _display_base(name: str) -> str:
    """去掉显示名里的难度/版本括号：「永恒沙漠（普通）」→「永恒沙漠」"""
    return re.sub(r"[（(].*?[)）]", "", name)


def resolve(text: str) -> str | None:
    """'ron掉落' / '掉落 克洛塔' / 'ce' → key；认不出返回 None。
    副本全名（显示名去括号）也可直接触发：'深渊机灵掉落'、'克洛塔的末日掉落'"""
    t = _norm(text)
    for probe in (t.replace("掉落", ""), t):
        for key, (name, aliases, _) in CHARTS.items():
            if probe in aliases or probe == key or (probe and probe == _display_base(name)):
                return key
        if probe in MISSING:
            return None
    return None


def missing_name(text: str) -> str | None:
    t = _norm(text).replace("掉落", "")
    for alias, name in MISSING.items():
        if t == alias.strip():
            return name
    return None


def chart_display(key: str) -> str:
    return CHARTS[key][0]


def chart_source(key: str) -> str:
    return CHARTS[key][2]


def command_names() -> list[str]:
    """注册进 on_command aliases 的组合词：ron掉落 / 掉落ron / ce掉落 …
    以及中文全名：二象性掉落 / 掉落门徒誓约（显示名去括号后同样注册）"""
    names = set()
    for key, (name, aliases, _) in CHARTS.items():
        for a in [key] + aliases + [_display_base(name)]:
            names.add(f"{a}掉落")
            names.add(f"掉落{a}")
    return sorted(names)


def segments(key: str) -> list[str]:
    """该副本的全部切块图路径（按序）"""
    d = os.path.join(_base(), key)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jpg")]
