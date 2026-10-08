"""多语言查询冒烟测试：每个查询入口用 英文名 / 繁体名 / 简体名 各打一发。

不进 QQ、不发网络请求（除了 import 时读本地索引），跑法：
    .venv/Scripts/python.exe _rtest/test_i18n_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import name_i18n
import raid_loot
import weapon_filter
from destiny_data import search_armor_sets, search_perks, search_weapons_full, suggest_weapons
from weapon_usage import resolve_weapon_hashes

OK = FAIL = 0


def check(label: str, got, want_any=None, want_empty: bool = False):
    global OK, FAIL
    if want_empty:
        good = not got
    else:
        good = bool(got)
        if good and want_any is not None:
            good = any(x in str(got) for x in want_any)
    OK, FAIL = OK + good, FAIL + (not good)
    print(f"{'ok  ' if good else 'FAIL'} {label}: {str(got)[:110]}")


print("索引可用:", name_i18n.ready())

print("\n--- 1) /武器查询 + @机器人（英文 / 繁体 / 简体名）---")
check("Fatebringer (en)", [w["name"] for w in search_weapons_full("Fatebringer")], ["命运终结者"])
check("fatebring (en 片段)", [w["name"] for w in search_weapons_full("fatebring")])
check("Gjallarhorn (en)", [w["name"] for w in search_weapons_full("Gjallarhorn")], ["加拉尔号角"])
check("Dragon's Breath (en)", [w["name"] for w in search_weapons_full("dragon's breath")], ["龙息"])
check("宿命使者 (cht)", [w["name"] for w in search_weapons_full("宿命使者")], ["命运终结者"])
check("龍之氣息 (cht)", [w["name"] for w in search_weapons_full("龍之氣息")], ["龙息"])
check("加拉尔号角 (chs 原有路径)", [w["name"] for w in search_weapons_full("加拉尔号角")], ["加拉尔号角"])
check("龙息 (chs 原有路径)", [w["name"] for w in search_weapons_full("龙息")], ["龙息"])
check("Conditional Finality (en)", [w["name"] for w in search_weapons_full("conditional finality")])
check("秋风 (chs 片段)", [w["name"] for w in search_weapons_full("秋风")], ["秋风"])
check("suggest Gjallar (面板联想)", [w["name"] for w in suggest_weapons("gjallar")], ["加拉尔号角"])
check("name_hit(Fatebringer 英文精确)", name_i18n.name_hit("2171478765", "fatebringer"))
check("name_hit(秋风 中文不该命中)", not name_i18n.name_hit("2171478765", "秋风"))
check("无关词不命中", search_weapons_full("qwzxv"), want_empty=True)

print("\n--- 2) /perk查询（英文 / 繁体 / 简体）---")
check("Incandescent (en)", [p["name"] for p in search_perks("Incandescent")], ["辉耀炽热"])
check("incandes (en 片段)", [p["name"] for p in search_perks("incandes")])
check("辉耀炽热 (chs)", [p["name"] for p in search_perks("辉耀炽热")], ["辉耀炽热"])
check("热力四射 (chs)", [p["name"] for p in search_perks("热力四射")], ["热力四射"])

print("\n--- 3) /护甲套装（英文 / 繁体 / 别名 / 简体）---")
for q in ("Seventh Seraph", "第七熾天使", "第七炽天使", "vog", "Last Wish",
          "偉大狩獵", "Nezarec", "奈扎雷克的夢魘", "Taken King", "邪神降臨"):
    res = search_armor_sets(q)
    check(f"套装 {q!r}", [s["name"] for s in res])

print("\n--- 4) /掉落（英文 / 繁体 / 缩写 / 简体）---")
for q in ("Crota's End", "Vault of Glass", "國王的殞落", "克洛塔", "ce", "深渊机灵",
          "Root of Nightmares", "Deep Stone Crypt", "二象性", "Vesper's Host",
          "门徒誓约", "Garden of Salvation"):
    check(f"掉落 {q!r}", raid_loot.resolve(q))
check("掉落 瞎写的词不命中", raid_loot.resolve("qwzxv掉落"), want_empty=True)

print("\n--- 5) /武器筛选（英文 / 繁体 / 多词连读）---")
for q in ("Hand Cannon exotic", "脈衝步槍 烈日", "Fatebringer", "adaptive frame 手炮",
          "冲 锻造", "重型武器", "chain reaction"):
    r = weapon_filter.filter_weapons(q)
    check(f"筛选 {q!r} → {r['total']} 把", r["total"])
    print(f"      词条 {r['words']} → 标签 {r['labels']}")
check("筛选 手炮 (chs 原有路径)", weapon_filter.filter_weapons("手炮")["total"])

print("\n--- 6) /仓库 关键词（英文 / 繁体物品名 → hash）---")
for q in ("Mythoclast", "威寇斯破神者", "gjallarhorn", "vex", "宿命使者"):
    hs = name_i18n.item_hashes(q)
    check(f"仓库 {q!r} → {len(hs)} 个 hash", hs)

print("\n--- 7) 面板单武器校准（英文名 / 简体名）---")
check("resolve_weapon_hashes('Gjallarhorn')", resolve_weapon_hashes("Gjallarhorn"))
check("resolve_weapon_hashes('龙息')", resolve_weapon_hashes("龙息"))

print(f"\n通过 {OK} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
