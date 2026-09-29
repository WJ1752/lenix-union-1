"""_resolve_name 端到端：@已绑定 → 出 TA 的名字；@没绑定 → 提示不退回自己"""
import sys, os, asyncio, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "nonebot_plugins"))

import nonebot
nonebot.init(driver="~none")
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender

import destiny2 as m

SELF = "3104813702"
BIND = os.path.join(ROOT, "dist", "D2Query", "user_bindings.json")
m.d2.bind_path = lambda: BIND  # 指向实际部署用的绑定文件

bindings = json.load(open(BIND, encoding="utf-8"))
qq_bound, name_bound = next(iter(bindings.items()))
print(f"真实绑定样例：{qq_bound} → {name_bound}（共 {len(bindings)} 条）")

notices = []
async def fake_notice(matcher, event, title, lines, kind="info", fallback=""):
    notices.append((title, kind))
m._notice = fake_notice


def ev(segs, user_id=1234):
    msg = Message(segs)
    e = MessageEvent(
        time=0, self_id=SELF, post_type="message", message_type="group", sub_type="normal",
        message_id=1, user_id=user_id, message=msg, raw_message=str(msg), font=0,
        sender=Sender(user_id=user_id, nickname="tester"), group_id=999,
    )
    e.to_me = False
    return e


at = lambda qq: MessageSegment.at(str(qq))
tx = lambda s: MessageSegment.text(s)
failed = []

def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}  got={got!r} want={want!r}")
    if not ok:
        failed.append(label)


async def main():
    # 1) @ 一个已绑定的人 → 返回 TA 的绑定名
    e = ev([tx("/生涯 "), at(qq_bound)])
    args = Message([at(qq_bound)])
    notices.clear()
    got = await m._resolve_name(None, e, args.extract_plain_text(), args)
    check("@已绑定的人 → 出 TA 的账号", got, name_bound)
    check("不弹提示", notices, [])

    # 2) @ 一个没绑定的人（且发起人自己有绑定）→ 提示 TA 没绑定，而不是查发起人自己
    e2 = ev([tx("/生涯 "), at("10001")], user_id=int(qq_bound))
    args2 = Message([at("10001")])
    notices.clear()
    got2 = await m._resolve_name(None, e2, args2.extract_plain_text(), args2)
    check("@没绑定的人 → 不返回名字", got2, None)
    check("给出「对方还没绑定」提示", [n[0] for n in notices], ["对方还没绑定账号"])

    # 3) 没 @ 时行为不变：仍走发起人自己的绑定
    e3 = ev([tx("/生涯")], user_id=int(qq_bound))
    notices.clear()
    got3 = await m._resolve_name(None, e3, "", Message([]))
    check("不带参数 → 仍用发起人自己的绑定", got3, name_bound)

    # 4) 显式写名字仍然优先
    e4 = ev([tx("/生涯 别人#1234 "), at(qq_bound)])
    got4 = await m._resolve_name(None, e4, "别人#1234", Message([at(qq_bound)]))
    check("显式名字优先于 @", got4, "别人#1234")

asyncio.run(main())
print()
print("FAILED:", failed if failed else "none")
