"""抓取 Starside 轮换页的「突袭 ↔ 地牢」配对表 → manifest_index/rotation_pairs.json

来源: https://starside.work/rotation/index.html （静态页，一张 10 行的表）
用法: .venv/Scripts/python.exe scrape_starside_rotation.py

这张表是**固定周期**：每周整体向后挪一格。知道本周「突袭①」落在哪一行（用官方
GetPublicMilestones 里「有周常挑战的突袭」反查即可），就能按固定偏移推出
突袭② / 地牢① / 地牢② —— 见 destiny_data.rotation_week() 的注释。
Bungie 加了新突袭/新地牢时需要重跑本脚本更新这张表。
"""
import json
import os
import re
import sys

import httpx

URL = "https://starside.work/rotation/index.html"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "manifest_index", "rotation_pairs.json")


def parse(html: str) -> list:
    i = html.find("突袭")
    seg = html[i:i + 6000] if i >= 0 else html
    rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [re.sub(r"\s+", " ", re.sub("<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(cells) == 3 and cells[0].isdigit():
            rows.append({"seq": int(cells[0]), "raid": cells[1], "dungeon": cells[2]})
    return rows


def main() -> int:
    r = httpx.get(URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    rows = parse(r.text)
    if len(rows) < 5 or not any(x["raid"] for x in rows):
        print("解析失败（页面结构可能变了），未写入。前几行：%s" % rows[:3])
        return 1
    json.dump({"source": URL, "rows": rows},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出 %d 行 → %s" % (len(rows), OUT))
    for x in rows:
        print("   %2d  %-12s %s" % (x["seq"], x["raid"] or "(无突袭)", x["dungeon"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
