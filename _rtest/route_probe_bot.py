"""指令路由探针（服务端）：跑真实插件，把所有卡片渲染换成 "CARD:<名字>"，
数据层换成假实现，这样一条消息只会打出「哪个处理器响应了」，不联网、不截图。

用法：.venv\\Scripts\\python _rtest\\route_probe_bot.py   （监听 8081）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import bot_log  # noqa: E402

_orig_add = bot_log.add


def _add(direction, event=None, text="", **kw):
    print(f"[OUT] g{kw.get('group_id','')} {direction} skip={kw.get('extra')} :: {text}", flush=True)
    return _orig_add(direction, event=event, text=text, **kw)


bot_log.add = _add

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.init(driver="~fastapi", host="127.0.0.1", port=8081, log_level="WARNING",
             command_start={"/"})
nonebot.get_driver().register_adapter(Adapter)
nonebot.load_plugins(os.path.join(ROOT, "nonebot_plugins"))

import card_render  # noqa: E402
import bot_cards  # noqa: E402
import destiny_data as d2  # noqa: E402

async def _fake_png(html, *a, **k):
    print(f"[HTML] {str(html)[:120]}", flush=True)
    return b"\x89PNG\r\n\x1a\n"


card_render.html_to_png = _fake_png

# 卡片构造器 → 命名标记（不联网、不渲染）
for _n in ("raid_card", "mode_card", "player_card", "career_card", "weapon_card",
           "weapons_list_card", "perk_card", "nodes_card", "history_card", "wpvp_card",
           "wpve_card", "heat_card", "notice"):
    setattr(bot_cards, _n, (lambda n: lambda *a, **k: f"CARD:{n}")(_n))

# 数据层 → 秒返回的假数据
def _ok(*a, **k):
    async def _c():
        return {"display": "TEST#0001"}
    return _c()

for _n in ("raid_report", "mode_report", "player_report", "career_report", "report",
           "node_report", "history_report", "weapon_report", "wpvp_report",
           "wpve_report", "heat_report", "lifetime_stats"):
    if hasattr(d2, _n):
        setattr(d2, _n, _ok)

d2.search_weapons_full = lambda q, n=12: [{"name": f"W-{q}", "hash": 1, "type": "手炮"}]
d2.search_perks = lambda q, n=3: [{"name": f"P-{q}", "hash": 1}]

nonebot.run()
