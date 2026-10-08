"""插件能否加载 + 维护闸门标记是否生效 + 闸门对本群是否判定正确（不联网、不真起 bot）"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OB11

nonebot.init(driver="~fastapi+~httpx+~websockets", host="127.0.0.1", port=18901,
             log_level="WARNING", command_start={"/"})
nonebot.get_driver().register_adapter(OB11)
nonebot.load_plugins(os.path.join(ROOT, "nonebot_plugins"))

import nonebot_plugins.destiny2 as mod                      # noqa: E402
import bungie_status as bst                                 # noqa: E402

print("插件导入成功")

local = ("help_query", "unbind_query", "mine_query", "weapon_query", "perk_query",
         "armor_query", "filter_query", "armor_lookup", "drop_query", "cp_query",
         "login_query", "callback_query")
bad = [n for n in local if not getattr(mod, n)._default_state.get("maint_ok")]
print("本地指令已标记 maint_ok：", "全部 ✓" if not bad else f"缺 {bad}")

need = ("base_query", "bind_query", "dust_query", "xur_query", "rot_query",
        "raid_query", "pvp_query", "heat_query", "fireteam_query", "vault_query",
        "wpvp_query", "gm_query", "career_query")
wrong = [n for n in need if getattr(mod, n)._default_state.get("maint_ok")]
print("需要 Bungie 的指令未被标记：", "全部 ✓" if not wrong else f"误标 {wrong}")
print("维护闸门已注册：", hasattr(mod, "_maint_gate"))
print("维护提示文案：", bst.text())
