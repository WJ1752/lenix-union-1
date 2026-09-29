"""指令识别回归：验证「不带 / 不响应」「/pve是顺手写的 不响应」「@机器人 /引用 也能响应」
「@ 别人的指令不抢答」

用法：先启动 bot（.venv\\Scripts\\python bot.py，监听 8081），再跑本脚本。
每个用例用一个独立 group_id，回复里带的 group_id 就能对上是哪一条。
"""
import asyncio
import json
import sys

import websockets

PORT = sys.argv[1] if len(sys.argv) > 1 else "8081"
SELF = 10000

CASES = [
    (8801, "pve是顺手写的，主要是glk刚才问了", False, "纯文本没斜杠"),
    (8802, "/pve是顺手写的", False, "斜杠但命令词后连写"),
    (8803, "pve Wj#8984", False, "裸写指令"),
    (8804, "me too 我也觉得", False, "英文词开头"),
    (8805, "/帮助", True, "标准斜杠指令"),
    (8806, [{"type": "at", "data": {"qq": str(SELF)}}, {"type": "text", "data": {"text": " /帮助"}}],
     True, "@机器人后跟指令"),
    (8807, [{"type": "reply", "data": {"id": "1"}}, {"type": "text", "data": {"text": "/帮助"}}],
     True, "引用消息后跟指令"),
    # —— 「串指令」回归：@ 的是别的 QQ，指令不是给我们的，别抢答 ——
    (8808, [{"type": "at", "data": {"qq": "3889001007"}}, {"type": "text", "data": {"text": " /帮助"}}],
     False, "@别的 bot 的指令不抢答"),
    (8809, [{"type": "at", "data": {"qq": "1721883202"}}, {"type": "text", "data": {"text": " /帮助"}}],
     False, "@群成员的指令不抢答"),
    (8810, [{"type": "at", "data": {"qq": str(SELF)}}, {"type": "at", "data": {"qq": "1721883202"}},
            {"type": "text", "data": {"text": " /帮助"}}],
     True, "@了我也 @ 了别人，照样响应"),
    (8811, [{"type": "at", "data": {"qq": "0", "name": "机器人群名片"}},
            {"type": "text", "data": {"text": " /帮助"}}],
     True, "qq=0 的未解析 @ 照常响应"),
]


async def main():
    got = {}
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/onebot/v11/ws",
                                  additional_headers={"X-Self-ID": str(SELF),
                                                      "X-Client-Role": "Universal"},
                                  max_size=None) as ws:
        await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle",
                                  "sub_type": "connect", "self_id": SELF, "time": 0}))
        await asyncio.sleep(0.4)
        for gid, msg, _, _ in CASES:
            await ws.send(json.dumps({
                "post_type": "message", "message_type": "group", "sub_type": "normal",
                "self_id": SELF, "time": 0, "message_id": gid,
                "group_id": gid, "user_id": 666, "message": msg,
                "raw_message": "", "font": 0,
                "sender": {"user_id": 666, "nickname": "tester"},
            }))
            await asyncio.sleep(0.2)
        print(f"→ 已发送 {len(CASES)} 条，等待回复…")
        end = asyncio.get_event_loop().time() + 50
        while asyncio.get_event_loop().time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - asyncio.get_event_loop().time()))
            except asyncio.TimeoutError:
                break
            data = json.loads(raw)
            if data.get("action") == "send_msg":
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1},
                                          "echo": data.get("echo")}))
                got.setdefault(data["params"].get("group_id"), True)
            elif data.get("action"):
                # 协议端要回的其它调用（引用消息会触发 get_msg），不回会卡 30 秒
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                          "data": {"message_id": 1, "time": 0,
                                                   "sender": {"user_id": 666}, "message": "x"},
                                          "echo": data.get("echo")}))

    ok = True
    for gid, _, want, desc in CASES:
        has = gid in got
        mark = "✓" if has == want else "✗"
        if has != want:
            ok = False
        print(f"{mark} [{gid}] {'有回复' if has else '无回复'}（期望{'有' if want else '无'}）— {desc}")
    print("全部通过" if ok else "有用例不符合预期")
    sys.exit(0 if ok else 1)


asyncio.run(main())
