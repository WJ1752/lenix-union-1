import re, sys
sys.path.insert(0, ".")
import webui

def g(name, rel, **kw):
    base = {"name": name, "master_mode": False, "ref": 0, "pgcr": "", "plays": 10,
            "clears": 8, "best": None, "last": "2026-09-30 12:00", "ffc": 600,
            "flawless": 0, "solo": 0, "duo": 0, "trio": 0, "solo_fl": 0, "duo_fl": 0,
            "trio_fl": 0, "master": 0, "diffs": [], "day_one": 0, "week_one": 0,
            "rel_d1": rel, "rel_w1": rel}
    base.update(kw)
    return base

rep = {
    "display": "测试#1234", "total_clears": 20, "total_plays": 30, "rr_aligned": True,
    "flawless": 2, "solo_fl": 1, "duo_fl": 1, "trio_fl": 3, "master": 5,
    "matches": [], 
    "raids": [
        g("克洛塔的末日", "2023-09-02 17:00", solo=1, duo=1, trio=5, flawless=1,
          solo_fl=1, trio_fl=2, day_one=3, week_one=4,
          d1_rank={"rank": 5971, "total": 7314}),
        g("分离教义", "2025-02-08 18:00", day_one=1, d1_rank={"rank": 13441, "total": 19447}),
    ],
    "raids_master": [],
}
for mode, lab in ((4, "RAID"), (82, "DUNGEON")):
    html = webui.render_raid_card(rep, "测试", "测试", mode)
    badges = re.findall(r"class='bd ([^']*)'[^>]*>([^<]*)<", html)
    tops = re.findall(r"<span>([^<]*(?:无暇|通关)[^<]*)</span><b>([^<]*)</b>", html)
    print(f"== {lab} badges:", badges)
    print(f"== {lab} tops:", tops)
