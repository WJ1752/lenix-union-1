"""指令路由探针（客户端）：伪装协议端接上探针 bot，逐条发消息并打印回复标签。

用法：先跑 _rtest\\route_probe_bot.py，再跑本脚本。
"""
import asyncio
import base64
import json
import sys

import websockets

PORT = sys.argv[1] if len(sys.argv) > 1 else "8081"
SELF = 3104813702  # 真实 bot QQ，at 段必须指向它才会被当成 to_me

# 绑定过账号的 QQ：这样 /raid 不带名字也能跑
BOUND_UID = 1953008905

CASES = [
    ("A @Bot /raid",        [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "text", "data": {"text": " /raid"}}]),
    ("B 裸 /raid",           "/raid"),
    ("C /raid Wj#8984",      "/raid Wj#8984"),
    ("D @Bot /pvp",         [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "text", "data": {"text": " /pvp"}}]),
    ("E @Bot 秋风",          [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "text", "data": {"text": " 秋风"}}]),
    ("F @Bot /常用武器",     [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "text", "data": {"text": " /常用武器"}}]),
    ("G 引用Bot + /raid",    "REPLY1"),
    ("H @Bot /帮助",         [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "text", "data": {"text": " /帮助"}}]),
    # —— 本轮的 bug：@ 的是别人的命令，不该回 ——
    ("I @小日向(3889001007) /raid",
     [{"type": "at", "data": {"qq": "3889001007"}},
      {"type": "text", "data": {"text": " /raid"}}]),
    ("J @小日向 /锻造 X",
     [{"type": "at", "data": {"qq": "3889001007"}},
      {"type": "text", "data": {"text": " /锻造 Vesper#4745"}}]),
    ("K @队友 /pvp",         [{"type": "at", "data": {"qq": "1721883202"}},
                             {"type": "text", "data": {"text": " /pvp"}}]),
    ("L @本机+@队友 /raid",  [{"type": "at", "data": {"qq": str(SELF)}},
                             {"type": "at", "data": {"qq": "1721883202"}},
                             {"type": "text", "data": {"text": " /raid"}}]),
    ("M @本机 秋风",         [{"type": "at", "data": {"qq": "0", "name": "小日向Bot"}},
                             {"type": "text", "data": {"text": " /帮助"}}]),
]


async def main():
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/onebot/v11/ws",
                                  additional_headers={"X-Self-ID": str(SELF),
                                                      "X-Client-Role": "Universal"},
                                  max_size=None) as ws:
        await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle",
                                  "sub_type": "connect", "self_id": SELF, "time": 0}))
        await asyncio.sleep(0.5)
        print(f"→ 发送 {len(CASES)} 条…", flush=True)
        for i, (label, msg) in enumerate(CASES):
            if msg == "REPLY1":
                # 引用机器人自己发的一条消息（模拟 QQ 的「回复」），后面再跟指令
                msg = [{"type": "reply", "data": {"id": "999"}},
                       {"type": "text", "data": {"text": " /raid"}}]
            await ws.send(json.dumps({
                "post_type": "message", "message_type": "group", "sub_type": "normal",
                "self_id": SELF, "time": 0, "message_id": 9000 + i,
                "group_id": 9900 + i, "user_id": BOUND_UID, "message": msg,
                "raw_message": "", "font": 0,
                "sender": {"user_id": BOUND_UID, "nickname": "tester", "card": "tester"},
            }))
            await asyncio.sleep(0.3)
        # 收回复
        end = asyncio.get_event_loop().time() + 40
        done = {}
        while asyncio.get_event_loop().time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                break
            data = json.loads(raw)
            act = data.get("action")
            if act:
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                          "data": {"message_id": 1, "message": []},
                                          "echo": data.get("echo")}))
            if act == "send_msg":
                gid = data["params"].get("group_id")
                m = data["params"].get("message")
                cid = None
                for seg in (m if isinstance(m, list) else []):
                    if seg.get("type") == "image":
                        cid = seg.get("data", {}).get("file", "")[:0]
                done[gid] = (str(m)[:70], cid)
        for i, (label, _) in enumerate(CASES):
            gid = 9900 + i
            got = done.get(gid)
            print(f"{gid} {label:22s} → {'有回复' if got else '无回复'} {got[0][:60] if got else ''}",
                  flush=True)


asyncio.run(main())
