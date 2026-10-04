"""双通道平台层：同一套指令同时服务 NapCat（OneBot v11）与 QQ 官方机器人

NapCat / 协议端（个人号）：群标识就是 QQ 群号；图片走 base64；@ 用 QQ 号。
QQ 官方机器人（q.qq.com，nonebot-adapter-qq）：
  - 只有 group_openid / member_openid，拿不到 QQ 号（绑定表按 openid 另存，不会撞车）；
  - 图片必须先上传富媒体换 file_info（MessageSegment.file_image），
    而且**一条消息只带一个媒体**（适配器只取最后一个媒体段），多图要拆成多条；
  - 被动回复窗口：群聊 5 分钟 / 最多 5 条，超窗口发不出去（只能记日志，没有主动推送）；
  - 群聊只推送 @机器人 的消息 → 不存在「@ 了别的 bot 被误触发」的问题。

业务逻辑（nonebot_plugins/destiny2.py）两边共用，平台差异全部收在这个模块里：
谁发的、哪个群、@ 谁、图怎么发出去。这个模块不依赖官方通道是否可用——
没装/没配官方适配器时全部按 NapCat 行为走。
"""
import json

from nonebot.adapters.onebot.v11 import MessageSegment as OBSegment

try:  # 官方适配器缺失（未安装/未启用）时仍要能跑 NapCat 通道
    from nonebot.adapters.qq import MessageSegment as QQSegment
    from nonebot.adapters.qq.event import QQMessageEvent

    OFFICIAL_AVAILABLE = True
except Exception:  # noqa: BLE001
    OFFICIAL_AVAILABLE = False
    QQSegment = None
    QQMessageEvent = None

CONFIG_FILE = "bot_config.json"


def _cfg() -> dict:
    try:
        return json.load(open(CONFIG_FILE, encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[platform] {CONFIG_FILE} 读取失败（按默认配置处理）："
              f"{type(exc).__name__}: {exc}")
        return {}


# ---------- 平台判定 ----------
def is_official(event) -> bool:
    """QQ 官方适配器事件？（NapCat/OneBot 一律 False）"""
    return OFFICIAL_AVAILABLE and isinstance(event, QQMessageEvent)


def is_message_event(event) -> bool:
    """消息事件（两个适配器的 MessageEvent.get_type() 都返回 "message"）"""
    try:
        return event.get_type() == "message" and hasattr(event, "get_message")
    except Exception:  # noqa: BLE001
        return False


def channel(event) -> str:
    return "official" if is_official(event) else "napcat"


# ---------- 身份 / 群标识 ----------
def uid(event) -> str:
    """发起人的标识：NapCat=QQ 号；官方=member_openid/user_openid（绑定表就按它存）"""
    return str(getattr(event, "user_id", "") or event.get_user_id() or "")


def self_id(event) -> str:
    """机器人自己的标识：OneBot 事件自带 self_id；官方事件没有，只能问当前 bot"""
    sid = getattr(event, "self_id", "") or ""
    if sid:
        return str(sid)
    try:
        from nonebot import get_bot

        return str(get_bot().self_id or "")
    except Exception:  # noqa: BLE001
        return ""


def group_key(event) -> str:
    """群标识：NapCat=群号；官方=group_openid；私聊返回空串"""
    if is_official(event):
        return str(getattr(event, "group_openid", "") or "")
    return str(getattr(event, "group_id", "") or "")


def is_group(event) -> bool:
    return bool(group_key(event))


def nickname(event) -> str:
    if is_official(event):
        author = getattr(event, "author", None)
        return str(getattr(author, "username", "") or "")
    sender = getattr(event, "sender", None)
    if sender is None:
        return ""
    return getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""


def who(event) -> str:
    """后台任务由谁发起，显示在面板进度条上"""
    nick = nickname(event) or (uid(event)[:8] if is_official(event) else "")
    nick = nick or str(uid(event) or "?")
    gid = group_key(event)
    if is_official(event):
        return (f"官方群 {gid[:8]} · {nick}" if gid else f"官方私聊 · {nick}")
    return f"群 {gid} · {nick}" if gid else f"私聊 · {nick}"


# ---------- @ 判定 ----------
def at_target(args, event) -> str:
    """参数里 `@某人` → 那个人的标识（NapCat=QQ 号 / 官方=openid）。

    群里 `/生涯 @小明` 就是查小明的号（用小明的绑定），比 `/生涯 小明#1234` 少打字。
    @ 机器人自己被适配器摘掉了或带 is_bot 标记；qq=0（协议端没解析出号码）不算数。
    官方通道要 mentions 数组里有别的成员才会出 mention_user 段——平台不给就查不到。
    """
    if not args:
        return ""
    me = self_id(event)
    for seg in args:
        data = seg.data or {}
        if is_official(event):
            if seg.type != "mention_user" or data.get("is_bot"):
                continue
            target = str(data.get("user_id") or "")
        else:
            if seg.type != "at":
                continue
            target = str(data.get("qq") or "")
            if target in ("", "0"):
                continue
        if target and target != me:
            return target
    return ""


def at_other_only(event, msg) -> bool:
    """消息里 @ 了别人、却唯独没 @ 我 → 这条不是发给我的，整条丢掉。

    只对 NapCat 通道有意义：群里同时挂着别的查询 bot（比如小日向）时，用户对着
    它发的 `/raid` 我们也会跟着回一遍，群里就变成「一条指令两个 bot 同时回」。
    官方通道只推送 @机器人 的消息，不存在这种误触发，直接放行。
    """
    if is_official(event) or not is_group(event):
        return False
    if getattr(event, "to_me", False):
        return False  # @ 了我，就算还 @ 了别人也照样响应
    me = self_id(event)
    cmd_at = None  # 指令文本段的下标：它之后的 @ 都是查询目标
    for i, seg in enumerate(msg):
        if seg.is_text() and str((seg.data or {}).get("text") or "").lstrip().startswith("/"):
            cmd_at = i
            break
    for i, seg in enumerate(msg):
        if seg.type != "at":
            continue
        qq = str((seg.data or {}).get("qq") or "")
        if qq in ("", "0", me):
            continue
        if cmd_at is None or i < cmd_at:
            return True  # 指令之前（或压根没指令）@ 了别的 QQ
    return False


def at_me_only(event) -> bool:
    """直查的触发条件（@机器人 + 名字 → 自动出卡片）：真 @ 我、群聊。

    NapCat 通道要注意 nonebot 的 to_me 有三条来源，其中两条会误伤：
      - 「引用/回复机器人的消息」→ 适配器 _check_reply 把 to_me 置真（没 @ 也是真）；
      - 私聊 → 恒为真。
    官方通道的群 @ 事件本来就是 @ 我（引用机器人消息不会被推送），不用再排引用；
    私聊同样不做直查。
    """
    if is_official(event):
        return is_group(event) and bool(getattr(event, "to_me", False))
    if not getattr(event, "to_me", False):
        return False
    if not is_group(event):
        return False
    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None) if reply is not None else None
    if sender is not None and str(getattr(sender, "user_id", "")) == str(getattr(event, "self_id", "")):
        return False  # 只是引用机器人自己的消息，没有 @
    return True


