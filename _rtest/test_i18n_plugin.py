"""插件侧验证：nonebot 插件能正常加载（新增 import 不漏），
异域护甲按繁体名命中，@机器人 直查的命中判定（strong）认英文名。

    .venv/Scripts/python.exe _rtest/test_i18n_plugin.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "nonebot_plugins"))

import json

import nonebot

import name_i18n

# 插件模块级就要 _get_driver()，必须像 bot_runtime 那样先 init 再 load（不 run，不占端口）
nonebot.init(driver="~fastapi", command_start={"/"})
nonebot.load_plugins(os.path.join(ROOT, "nonebot_plugins"))
p = next(m for m in sys.modules.values()
         if getattr(m, "__file__", "") and m.__file__.endswith("destiny2.py"))
print("     插件模块:", p.__name__)

OK = FAIL = 0


def check(label, cond, extra=""):
    global OK, FAIL
    OK, FAIL = OK + bool(cond), FAIL + (not bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {label} {extra}")


items = p._armor_data()
check("异域护甲索引可读", bool(items), f"{len(items or [])} 件")

armor = next((it for it in items if it.get("en")), None)
print(f"     样例护甲 {armor.get('name')} / {armor.get('en')} / hash={armor.get('hash')}")
cht_names = [k for k, v in json.load(open(os.path.join(ROOT, "manifest_index", "item_cht.json"),
                                         encoding="utf-8")).items()
             if str(armor.get("hash")) in v]
check("该护甲有繁体名可查", bool(cht_names), str(cht_names[:2]))
if cht_names:
    exact, fuzzy = p._armor_match(items, cht_names[0])
    check(f"护甲查繁体名 {cht_names[0]!r} → 精确命中", len(exact) == 1,
          f"exact={len(exact)} fuzzy={len(fuzzy)}")
exact_en, _ = p._armor_match(items, armor.get("en"))
check("护甲查英文名仍然命中（原有能力）", len(exact_en) >= 1)
exact_zh, _ = p._armor_match(items, armor.get("name"))
check("护甲查中文名仍然命中（原有能力）", len(exact_zh) >= 1)

check("插件 name_i18n 可用", name_i18n.ready())
# 别名要么含「掉落」要么就是 loot：command_names() 给的是「xx掉落 / 掉落xx」两种形态，
# 只要不冒出别的裸词（那种会误吞普通消息）就算过
check("掉落指令别名仍以中文注册", all("掉落" in a or a == "loot"
                                 for a in
                                 {"掉落", "d2掉落", "掉落表", "loot"} | set(p.raid_loot.command_names())))

print(f"\n通过 {OK} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
