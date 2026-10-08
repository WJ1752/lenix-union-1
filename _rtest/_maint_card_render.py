"""渲染维护提示卡（用户会看到的那个）到 PNG，人工看一眼"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bungie_status as bst
import bot_cards, card_render

# 造一个「维护中」的状态，让文案带上官方原文
bst._state.update(on=True, since=__import__("time").time() - 72 * 60,
                  kind="disabled", detail="DestinyThrottledByGameServer There's a lot of "
                  "requests coming into the game server right now!")
lines = [bst.text(), "官方原文：" + bst._state["detail"][:120],
         "维护期间查不了数据（硬查只会拿到错数字），官方恢复后重发一次即可；"
         "已经排队的查询会自动中止，不用手动取消。"]
html = bot_cards.notice("服务器维护中", lines, kind="warn")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_maint_card.png")
png = asyncio.run(card_render.html_to_png(html))
open(out, "wb").write(png)
print("卡片已渲染:", out, len(png), "字节")

html2 = bot_cards.notice("这次的数据不完整",
                         ["角色生涯统计读取失败（SystemDisabled(5) The system is disabled.）："
                          "Bungie 没返回数据（疑似官方维护或接口异常），已拦下避免出错误统计，"
                          "稍后重发一次即可",
                          "已拦下避免出错误统计，稍后重发一次即可。"], kind="warn")
out2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_suspect_card.png")
open(out2, "wb").write(asyncio.run(card_render.html_to_png(html2)))
print("卡片已渲染:", out2)
