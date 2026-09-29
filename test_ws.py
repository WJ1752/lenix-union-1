"""模拟 NapCat 反向 WS 连接，测试 d2 指令（机器人的图片回复会存成 _ws_out.png）

用法：
    .venv\\Scripts\\python test_ws.py                                   # 默认 /perk查询 热力四射 @ 8081（bot.py）
    .venv\\Scripts\\python test_ws.py "/玩家 Wj#8984" 8901               # webui / exe 的 bot 在 8901
"""
import asyncio
import base64
import json
import sys

import websockets

CMD = sys.argv[1] if len(sys.argv) > 1 else "/perk查询 热力四射"
PORT = sys.argv[2] if len(sys.argv) > 2 else "8081"
SELF = 10000  # 假 self_id，别用真 QQ 号


async def main():
    async with websockets.connect(
        f"ws://127.0.0.1:{PORT}/onebot/v11/ws",
        additional_headers={"X-Self-ID": str(SELF), "X-Client-Role": "Universal"},
        max_size=None,  # 卡片截图 base64 常超过默认 1MB，测试端别掐断
    ) as ws:
        await ws.send(json.dumps({
            "post_type": "meta_event", "meta_event_type": "lifecycle",
            "sub_type": "connect", "self_id": SELF, "time": 0,
        }))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "post_type": "message", "message_type": "group", "sub_type": "normal",
            "self_id": SELF, "time": 0, "message_id": 1,
            "group_id": 888, "user_id": 666,
            "message": CMD, "raw_message": CMD, "font": 0,
            "sender": {"user_id": 666, "nickname": "tester"},
        }))
        print(f"→ 已发送：{CMD}")
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                print("× 60 秒内没有回复（机器人没跑？插件加载失败了？消息日志里能看到收到的指令）")
                return
            data = json.loads(raw)
            if data.get("action") != "send_msg":
                continue
            # 回 ack，模拟真实协议端；否则 bot 的 call_api 会一直等到超时
            await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                      "data": {"message_id": 1}, "echo": data.get("echo")}))
            for seg in data["params"].get("message", []):
                d = seg.get("data", {})
                d = d.get("file", "") if isinstance(d, dict) else str(d)
                if seg["type"] == "image":
                    print(f"← 收到【图片】{str(d)[:40]}")
                    if d.startswith("base64://"):
                        open("_ws_out.png", "wb").write(base64.b64decode(d[9:]))
                        print("   已存 _ws_out.png")
                else:
                    print(f"← 收到【{seg['type']}】{str(d)[:200]}")
            return


asyncio.run(main())
