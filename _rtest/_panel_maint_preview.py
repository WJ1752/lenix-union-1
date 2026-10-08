"""起一个带「发送失败图片」样本的面板实例（8999），供人工/无头浏览器看样式"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uvicorn
import bot_log
import destiny_data as d2
import bungie_status as bst

png = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_maint_card.png"), "rb").read()
fn = bot_log.save_unsent(png, "PVE 武器使用 Kostus#8079", "E00DC54A7BD7D0D24B54CC7AD5CE96", "")
fn2 = bot_log.save_unsent(png, "本周轮换", "810807201", "")
print("样本图片:", fn, fn2)

bot_log.add("in", text="/pve生涯武器 Kostus#8079", group_id="E00DC54A7BD7D0D24B54CC7AD5CE96",
            user_id="7A9F3C2E", nickname="狼群环伺之夜")
bot_log.add("out", text=f"[发送失败] PVE 武器使用 Kostus#8079：<ActionFailed: 400, "
                        f"code=40034005, message=回复消息msg_id过期>",
            group_id="E00DC54A7BD7D0D24B54CC7AD5CE96", user_id="3104813702", nickname="Bot",
            extra={"unsent": fn, "resend_group": "E00DC54A7BD7D0D24B54CC7AD5CE96",
                   "resend_user": "", "resend_official": True})
bot_log.add("out", text="[发送失败] 本周轮换：ActionFailed: 风控/掉线",
            group_id="810807201", user_id="3104813702", nickname="Bot",
            extra={"unsent": fn2, "resend_group": "810807201", "resend_user": "",
                   "resend_official": False})

# 一条被维护自动中止的后台任务
d2.JOBS["preview1"] = {"id": "preview1", "label": "PVE 生涯武器（全生涯）", "kind": "wpve",
                       "who": "群 810807201", "name": "Kostus#8079", "status": "aborted",
                       "ts": time.time() - 600, "started": time.time() - 580,
                       "ended": time.time() - 60, "done": 1200, "total": 5000,
                       "error": "Bungie 服务器维护中，已自动中止：Bungie 服务器正在维护"}
# 维护态点亮，看横幅
bst._state.update(on=True, since=time.time() - 72 * 60, kind="disabled",
                  detail="DestinyThrottledByGameServer There's a lot of requests ...")

import webui
uvicorn.run(webui.app, host="127.0.0.1", port=8999, log_level="warning")