# ---------- 消息段构造 ----------
def text_seg(event, text: str):
    return QQSegment.text(text) if is_official(event) else OBSegment.text(text)


def image_seg(event, png: bytes, name: str = "card.png"):
    """图片段：NapCat 走 base64 直发；官方走富媒体上传（到真正发送时才上传）"""
    if is_official(event):
        return QQSegment.file_image(png, name)
    import base64

    return OBSegment.image("base64://" + base64.b64encode(png).decode())


def at_seg(event, target: str):
    return QQSegment.mention_user(target) if is_official(event) else OBSegment.at(str(target))


def join(*segs):
    """把若干段拼成对应适配器的 Message（段类型自带 message class）"""
    cls = type(segs[0]).get_message_class()
    msg = cls()
    for seg in segs:
        msg.append(seg)
    return msg


def at_back_enabled() -> bool:
    """回复里要不要 @ 发起人。NapCat 一直 @（小日向同款）。

    官方群的 @ 语法（适配器渲染成 <@openid>）没在官方文档里得到确认，
    默认不 @——被动回复本身就回落在那条消息上；确认渲染没问题后把
    bot_config.json 的 official_at_back 改成 true 即可，不用重新打包。
    """
    return bool(_cfg().get("official_at_back"))


def reply_msg(event, *segs):
    """群聊回复统一入口：按平台决定要不要在前缀 @ 发起人"""
    if is_group(event) and (not is_official(event) or at_back_enabled()):
        target = uid(event)
        if target:
            return join(at_seg(event, target), text_seg(event, " "), *segs)
    return join(*segs)


def multi_media_ok(event) -> bool:
    """一条消息能不能带多张图：NapCat 能；官方只能带一个媒体（多图必须拆条）"""
    return not is_official(event)


def official_groups_allow(event) -> bool:
    """官方群开关：bot_config.json 的 official_groups（openid 列表），空 = 全部响应。

    与 NapCat 的 enabled_groups 分开存：一边是 QQ 群号，一边是 openid，混在一起
    面板上的群开关会把官方群判成"未勾选"。
    """
    en = _cfg().get("official_groups") or []
    return not en or group_key(event) in [str(x) for x in en]
