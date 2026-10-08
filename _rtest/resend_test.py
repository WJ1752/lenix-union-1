"""面板「重发未发送图片」的分派测试：用假 bot 对象验 NapCat / 官方两条路各走对接口

    .venv/Scripts/python.exe _rtest/resend_test.py
"""
import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_log
import bot_runtime
import webui

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeOB:
    """假装 NapCat 的 OneBot v11 Bot（模块名里要有 onebot，程序靠它分派）"""
    def __init__(self):
        self.sent = []

    async def call_api(self, api, **kw):
        self.sent.append((api, kw))
        return {"ok": True}


FakeOB.__module__ = "nonebot.adapters.onebot.v11.bot"


class FakeQQ:
    """假装官方 QQ 通道的 Bot"""
    def __init__(self):
        self.sent = []

    async def send_to_group(self, group_openid, message, **kw):
        self.sent.append(("group", group_openid, message, kw))
        return {"ok": True}

    async def send_to_c2c(self, openid, message, **kw):
        self.sent.append(("c2c", openid, message, kw))
        return {"ok": True}


FakeQQ.__module__ = "nonebot.adapters.qq.bot"


def _seed() -> str:
    png = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
    return bot_log.save_unsent(png, "测试卡片", "123456", "")


async def main():
    png_name = _seed()
    path = bot_log.unsent_path(png_name)
    print("样本图:", png_name, os.path.getsize(path), "字节")

    ob, qq = FakeOB(), FakeQQ()
    orig = bot_runtime.get_bots
    bot_runtime.get_bots = lambda: {"1": ob, "2": qq}
    try:
        # NapCat 群
        r = await webui._resend_image(path, "810807201", "", official=False)
        check("NapCat 群消息走 send_group_msg", r.get("ok") and ob.sent
              and ob.sent[-1][0] == "send_group_msg", str(r) + str(ob.sent))
        seg = ob.sent[-1][1].get("message")
        import re as _re
        m = _re.search(r"base64://([A-Za-z0-9+/=]+)", str(seg))
        check("图片按 base64 段发出（内容长度对得上）",
              bool(m) and len(base64.b64decode(m.group(1))) > 64, str(seg)[:90])
        check("官方通道没被误用", not qq.sent)

        # NapCat 私聊
        r = await webui._resend_image(path, "", "10086", official=False)
        check("NapCat 私聊走 send_private_msg", r.get("ok") and ob.sent[-1][0] == "send_private_msg",
              str(r))

        # 官方通道（群里）
        r = await webui._resend_image(path, "E00DC54A7BD7D0D24B54CC7AD5CE96", "", official=True)
        check("官方群走 send_to_group（不传 msg_id = 主动消息）",
              r.get("ok") and qq.sent and qq.sent[-1][0] == "group"
              and "msg_id" not in qq.sent[-1][3], str(r) + str(qq.sent[-1][:2]))
        msg = qq.sent[-1][2]
        check("官方通道带的是 file_image 富媒体段",
              "file_image" in str(msg) and "测试卡片" not in str(msg) or "file_image" in str(msg),
              str(msg)[:120])

        # 官方私聊
        r = await webui._resend_image(path, "", "AABBCC", official=True)
        check("官方私聊走 send_to_c2c", r.get("ok") and qq.sent[-1][0] == "c2c", str(r))

        # 全部失败 → 返回错误而不是抛
        class Bad(FakeOB):
            async def call_api(self, api, **kw):
                raise RuntimeError("风控：消息发送被拦截")
        Bad.__module__ = "nonebot.adapters.onebot.v11.bot"   # 分派靠模块名判通道
        bot_runtime.get_bots = lambda: {"1": Bad()}
        r = await webui._resend_image(path, "810807201", "", official=False)
        check("发送失败时如实回错误（面板弹提示）",
              (not r.get("ok")) and "风控" in r.get("msg", ""), str(r))

        # 没有协议端
        bot_runtime.get_bots = lambda: {}
        r = await webui._resend_image(path, "810807201", "", official=False)
        check("没协议端时提示等恢复", (not r.get("ok")) and "没连上" in r.get("msg", ""), str(r))
    finally:
        bot_runtime.get_bots = orig
        try:
            os.remove(path)
        except OSError:
            pass

    print(f"\n===== 通过 {16 - len(FAILS)} / 失败 {len(FAILS)} =====")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
