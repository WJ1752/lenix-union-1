"""抓取 Starside 轮换页 → manifest_index/rotation_pairs.json

来源: https://starside.work/rotation/index.html （静态页，可直接访问无 CF）
用法: .venv/Scripts/python.exe scrape_starside_rotation.py
      网络不通时自动回退用仓库根的离线快照 _tmp_rotation.html（页面更新后建议重抓）。

页面上有两张固定周期表，都落进同一个 JSON：

1) rows —— 「突袭 ↔ 地牢」10 行配对表。
   每周整体向后挪一格。知道本周「突袭①」落在哪一行（用官方
   GetPublicMilestones 里「有周常挑战的突袭」反查即可），就能按固定偏移推出
   突袭② / 地牢① / 地牢② —— 见 destiny_data.rotation_week() 的注释。
   Bungie 加了新突袭/新地牢时需要重跑本脚本更新这张表。

2) distortion —— 「扭曲星球轮换」7x24=168 时段表。
   每小时换一个目的地，循环顺序 7 小时一轮：
   欧洲无人区 → 幽梦之城 → 王座世界 → 月球 → 木卫二 → 涅索斯 → 发射基地
   一周 168 小时恰走完 24 轮，因此**每周的表相同**（页面页脚原话）。
   页面表格按访问者**本机时钟**高亮（行=当前时段，列=今天星期几），即表以
   「本机周一 00:00」为起点；bot 跑在用户机器上（UTC+8），直接用本机时间查。
   对齐自检：Bungie 周复位 = UTC 周二 17:00 = 北京周三 01:00，距周一 00:00
   恰 49h = 7 整循环，复位点自动回到 cycle[0]，无需额外偏移。
   存储结构：cycle[7] 目的地顺序 + slots[周几(周一=0)][小时(0-23)] = cycle 下标。
   运行时查询见 destiny_data.distortion_now()。
"""
import datetime as dt
import json
import os
import re
import sys

import httpx

URL = "https://starside.work/rotation/index.html"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "manifest_index", "rotation_pairs.json")
SNAPSHOT = os.path.join(ROOT, "_tmp_rotation.html")

NOTE = ("扭曲：每小时换目的地，7 小时一轮；表以本机周一 00:00 为起点（页面按访问者本机"
        "时钟高亮，bot=UTC+8），slots[weekday(周一=0)][hour]=cycle 下标。Bungie 周复位"
        "（北京周三 01:00）距锚点恰 49h=7 整循环，复位自动回 cycle[0]，每周表相同。")


def _cells(row_html: str) -> list:
    return [re.sub(r"\s+", " ", re.sub("<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def parse_raid_rows(html: str) -> list:
    i = html.find("突袭")
    seg = html[i:i + 6000] if i >= 0 else html
    rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = _cells(r)
        if len(cells) == 3 and cells[0].isdigit():
            rows.append({"seq": int(cells[0]), "raid": cells[1], "dungeon": cells[2]})
    return rows


def _dist_cycle(html: str, monday_col: list) -> list:
    """循环顺序：优先取页面 chain 段落（…→ 回到开头），失败则退用周一 00-06 时列。"""
    m = re.search(r'class="chain"[^>]*>([^<]+)<', html)
    if m:
        chain = [x.strip() for x in m.group(1).split("→") if x.strip()]
        chain = [x for x in chain if x in set(monday_col)]   # 滤掉「回到开头」
        if len(chain) == 7 and set(chain) == set(monday_col):
            return chain
    return monday_col[:7]


def parse_distortion(html: str) -> dict:
    """解析「扭曲星球轮换」7x24 表 → {cycle, slots}，并做三个时刻的抽查校验。"""
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)
    body = None
    for t in tables:                       # 认表头：周一…周天 的那张才是扭曲表
        if re.search(r"<th[^>]*>周一</th>.*?<th[^>]*>周天</th>", t, re.S):
            body = t
            break
    if body is None:
        raise ValueError("没找到扭曲 7 列表")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    monday, slots = None, []
    for r in rows:
        cells = _cells(r)
        if len(cells) != 8 or not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", cells[0]):
            continue
        hour = int(cells[0][:2])
        names = cells[1:]                  # 周一…周天
        if hour != len(slots):
            raise ValueError(f"时段行乱序：期望第 {len(slots)} 小时，拿到 {cells[0]}")
        slots.append(names)
        if hour == 0:
            monday = names
    if len(slots) != 24 or not monday:
        raise ValueError(f"扭曲表行数不对：{len(slots)}")
    cycle = _dist_cycle(html, monday)
    idx = {n: i for i, n in enumerate(cycle)}
    slots = [[idx[n] for n in day] for day in slots]      # 此时 slots[小时][周几]
    slots = [[slots[h][d] for h in range(24)] for d in range(7)]   # 转置 → [周几][小时]
    _selfcheck(cycle, slots)
    return {"cycle": cycle, "hours": 7, "slots": slots}


def _at(slots: list, cycle: list, weekday: int, hour: int) -> str:
    return cycle[slots[weekday][hour]]


def _selfcheck(cycle: list, slots: list) -> None:
    """抽查 3 个时刻（与页面表格逐格比对过）+ 周复位对齐自检，不符直接抛错不落盘。"""
    assert _at(slots, cycle, 2, 2) == "幽梦之城", "周三 02:00 应为 幽梦之城"
    assert _at(slots, cycle, 5, 15) == "王座世界", "周六 15:00 应为 王座世界"
    assert _at(slots, cycle, 1, 23) == "涅索斯", "周二 23:00-24:00（含 23:59）应为 涅索斯"
    # 周复位（北京周三 01:00 = weekday 2, hour 1）应回到循环开头
    assert _at(slots, cycle, 2, 1) == cycle[0], "周三 01:00（周复位）应回到循环开头"


def main() -> int:
    text, src = None, URL
    try:
        r = httpx.get(URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        text = r.text
    except Exception as exc:  # noqa: BLE001
        if not os.path.exists(SNAPSHOT):
            print(f"抓取失败且无离线快照：{exc}")
            return 1
        print(f"在线抓取失败（{exc}），改用离线快照 _tmp_rotation.html")
        text = open(SNAPSHOT, encoding="utf-8").read()
        src = URL + "（离线快照 _tmp_rotation.html）"
    rows = parse_raid_rows(text)
    dist = parse_distortion(text)
    if len(rows) < 5 or not any(x["raid"] for x in rows):
        print("突袭表解析失败（页面结构可能变了），未写入。前几行：%s" % rows[:3])
        return 1
    json.dump({"source": src, "note": NOTE, "rows": rows, "distortion": dist},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出 %d 行突袭配对 + 扭曲 %d 目的地 x 168 时段 → %s"
          % (len(rows), len(dist["cycle"]), OUT))
    for x in rows:
        print("   %2d  %-12s %s" % (x["seq"], x["raid"] or "(无突袭)", x["dungeon"]))
    cyc = dist["cycle"]
    print("扭曲循环: " + " → ".join(cyc))
    print("校验通过: 周三02:00=%s 周六15:00=%s 周二23:59=%s 周三01:00(复位)=%s"
          % (_at(dist["slots"], cyc, 2, 2), _at(dist["slots"], cyc, 5, 15),
             _at(dist["slots"], cyc, 1, 23), _at(dist["slots"], cyc, 2, 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
