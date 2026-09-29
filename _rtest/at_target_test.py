"""@某人 查询目标的回归测试：段序判定 + CommandArg 取 at + 绑定解析"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nonebot_plugins"))

import nonebot
nonebot.init(driver="~none")
from nonebot.adapters.onebot.v11 import Message, MessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.rule import TrieRule

import destiny2 as m

SELF = "3104813702"
BOT = m
FAILED = []


def parse(e):
    """走 nonebot 真实的前缀解析，拿到 CommandArg"""
    state = {}
    TrieRule.get_value(None, e, state)
    return state["_prefix"]["command_arg"]


def ev(segs, to_me=False, group=True, self_id=SELF):
    msg = Message(segs)
    cls = MessageEvent if group else PrivateMessageEvent
    e = cls(
        time=0, self_id=self_id, post_type="message", message_type="group" if group else "private",
        sub_type="normal", message_id=1, user_id=1234, message=msg,
        raw_message=str(msg), font=0,
        sender=Sender(user_id=1234, nickname="tester"),
        **({"group_id": 999} if group else {}),
    )
    e.to_me = to_me  # 适配器在预处理阶段置位，这里直接模拟
    return e


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}  got={got!r} want={want!r}")
    if not ok:
        FAILED.append(label)


at = lambda qq: m.MessageSegment.at(str(qq))
tx = lambda s: m.MessageSegment.text(s)

print("=== _at_other_only（True=整条丢掉）===")
check("@小日向 /raid  → 丢掉", m._at_other_only(ev([at(3889001007), tx("/raid")]), ev([at(3889001007), tx("/raid")]).get_message()), True)
check("/raid @某人     → 放行", m._at_other_only(ev([tx("/raid "), at(8888)]), ev([tx("/raid "), at(8888)]).get_message()), False)
check("@机器人 /raid @某人 → 放行", m._at_other_only(ev([tx("/raid "), at(8888)], to_me=True), ev([tx("/raid "), at(8888)], to_me=True).get_message()), False)
check("单独 @小日向     → 丢掉", m._at_other_only(ev([at(3889001007)]), ev([at(3889001007)]).get_message()), True)
check("/raid 无 at      → 放行", m._at_other_only(ev([tx("/raid")]), ev([tx("/raid")]).get_message()), False)
check("/raid @机器人自己 → 放行", m._at_other_only(ev([tx("/raid "), at(SELF)]), ev([tx("/raid "), at(SELF)]).get_message()), False)
check("私聊 @别人       → 放行", m._at_other_only(ev([tx("/raid "), at(8888)], group=False), ev([tx("/raid "), at(8888)], group=False).get_message()), False)

print()
print("=== CommandArg 解析出的参数里能不能拿到 at ===")
for label, segs, want_qq in [
    ("/生涯 @小明", [tx("/生涯 "), at(8888)], "8888"),
    ("/raid @小明", [tx("/raid "), at(8888)], "8888"),
    ("/生涯 @小明 名字", [tx("/生涯 "), at(8888), tx(" 名字")], "8888"),
    ("/生涯 名字", [tx("/生涯 名字")], ""),
    ("/生涯", [tx("/生涯")], ""),
    ("/pve生涯武器 @小明 s27", [tx("/pve生涯武器 "), at(8888), tx(" s27")], "8888"),
]:
    e = ev(segs)
    args = parse(e)
    got = m._at_target(args, e) if args is not None else "<无前缀>"
    check(label, got, want_qq)

print()
print("=== /pve生涯武器 @某人 s27 的 scope 仍能解析 ===")
e = ev([tx("/pve生涯武器 "), at(8888), tx(" s27")])
args = parse(e)
raw, scope = m._split_scope(args.extract_plain_text().strip())
check("scope", scope, "s27")
check("raw 为空 → 走 @ 目标", raw, "")

print()
print("=== 绑定解析（用真实 user_bindings.json 的第一个 QQ）===")
bindings = m._load_bindings()
if bindings:
    qq, name = next(iter(bindings.items()))
    got = m._at_target(Message([tx("/生涯 "), at(qq)]), ev([tx("/生涯 "), at(qq)]))
    check(f"@已绑定的 {qq}", got, str(qq))
    check(f"该 QQ 的绑定名", m._load_bindings().get(got), name)
else:
    print("跳过：没有绑定数据")

print()
print("FAILED:", FAILED if FAILED else "none")
