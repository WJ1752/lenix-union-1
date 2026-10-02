"""官方 QQ 机器人最小验证脚本（NapCat/个人号之外的第二条通道）

验证四件事：
  1. 用 AppID/AppSecret 能连上官方网关（WebSocket，出站连接，不需要公网服务器）
  2. 群里 @机器人 能收到 GROUP_AT_MESSAGE_CREATE 事件
  3. 能被动回复文本（带 msg_id，5 分钟窗口）
  4. 能发一张图片（走官方富媒体上传换取 file_info —— 与 NapCat 直发图最大的差别，
     这一步必须单独验证，卡片能不能进群全看它）

用法：
  1. 开放平台 → 你的机器人 → 开发设置：复制 AppID / AppSecret
  2. 填进同目录 qq_official_creds.json（模板：qq_official_creds.example.json）
  3. .venv/Scripts/python.exe qq_official_smoke.py
  4. 在机器人所在群里 @雷尼克斯联合-1 说句话

可选：
  --image <路径>   改发指定图片（例：--image _tmp_real_card.png 发一张真实卡片）
  --selftest       只做本地自检（生成测试图、校验配置，不连网、不需要凭证）

凭证只在本机读取；qq_official_creds.json 已在 .gitignore 里，不要外发或提交。
"""

import argparse
import json
import pathlib
import struct
import sys
import zlib

import nonebot
from nonebot import on_message
from nonebot.adapters.qq import Adapter, GroupAtMessageCreateEvent, MessageSegment
from nonebot.adapters.qq.config import Intents

BASE_DIR = pathlib.Path(__file__).resolve().parent
CREDS_FILE = BASE_DIR / "qq_official_creds.json"

# 只要群 @ 消息这一个 intent；其余显式关掉——申请了没审批的 intent 会被网关拒绝
ONLY_GROUP_INTENTS = {
    "guilds": False,
    "guild_members": False,
    "guild_message_reactions": False,
    "message_audit": False,
    "at_messages": False,
    "c2c_group_at_messages": True,
}

IMAGE_PATH: pathlib.Path | None = None


def _load_creds() -> tuple[str, str]:
    if not CREDS_FILE.exists():
        sys.exit(
            f"缺少 {CREDS_FILE.name}。把它照着 qq_official_creds.example.json 建好再跑：\n"
            f"  开放平台 → 你的机器人 → 开发设置 → AppID / AppSecret"
        )
    data = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    appid = str(data.get("appid") or "").strip()
    secret = str(data.get("secret") or "").strip()
    if not appid or not secret or appid.startswith("填"):
        sys.exit("qq_official_creds.json 里的 appid / secret 还是空的或占位文本，先填真值")
    return appid, secret


def _make_test_png(width: int = 600, height: int = 240) -> bytes:
    """生成一张渐变测试图（不依赖 Pillow/Playwright），验证富媒体上传用"""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # 每行的 filter 字节
        for x in range(width):
            rows += bytes((30 + x * 60 // width, 30 + y * 60 // height, 90))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit truecolor
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def _build_app(appid: str, secret: str) -> None:
    nonebot.init(
        driver="~httpx+~websockets",
        log_level="INFO",
        qq_bots=[{"id": appid, "secret": secret, "intent": ONLY_GROUP_INTENTS}],
    )
    nonebot.get_driver().register_adapter(Adapter)

    matcher = on_message(priority=1, block=True)

    @matcher.handle()
    async def _smoke(event: GroupAtMessageCreateEvent) -> None:
        text = event.get_message().extract_plain_text().strip()
        print(f"[收到] 群 {event.group_openid} · 成员 {event.author.member_openid} · 内容 {text!r}")
        await matcher.send(
            MessageSegment.text(
                "官方通道 OK\n"
                f"收到：{text or '(只有@)'}\n"
                f"群 openid：{event.group_openid}\n"
                f"你的 member_openid：{event.author.member_openid}"
            )
        )
        if IMAGE_PATH is not None:
            png, name = IMAGE_PATH.read_bytes(), IMAGE_PATH.name
            print(f"[发送] 图片 {IMAGE_PATH}（{len(png)} bytes）")
        else:
            png, name = _make_test_png(), "smoke.png"
            print(f"[发送] 生成的测试图（{len(png)} bytes）")
        await matcher.send(MessageSegment.file_image(png, name))


def main() -> None:
    global IMAGE_PATH
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 cp936，中文日志会炸
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="官方 QQ 机器人最小验证")
    parser.add_argument("--image", help="改发指定图片（默认发脚本生成的测试图）")
    parser.add_argument("--selftest", action="store_true", help="只做本地自检，不连网")
    args = parser.parse_args()

    if args.image:
        IMAGE_PATH = pathlib.Path(args.image)
        if not IMAGE_PATH.exists():
            sys.exit(f"--image 指定的文件不存在：{IMAGE_PATH}")

    if args.selftest:
        png = _make_test_png()
        print(f"[自检] 测试图 {len(png)} bytes，PNG 魔数 {png[:8]!r}")
        _build_app("0", "selftest")
        print(f"[自检] intents = {Intents(**ONLY_GROUP_INTENTS).to_int()}（群 @ 消息位=25）")
        print("[自检] 配置与处理函数都没问题；去掉 --selftest 即连官方网关")
        return

    appid, secret = _load_creds()
    print(f"[启动] AppID {appid[:6]}***，连官方网关（WebSocket）…")
    _build_app(appid, secret)
    nonebot.run()


if __name__ == "__main__":
    main()
