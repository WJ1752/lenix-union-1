"""命运2 玩家查询插件

**输出全部是小日向式图片卡片**（HTML 排版 + 无头浏览器截图，见 bot_cards.py / card_render.py），
不再回纯文本；只有当渲染器彻底不可用时才退回文本。

触发词对齐小日向（`/中文` 形式），**必须带 `/` 前缀**，且命令词后要紧跟空白或直接结束
（`pve是顺手写的` 这种连写不会被当成指令）；旧 `d2` 系列保留为别名：
  /绑定 玩家名#1234   → 绑定自己的账号（之后玩家查询可省去名字）
  /解绑                → 解除绑定
  /我的                → 查看当前绑定账号（别名 账号）
  /玩家 玩家名#1234    → 基础信息（最高光能、各角色）      别名 d2；已绑定可省略名字
  /生涯 玩家名#1234    → 生涯统计（击杀/死亡/KD/场次）      别名 d2周报、周报；已绑定可省略名字
  /raid                → 突袭战绩（通关/无暇/大师/单人双人三人/无暇）别名 突袭、d2raid
  /地牢                → 地牢战绩                          别名 dungeon、d2地牢
  /pvp                 → 熔炉：生涯统计 + 近期战绩 + 模式细分  别名 熔炉、d2pvp
  /pve                 → PVE：生涯统计 + 近期战绩 + 模式细分   别名 d2pve
  /智谋                → 智谋战绩                          别名 gambit、d2智谋
  /历史                → 最近对局流（全模式）                别名 战绩、最近对局、d2历史
  /热力图              → 按赛季分组的全历史活跃日历           别名 活跃、d2热力图
  /锻造                → 武器锻造图案进度                   别名 图案、d2锻造
  /称号                → 称号进度（含镀金）                 别名 d2称号
  /常用武器 [范围]     → PVP 武器排名 + 爆头率（默认全生涯）      别名 生涯武器、pvp生涯武器、武器统计、mvp
                         范围可写 s27 / 赛季27 / 全生涯，例：/pvp生涯武器 s27
  /武器查询 <名称>     → 武器 perk 池（特性/枪管/弹匣/枪托）别名 d2武器
  /perk查询 <名称>     → perk 官方说明 + 中文精确数值       别名 d2perk、d2特性
  /护甲查询 <名称>     → 异域护甲特性卡（职业金显示全部特性组合）别名 d2护甲
  /护甲套装 [套装名]   → 护甲套装 2/4 件效果（Starside 中文数值）别名 套装效果、d2套装、套装
                         不带名字出全部套装索引；带名字/别名出单套全文
                         （别名：炽天使套 / 一愿 / 遗愿 / 梦魇 / vog / kf / vow / ce …）
  @机器人 <名称>       → 群里直接 @ 机器人接武器名/护甲名/perk名，自动出对应卡片（小日向式）
  /帮助                → 指令一览                          别名 help、菜单

玩家类指令（/玩家 /生涯 /raid /地牢 /pvp /pve /智谋 /历史 /热力图 /锻造 /称号
/生涯武器 /pve生涯武器）后面可以跟一个 `@某人`：对方绑定过账号就直接查 TA 的，
例 `/生涯 @小明`、`/pve生涯武器 @小明 s27`。**@ 必须放在指令后面**；对方没绑定时
提示「TA 还没绑定」，不会悄悄退回发起人自己的账号。

群里 @ 的是**别的 QQ**（别的 bot、别的群成员）时整条消息不响应——那种指令不是给我们的，
否则会和群里其他 bot 抢答；@ 了机器人自己（或谁都没 @ 的裸指令）才响应。
唯一例外是上面这条：`/指令 @某人` 里的 @ 在指令**之后**，那是查询目标、不是对别的 bot 说话。
"""
import asyncio
import base64
import html as _html
import json
import re
import time

from nonebot import on_command, on_message, on_type
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, IgnoredException, SkippedException
from nonebot.internal.matcher import Matcher
from nonebot.message import event_preprocessor, run_postprocessor
from nonebot.params import CommandArg
from nonebot.rule import Rule

import bot_cards

try:
    import weapon_usage
except Exception:  # noqa: BLE001
    weapon_usage = None
import bot_log
import card_render
import destiny_data as d2
import raid_loot
import weapon_filter as wf


def _allowed_group(event: Event) -> bool:
    """群开关：bot_config.json 里 enabled_groups 为空 = 全部响应"""
    gid = getattr(event, "group_id", None)
    if gid is None:
        return True  # 私聊始终允许
    try:
        cfg = json.load(open("bot_config.json", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return True
    en = cfg.get("enabled_groups") or []
    return not en or str(gid) in en


# ---------- 消息日志（面板可见：哪个群说了什么、机器人回了什么） ----------
def _nickname(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender is None:
        return ""
    return getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""


def _is_cmd(text: str) -> bool:
    """面板日志判定：以 `/` 开头就记（含拼错的，方便排查「发了却没回」）"""
    return text.strip().startswith("/")


def _who(event: Event) -> str:
    """后台任务由谁发起，显示在面板进度条上"""
    nick = _nickname(event) or str(getattr(event, "user_id", "") or "?")
    gid = getattr(event, "group_id", None)
    return f"群 {gid} · {nick}" if gid else f"私聊 · {nick}"


def _at_other_only(event: MessageEvent, msg: Message) -> bool:
    """消息里 @ 了别人、却唯独没 @ 我 → 这条不是发给我的，整条丢掉。

    群里同时挂着别的查询 bot（比如小日向 3889001007）时，用户对着它发的
    `/raid`、`/pvp`、`/锻造` 我们也会跟着回一遍（NapCat 日志里 17:51、18:03 各一次），
    群里看到的就是「一条指令两个 bot 同时回」。on_command 在 nonebot 2.5 里**不带**
    to_me 规则（裸写指令也要响应），所以这个判定只能自己做。

    「有没有 @ 我」只看 `event.to_me`：适配器的 `_check_at_me` 在消息预处理阶段就把
    开头的 `@我` 段**摘掉了**，再在这里扫 qq 是扫不到的（踩过这个坑）。剩下的 at 段
    要么是 @ 别人，要么协议端没解析出号码（qq=0，例如 @ 机器人的群名片），后者按
    「不是别人」放行。

    区分靠 **@ 在指令的前面还是后面**：`@小日向 /raid`（@ 在前）是对着别的 bot 说话，
    丢掉；`/raid @某人`（@ 在后）是「查这个人」，放行给指令响应器（见 `_at_target`）。
    """
    if getattr(event, "group_id", None) is None:
        return False  # 私聊不会有 at
    if getattr(event, "to_me", False):
        return False  # @ 了我，就算还 @ 了别人也照样响应
    self_id = str(getattr(event, "self_id", "") or "")
    cmd_at = None  # 指令文本段的下标：它之后的 @ 都是查询目标
    for i, seg in enumerate(msg):
        if seg.is_text() and str((seg.data or {}).get("text") or "").lstrip().startswith("/"):
            cmd_at = i
            break
    for i, seg in enumerate(msg):
        if seg.type != "at":
            continue
        qq = str((seg.data or {}).get("qq") or "")
        if qq in ("", "0", self_id):
            continue
        if cmd_at is None or i < cmd_at:
            return True  # 指令之前（或压根没指令）@ 了别的 QQ
    return False


def _as_command(event: MessageEvent):
    """把开头的 引用 / 表情 等非文本段去掉（@机器人 由适配器自己处理）。

    NoneBot 解析指令前缀（TrieRule）发生在「事件预处理之后、响应器之前」，
    所以这一步必须挂在事件预处理上：否则「引用某条消息再发 /pve」在 Trie 解析时
    第一段是 reply、不是文本，前缀直接为空 → 所有指令都不匹配，表现为静默丢消息。

    顺带在**段还没被裁掉之前**做「@ 的是别人吗」判定：裁掉 at 段之后就看不出来了，
    而事件预处理是并发跑的（nonebot 里存的是 set，没有先后顺序），所以不能另开一个
    预处理函数去读原始消息。
    """
    if event.get_type() != "message":
        return
    msg = event.get_message()
    if _at_other_only(event, msg):
        # 不处理，但要留痕：面板上看得到「为什么这条没回」
        bot_log.add("in", text=msg.extract_plain_text().strip(), 
                    group_id=getattr(event, "group_id", "") or "",
                    user_id=str(getattr(event, "user_id", "") or ""),
                    nickname=_nickname(event),
                    extra={"enabled": _allowed_group(event), "skip": "@了别人"})
        raise IgnoredException("消息 @ 的是其他 QQ，不是本机器人")
    while len(msg) > 1 and not msg[0].is_text():
        msg.pop(0)


event_preprocessor(_as_command)


def _log_out(event: Event, text: str):
    bot_log.add("out", text=text,
                group_id=getattr(event, "group_id", "") or "",
                user_id=getattr(event, "self_id", ""), nickname="Bot")


def _mention(event: Event) -> Message | None:
    """群里回复时 @ 一下发起人（小日向同款：不然对方不知道是回给自己的）；私聊不加"""
    uid = getattr(event, "user_id", None)
    if not uid or getattr(event, "group_id", None) is None:
        return None
    return Message([MessageSegment.at(str(uid)), MessageSegment.text(" ")])


def _at_sender(event: Event, seg):
    """把回复内容前面接上 @发起人（群聊）；私聊原样返回"""
    head = _mention(event)
    return head + seg if head is not None else seg


async def _reply(matcher, event: Event, text: str):
    """文本回复（仅渲染失败时的兜底）"""
    _log_out(event, text)
    await matcher.finish(_at_sender(event, MessageSegment.text(text)))


async def _reply_image(matcher, event: Event, png: bytes, label: str):
    """图片回复：面板日志记一行说明，QQ 里发图（群里顺手 @ 发起人）

    发送失败（协议端掉线/重连中）单独记一行，否则这种「回复丢失」在面板上看不出来"""
    _log_out(event, f"[图片] {label}")
    seg = MessageSegment.image("base64://" + base64.b64encode(png).decode())
    try:
        await matcher.finish(_at_sender(event, seg))
    except FinishedException:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_out(event, f"[发送失败] {label}：{exc}")
        raise


async def _send_card(matcher, event: Event, html: str, label: str, fallback: str):
    """HTML 卡片 → 截图 → 发图；渲染失败才回退文本"""
    try:
        png = await card_render.html_to_png(html)
    except Exception as exc:  # noqa: BLE001
        await _reply(matcher, event, f"{fallback}\n（图片渲染失败：{exc}）")
        return
    await _reply_image(matcher, event, png, label)


async def _notice(matcher, event: Event, title: str, lines: list[str],
                  kind: str = "info", fallback: str = ""):
    await _send_card(matcher, event, bot_cards.notice(title, lines, kind),
                     title, fallback or f"{title}\n" + "\n".join(lines))


def _exc_msg(exc: Exception) -> str:
    # 超时类异常（httpx.ReadTimeout 等）的 str() 是空串，只拼 exc 会显示成
    # 「接口暂时不可用：」，带上类型名才能看出是超时
    return f"{type(exc).__name__}: {exc}".strip()


@run_postprocessor
async def _unhandled_error(matcher: Matcher, event: Event, exception: Exception):
    """处理器里没接住的异常（如 Bungie 返回非 JSON 时的 JSONDecodeError）不该静默——
    此前 /raid 在网络抖动时就是「收了指令毫无回复」，用户以为机器人挂了"""
    if isinstance(exception, (FinishedException, SkippedException, IgnoredException)):
        return
    try:
        await _notice(matcher, event, "查询失败",
                      [f"指令处理出错：{type(exception).__name__}",
                       "多为 Bungie 接口或网络抖动，稍后重发一次即可"],
                      kind="err", fallback=f"查询失败：{type(exception).__name__}，稍后重试")
    except Exception:  # noqa: BLE001  兜底里再出错就只留日志
        _log_out(event, f"[兜底失败] {type(exception).__name__}: {exception}")


# ---------- 账号绑定（小日向式快捷指令：绑定后玩家查询可省去名字） ----------
BIND_HINT = ("还没有绑定账号", ["先发 <code>/绑定 玩家名#1234</code>，之后 <code>/生涯</code>、"
                              "<code>/玩家</code> 就不用再带名字了"])


def _load_bindings() -> dict:
    try:
        return json.load(open(d2.bind_path(), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_bindings(d: dict):
    json.dump(d, open(d2.bind_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _uid(event: Event) -> str:
    return str(getattr(event, "user_id", "") or "")


def _bound_name(event: Event) -> str:
    return _load_bindings().get(_uid(event), "")


def _at_target(args: Message | None, event: Event) -> str:
    """参数里 `@某人` → 那个人的 QQ（空串＝没 @ 别人）

    群里 `/生涯 @小明` 就是查小明的号（用小明的绑定），比 `/生涯 小明#1234` 少打字。
    @ 机器人自己被适配器摘掉了；qq=0（协议端没解析出号码）不算数。
    """
    if not args:
        return ""
    self_id = str(getattr(event, "self_id", "") or "")
    for seg in args:
        if seg.type != "at":
            continue
        qq = str((seg.data or {}).get("qq") or "")
        if qq and qq not in ("0", self_id):
            return qq
    return ""


async def _resolve_name(matcher, event: Event, raw: str,
                        args: Message | None = None) -> str | None:
    """玩家查询取名字：带参数用参数，否则用参数里 @ 的那个人（得有绑定），
    再否则用发起人自己的绑定，都没有则提示绑定并结束。
    参数不带 #编号 时走模糊搜索：唯一命中直接查，重名列出候选让用户补编号"""
    q = (raw or "").strip()
    if q:
        if "#" not in q:
            return await _fuzzy_player(matcher, event, q)
        return q
    qq = _at_target(args, event)
    if qq:
        b = _load_bindings().get(qq, "")
        if b:
            return b
        # 明确 @ 了人却查不到：报「TA 没绑定」，不要悄悄退回发起人自己的账号
        await _notice(matcher, event, "对方还没绑定账号",
                      ["TA 还没绑定，让他发 <code>/绑定 玩家名#1234</code>；",
                       "或者直接 <code>/指令 玩家名#1234</code> 查他"],
                      kind="warn",
                      fallback="对方还没绑定账号，让他先 /绑定 玩家名#1234")
        return None
    b = _bound_name(event)
    if b:
        return b
    await _notice(matcher, event, *BIND_HINT, fallback=BIND_HINT[0] + "：" + BIND_HINT[1][0])
    return None


async def _player_arg(matcher, event: Event, args: Message) -> str | None:
    return await _resolve_name(matcher, event, args.extract_plain_text(), args)


async def _fuzzy_player(matcher, event: Event, q: str) -> str | None:
    """不带 #编号 的名字：Bungie 模糊搜索，唯一命中直接出，重名让用户补编号"""
    try:
        cands = await d2.search_players_fuzzy(q)
    except Exception:  # noqa: BLE001  搜索挂了就走原来的精确解析，让它报该报的错
        return q
    uniq = {}
    for p in cands:
        uniq[(p.get("bungieGlobalDisplayName"),
              p.get("bungieGlobalDisplayNameCode"))] = p
    if not uniq:
        await _notice(matcher, event, "没找到这个玩家",
                      [f"本地名单里没有「{q}」开头的玩家。",
                       "Bungie 已关闭不带编号的模糊搜索，如果 TA 没和本群的人打过对局，"
                       "就查不到——请发完整 <code>名字#编号</code>（游戏内个人资料页可以看到）"],
                      kind="warn", fallback=f"没找到玩家 {q}，请带 #编号")
        return None
    if len(uniq) == 1:
        (name, code), = uniq
        return f"{name}#{code}"
    top = list(uniq)[:12]
    rows = "".join(f"<code>{_html.escape(n)}#{c}</code>　" for n, c in top)
    lines = [f"找到 <b>{len(uniq)}</b> 个同名玩家，发指令时带上 <code>#编号</code>：",
             rows]
    await _notice(matcher, event, "有重名玩家", lines,
                  kind="warn", fallback=f"有 {len(uniq)} 个同名玩家，请带上 #编号："
                  + "、".join(f"{n}#{c}" for n, c in top))
    return None


# 生涯武器的统计范围写在参数末尾：s27 / 赛季27 / 全生涯（不写＝全生涯）
_SCOPE_TOKEN = re.compile(r"^(?:s|赛季)\s*(\d{1,2})$", re.I)


def _split_scope(text: str) -> tuple[str, str]:
    """'Wj#8984 s27' → ('Wj#8984', 's27')；没写范围时 scope 返回空串（交给默认值）"""
    parts = text.split()
    if not parts:
        return text, ""
    last = parts[-1]
    if last in ("全生涯", "all"):
        return " ".join(parts[:-1]), "all"
    m = _SCOPE_TOKEN.match(last)
    if m:
        return " ".join(parts[:-1]), f"s{m.group(1)}"
    return text, ""


msg_logger = on_type(MessageEvent, priority=1, block=False)


@msg_logger.handle()
async def _log_incoming(event: MessageEvent):
    """记录指令消息与私聊消息（含被群开关挡下的，便于排查“为什么没回”）"""
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    if not text:
        kinds = "/".join(sorted({seg.type for seg in msg})) or "空"
        text = f"[{kinds}消息]"
    gid = getattr(event, "group_id", None) or ""
    if not _is_cmd(text) and gid:
        return  # 群里非指令消息不记，避免刷屏
    bot_log.add("in", text=text, group_id=gid,
                user_id=str(event.user_id), nickname=_nickname(event),
                extra={"enabled": _allowed_group(event)})


base_query = on_command("玩家", aliases={"d2"}, priority=10, block=True,
                        force_whitespace=True)
career_query = on_command("生涯", aliases={"周报", "d2周报"}, priority=9, block=True,
                          force_whitespace=True)
bind_query = on_command("绑定", aliases={"bind"}, priority=7, block=True,
                        force_whitespace=True)
unbind_query = on_command("解绑", aliases={"unbind"}, priority=7, block=True,
                          force_whitespace=True)
mine_query = on_command("我的", aliases={"账号", "me"}, priority=7, block=True,
                        force_whitespace=True)


@bind_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = args.extract_plain_text().strip()
    if not name:
        await _notice(bind_query, event, "绑定账号",
                      ["用法：<code>/绑定 玩家名#1234</code>（例如 <code>/绑定 小日向#21662</code>）"],
                      fallback="用法：/绑定 玩家名#1234")
    member = await d2.resolve_member(name)
    if not member:
        await _notice(bind_query, event, "没找到玩家", [f"确认名字和 <code>#编号</code> 后重试：{name}"],
                      kind="warn", fallback=f"没找到玩家 {name}")
    canonical = f"{member['display']}#{d2.fmt_code(member['code'])}"
    d = _load_bindings()
    d[_uid(event)] = canonical
    _save_bindings(d)
    await _notice(bind_query, event, "绑定成功", [f"已绑定 <b>{canonical}</b>",
                 "以后直接发 <code>/生涯</code>、<code>/玩家</code> 即可，不用再带名字"],
                  kind="ok", fallback=f"绑定成功：{canonical}")


@unbind_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    d = _load_bindings()
    old = d.pop(_uid(event), None)
    _save_bindings(d)
    if old:
        await _notice(unbind_query, event, "已解绑", [f"解除了 <b>{old}</b>"], kind="ok",
                      fallback=f"已解绑 {old}")
    await _notice(unbind_query, event, "还没绑定", ["发 <code>/绑定 玩家名#1234</code> 即可绑定"],
                  kind="warn", fallback="你还没有绑定账号")


@mine_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    b = _bound_name(event)
    if b:
        await _notice(mine_query, event, "当前绑定", [f"<b>{b}</b>"], kind="ok",
                      fallback=f"当前绑定：{b}")
    await _notice(mine_query, event, "还没绑定", ["发 <code>/绑定 玩家名#1234</code> 即可绑定"],
                  kind="warn", fallback="还没有绑定账号")


@base_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _player_arg(base_query, event, args)
    if not name:
        return
    try:
        data = await d2.full_report(name)
    except LookupError:
        await _notice(base_query, event, "没找到玩家", [f"确认名字和 <code>#编号</code> 后重试：{name}"],
                      kind="warn", fallback=f"没找到玩家 {name}")
        return
    await _send_card(base_query, event, bot_cards.player_card(data),
                     f"玩家卡片 {data['display']}", f"【{data['display']}】的最高光能：{data['max_light']}")


@career_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _player_arg(career_query, event, args)
    if not name:
        return
    try:
        data = await d2.full_report(name)
    except LookupError:
        await _notice(career_query, event, "没找到玩家", [f"确认名字和 <code>#编号</code> 后重试：{name}"],
                      kind="warn", fallback=f"没找到玩家 {name}")
        return
    await _send_card(career_query, event, bot_cards.career_card(data),
                     f"生涯卡片 {data['display']}", f"【{data['display']}】的生涯统计")


weapon_query = on_command("武器查询", aliases={"d2武器"}, priority=8, block=True,
                          force_whitespace=True)
perk_query = on_command("perk查询", aliases={"特性查询", "d2perk", "d2特性"}, priority=8,
                        block=True, force_whitespace=True)
armor_query = on_command("护甲套装", aliases={"套装效果", "d2套装", "套装"}, priority=8,
                         block=True, force_whitespace=True)
dust_query = on_command("每日光尘", aliases={"光尘商店", "d2光尘", "eververse", "光尘"},
                        priority=8, block=True, force_whitespace=True)
rot_query = on_command("轮换", aliases={"本周轮换", "d2轮换", "突袭轮换", "raid轮换"},
                       priority=8, block=True, force_whitespace=True)
filter_query = on_command("武器筛选", aliases={"d2武器筛选", "d2筛选", "筛选武器"},
                          priority=8, block=True, force_whitespace=True)


def _split_ver(q: str) -> tuple[str, int | None]:
    """拆结尾版本序号 = 同名多版本的版本号（1=最旧）：「暗夜魅影 2」→ ("暗夜魅影", 2)"""
    m = re.search(r"\s+(\d+)$", q)
    if m:
        return q[:m.start()].strip(), int(m.group(1))
    return q, None


async def _weapon_reply(matcher, event: Event, q: str, ver_num: int | None = None,
                        source: str = "武器查询", res: list[dict] | None = None):
    """武器查询主体（/武器查询 与 @机器人 直查共用）：没找到/序号超范围时
    _notice 会 finish 结束；命中够准（唯一/前缀）出详情卡，否则给候选列表；
    同名多版本按结尾序号选，不带序号默认最新版本。"""
    if res is None:
        res = d2.search_weapons_full(q, 12)
    if not res:
        await _notice(matcher, event, "没找到武器", [f"没有匹配「{q}」的武器"],
                      kind="warn", fallback=f"没找到武器「{q}」")
    top = res[0]
    # 同名多版本：按赛季从旧到新排，默认取最新；带序号取对应版本
    vers = d2.weapon_versions_by_name(top["name"])
    ver_tags, ver_cur, ver_names = [], 0, []
    if len(vers) > 1:
        if ver_num is not None:
            if not 1 <= ver_num <= len(vers):
                rng = f"1~{len(vers)}"
                await _notice(matcher, event, "版本序号超出范围",
                              [f"「{top['name']}」共 {len(vers)} 个版本，序号范围 {rng}",
                               "版本 1 最旧，序号越大越新"],
                              kind="warn", fallback=f"版本序号 {ver_num} 超出范围 {rng}")
            top = dict(d2.weapon_detail(vers[ver_num - 1]["hash"]), hash=vers[ver_num - 1]["hash"])
            ver_cur = ver_num
        else:
            ver_cur = len(vers)
            top = dict(d2.weapon_detail(vers[-1]["hash"]), hash=vers[-1]["hash"])
        ver_tags = [d2.season_tag(v["season"]) + ("·活动" if v["event"] else "") for v in vers]
        ver_names = [d2.season_name(v["season"]) for v in vers]
    strong = len(res) == 1 or top["name"].lower().startswith(q.lower()) or top["name"].lower() == q.lower()
    if strong:
        others = [w["name"] for w in res[1:4]]
        usage = None
        if weapon_usage is not None:
            try:
                usage = await weapon_usage.get_usage(int(top["hash"]))
            except Exception:  # noqa: BLE001
                usage = None
        html = bot_cards.weapon_card(top, others, ver_tags, ver_cur, ver_names, usage=usage)
        label = f"武器卡片 {top['name']}"
    else:
        html = bot_cards.weapons_list_card(res, q)
        label = f"武器候选列表 {q}"
    await _send_card(matcher, event, html, label, f"{source}：{q}")


async def _perk_reply(matcher, event: Event, q: str):
    """perk 查询主体（/perk查询 与 @bot perk xxx 显式前缀共用）"""
    res = d2.search_perks(q, 3)
    if not res:
        await _notice(matcher, event, "没找到 perk", [f"没有匹配「{q}」的 perk"],
                      kind="warn", fallback=f"没找到 perk「{q}」")
    await _send_card(matcher, event, bot_cards.perk_card(res, q),
                     f"Perk 卡片 {q}", f"perk 查询：{q}")


@weapon_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    q = args.extract_plain_text().strip()
    if not q:
        await _notice(weapon_query, event, "武器查询",
                      ["用法：<code>/武器查询 武器名</code>（例如 <code>/武器查询 秋风</code>）",
                       "同名武器多版本：<code>/武器查询 暗夜魅影 2</code> 查第 2 版（不带序号默认最新）"],
                      fallback="用法：/武器查询 武器名")
    name, ver_num = _split_ver(q)
    await _weapon_reply(weapon_query, event, name, ver_num)


FILTER_HINT = [
    "用法：<code>/武器筛选 关键词…</code>，空格分隔，多个词需<em>同时满足</em>",
    "例：<code>/武器筛选 主手 锻造 微冲 900</code>、<code>/武器筛选 电 重弹 腹背受敌</code>",
    "类型 手炮/微冲/喷子/机枪/榴弹/弓/偃月/线性…（AR SG SMG LFR 这类缩写也认）",
    "弹药 主手·白弹 / 副手·绿弹 / 重弹·紫弹｜槽位 动能 / 能量 / 威能",
    "元素 电/火/冰/虚空/缚丝｜射速 140/900…｜框架 速射/波形/适配/精密/轻质/高冲…",
    "特性 爆破专家/雪上加霜/事不过四…｜<code>锻造</code> 可锻造｜<code>异域</code> 金枪",
]


@filter_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    q = args.extract_plain_text().strip()
    if not q:
        await _notice(filter_query, event, "武器筛选", FILTER_HINT,
                      fallback="用法：/武器筛选 关键词（空格分隔）")
    res = wf.filter_weapons(q)
    if not res["items"]:
        await _notice(filter_query, event, "没有匹配的武器",
                      [f"关键词「{q}」没有命中任何武器，换个词或减少条件试试"] + FILTER_HINT,
                      kind="warn", fallback=f"没有匹配「{q}」的武器")
    await _send_card(filter_query, event, bot_cards.weapon_filter_card(res, q),
                     f"武器筛选 {q}", f"武器筛选：{q}（{res['total']} 把）")


@perk_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    q = args.extract_plain_text().strip()
    if not q:
        await _notice(perk_query, event, "Perk 查询",
                      ["用法：<code>/perk查询 perk名</code>（例如 <code>/perk查询 热力四射</code>）"],
                      fallback="用法：/perk查询 perk名")
    await _perk_reply(perk_query, event, q)


@armor_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    q = args.extract_plain_text().strip()
    if not q:  # 不带名字 → 全部套装索引（56 套全文一张图放不下，列 2/4 件效果名）
        await _send_card(armor_query, event, bot_cards.armor_sets_card(d2.all_armor_sets()),
                         "护甲套装一览",
                         "护甲套装效果一览（发 /护甲套装 套装名 看单套完整数值）")
        return
    res = d2.search_armor_sets(q)
    if not res:
        await _notice(armor_query, event, "没找到套装",
                      [f"没有匹配「{q}」的套装；可以用别名，如 <code>一愿</code>、<code>vog</code>、<code>kf</code>"],
                      kind="warn", fallback=f"没找到套装「{q}」")
        return
    if len(res) == 1:
        await _send_card(armor_query, event, bot_cards.armor_set_card(res[0]),
                         f"套装卡片 {res[0]['name']}", f"护甲套装：{res[0]['name']}")
        return
    # 多个候选 → 先给索引（含各候选 2/4 件效果名），让用户再挑一个
    await _send_card(armor_query, event, bot_cards.armor_sets_card(res, q),
                     f"套装候选 {q}", f"护甲套装「{q}」命中 {len(res)} 套")


# ---------- 异域护甲查询（数据：manifest_index/exotic_armor.json，懒加载） ----------
# 索引由独立数据脚本生成，机器人侧只读：文件缺失/损坏一律当「未构建」提示，
# 不抛异常不拖挂其它指令。命中规则：名字/英文名/别名精确命中出详情卡；
# 只有模糊命中且不止一件时出候选列表卡。
armor_lookup = on_command("护甲查询", aliases={"d2护甲"}, priority=8, block=True,
                          force_whitespace=True)

_ARMOR_CACHE: list[dict] | None = None
_ARMOR_TRIED = False


def _armor_data() -> list[dict] | None:
    """读异域护甲索引（只加载一次；None = 文件缺失或损坏）"""
    global _ARMOR_CACHE, _ARMOR_TRIED
    if not _ARMOR_TRIED:
        _ARMOR_TRIED = True
        try:
            # d2._idx_file 兼容源码目录 / PyInstaller 打包资源 / 当前目录三种定位
            data = json.load(open(d2._idx_file("exotic_armor.json"), encoding="utf-8"))
            items = [it for it in (data.get("items") or []) if isinstance(it, dict)]
            for it in items:  # 构建时间戳下放到条目，卡片页脚可显示
                it.setdefault("updated", data.get("updated") or "")
            _ARMOR_CACHE = items or None
        except Exception:  # noqa: BLE001  缺文件 / JSON 损坏统一当「未构建」
            _ARMOR_CACHE = None
    return _ARMOR_CACHE


def _armor_match(items: list[dict], q: str) -> tuple[list[dict], list[dict]]:
    """按 名字/英文名/别名 匹配异域护甲：返回 (精确命中, 模糊命中)"""
    q_low = (q or "").strip().lower()
    exact, fuzzy, seen = [], [], set()
    for it in items:
        names = {str(it.get("name") or ""), str(it.get("en") or "")}
        names |= {str(a) for a in (it.get("aliases") or [])}
        names = {n.strip().lower() for n in names if n.strip()}
        if q_low in names:
            exact.append(it)
        elif any(q_low in n or n in q_low for n in names):
            h = it.get("hash")
            if h in seen:  # 同一件护甲多个名字都命中时只留一条
                continue
            seen.add(h)
            fuzzy.append(it)
    return exact, fuzzy


async def _armor_reply(matcher, event: Event, q: str, source: str = "护甲查询"):
    """异域护甲查询主体（/护甲查询 与 @机器人 直查共用）"""
    items = _armor_data()
    if items is None:
        await _notice(matcher, event, "护甲数据未构建",
                      ["异域护甲索引还没生成（manifest_index/exotic_armor.json）",
                       "先在机器人目录跑一次数据构建，再来查"],
                      kind="warn", fallback="护甲数据未构建：manifest_index/exotic_armor.json 缺失")
        return
    exact, fuzzy = _armor_match(items, q)
    hits = exact or fuzzy
    if len(hits) == 1:
        top = hits[0]
        await _send_card(matcher, event, bot_cards.armor_card(top),
                         f"护甲卡片 {top.get('name') or q}", f"{source}：{q}")
        return
    if hits:
        await _send_card(matcher, event, bot_cards.armor_card(q, hits),
                         f"护甲候选 {q}", f"{source}「{q}」命中 {len(hits)} 件")
        return
    await _notice(matcher, event, "没找到护甲",
                  [f"没有匹配「{q}」的异域护甲",
                   "用法：<code>/护甲查询 护甲名</code>，支持外号/英文名"
                   "（如 <code>/护甲查询 星夜鹰</code>）"],
                  kind="warn", fallback=f"没找到护甲「{q}」")


@armor_lookup.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    q = args.extract_plain_text().strip()
    if not q:
        await _notice(armor_lookup, event, "护甲查询",
                      ["用法：<code>/护甲查询 护甲名</code>（例如 <code>/护甲查询 星夜鹰</code>）",
                       "支持外号与英文名：<code>/护甲查询 金头鹰</code>、<code>/护甲查询 celestine</code>"],
                      fallback="用法：/护甲查询 护甲名")
        return
    await _armor_reply(armor_lookup, event, q)


@dust_query.handle()
async def _(event: Event):
    if not _allowed_group(event):
        return
    try:
        store = await d2.eververse_store()
    except d2.BungieAuthRequired:
        await _notice(dust_query, event, "光尘商店需要先授权 Bungie 账号",
                      ["光尘商店是「登录后才能读」的接口。",
                       "请到 Bot 面板（<code>http://127.0.0.1:8900/panel</code>）点 "
                       "<b>授权 Bungie 账号</b>，登录一次即可。"],
                      kind="warn", fallback="光尘商店需要先在 Bot 面板授权 Bungie 账号")
    except Exception as exc:  # noqa: BLE001
        await _notice(dust_query, event, "光尘商店获取失败",
                      [f"Bungie 接口暂时不可用：{_exc_msg(exc)}"],
                      kind="err", fallback=f"光尘商店获取失败：{_exc_msg(exc)}")
    await _send_card(dust_query, event, bot_cards.eververse_card(store),
                     "光尘商店", "光尘商店数据获取失败")


@rot_query.handle()
async def _(event: Event):
    if not _allowed_group(event):
        return
    try:
        rot = await d2.rotation_week()
    except Exception as exc:  # noqa: BLE001
        await _notice(rot_query, event, "本周轮换获取失败",
                      [f"Bungie 里程碑接口暂时不可用：{_exc_msg(exc)}"],
                      kind="err", fallback=f"本周轮换获取失败：{_exc_msg(exc)}")
    await _send_card(rot_query, event, bot_cards.rotation_card(rot, d2.distortion_now()),
                     "本周轮换", "本周轮换数据获取失败")


# ---------- 掉落表：/掉落 克洛塔、/ron掉落、/ce掉落 …（Saya 掉落图） ----------
drop_query = on_command("掉落", aliases={"d2掉落", "掉落表", "loot", *raid_loot.command_names()},
                        priority=8, block=True, force_whitespace=True)


@drop_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    # 组合词（/ron掉落）时 CommandArg 为空，从整条消息里剥出关键词
    token = args.extract_plain_text().strip() or \
        str(event.get_message()).lstrip("/").strip()
    if not raid_loot.resolve(token) and not raid_loot.missing_name(token):
        token = re.sub(r"掉落", "", token).strip()
    def _chart_list() -> list[str]:
        """可查副本列表：名字 + 常用触发词"""
        out = []
        for k, (name, aliases, _) in raid_loot.CHARTS.items():
            short = [a for a in aliases if len(a) <= 4][:3]
            out.append(f"<b>{name}</b>：<code>/{k}掉落</code> 或 <code>/{'/'.join(short)}掉落</code>")
        return out

    missing = raid_loot.missing_name(token)
    if missing:
        await _notice(drop_query, event, f"{missing}暂无掉落图",
                      [f"Sayalarry 还没做过「{missing}」的掉落表图，当前可查："] + _chart_list(),
                      kind="warn", fallback=f"{missing}暂无掉落图")
    key = raid_loot.resolve(token)
    if not key:
        await _notice(drop_query, event, "掉落表查询",
                      ["用法：<code>/掉落 副本名</code>，也支持 <code>/ce掉落</code>、<code>/ron掉落</code> 这类缩写。",
                       "当前可查的副本："] + _chart_list(),
                      fallback="用法：/掉落 副本名（如 /掉落 克洛塔）")
    files = raid_loot.segments(key)
    if not files:
        await _notice(drop_query, event, "掉落图缺失",
                      [f"「{raid_loot.chart_display(key)}」的图片没有随包分发，检查 raid_images_proc 目录。"],
                      kind="err", fallback="掉落图缺失")
    _log_out(event, f"[图片] 掉落表 {raid_loot.chart_display(key)} ×{len(files)}")
    segs = [MessageSegment.image("base64://" + base64.b64encode(open(f, "rb").read()).decode())
            for f in files]
    try:
        await drop_query.finish(_at_sender(event, Message(segs)))
    except FinishedException:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_out(event, f"[发送失败] 掉落表 {key}：{exc}")
        raise


# ---------- 群里 @机器人 + 名字 = 直接查武器 / perk（小日向式） ----------
# 小日向不用打指令，@ 一下接名字就出卡片；这里对齐：@机器人 秋风 → 武器卡，
# @机器人 热力四射 → perk 卡。**只认真 @**：仅仅"引用/回复机器人的消息"不算。
async def _at_me_only(event: Event) -> bool:
    """直查的触发条件：被 @（nonebot 的 to_me）且是群聊。

    注意 nonebot 的 to_me 有三条来源，其中两条会误伤：
      - 「引用/回复机器人的消息」→ 适配器 _check_reply 会把 to_me 置真（没 @ 也会真）；
      - 私聊 → 恒为真。
    这里把"只是引用了机器人的消息"排除掉，只留真正的 @；私聊也不做直查。
    """
    if not getattr(event, "to_me", False):
        return False
    if getattr(event, "group_id", None) is None:
        return False
    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None) if reply is not None else None
    if sender is not None and str(getattr(sender, "user_id", "")) == str(getattr(event, "self_id", "")):
        return False  # 只是引用机器人自己的消息，没有 @
    return True


at_lookup = on_message(rule=Rule(_at_me_only), priority=12, block=False)


# 显式路由前缀：@bot 后先认指令词（顺带吃掉不带斜杠的指令别名，如「@bot d2武器 秋风」）
_AT_PERK_PREFIX = re.compile(r"^(?:d2)?(?:perk查询|特性查询|perk|特性)[\s:：]+(.+?)\s*$", re.I)
_AT_WEAPON_PREFIX = re.compile(r"^(?:武器查询|d2武器)[\s:：]+(.+?)\s*$")
_AT_ARMOR_PREFIX = re.compile(r"^(?:护甲查询|d2护甲|护甲(?!套装))[\s:：]+(.+?)\s*$")


@at_lookup.handle()
async def _(event: MessageEvent):
    if not _allowed_group(event):
        return
    # 剥 at 段与空白：开头的 @我 适配器预处理时已摘掉，这里兜底再清（含零宽空格）
    q = event.get_message().extract_plain_text().strip().strip("\u200b").strip()
    if not q:  # 纯 @ / 空文本：不响应
        return
    m = _AT_PERK_PREFIX.match(q)  # 显式 perk 前缀 → 强制走 perk 查询
    if m:
        name = m.group(1)
        if name and not name.startswith("/"):
            await _perk_reply(at_lookup, event, name)
        return
    m = _AT_WEAPON_PREFIX.match(q)  # 显式武器前缀 → 强制走武器查询
    if m:
        name, ver_num = _split_ver(m.group(1))
        if name and not name.startswith("/"):
            await _weapon_reply(at_lookup, event, name, ver_num)
        return
    m = _AT_ARMOR_PREFIX.match(q)  # 显式护甲前缀 → 强制走护甲查询（不吃「护甲套装 …」）
    if m:
        name = m.group(1)
        if name and not name.startswith("/"):
            await _armor_reply(at_lookup, event, name, source="护甲")
        return
    if q.startswith(("/", "／")) or re.match(r"(?i)^d2", q):
        # 以 / 或 d2 开头 = 指令语义，交给 on_command（priority 8 命中即 block），不重复响应
        return
    # 自由文本：先武器匹配；武器无命中再查护甲；护甲也无命中才落 perk
    name, ver_num = _split_ver(q)
    res = d2.search_weapons_full(name, 12)
    if res:
        await _weapon_reply(at_lookup, event, name, ver_num, source="武器", res=res)
        return
    arm = _armor_data()
    if arm is not None:
        exact, fuzzy = _armor_match(arm, name)
        if exact or fuzzy:
            await _armor_reply(at_lookup, event, name, source="护甲")
            return
    perks = d2.search_perks(name, 3)
    if perks:
        await _send_card(at_lookup, event, bot_cards.perk_card(perks, name),
                         f"Perk 卡片 {name}", f"perk：{name}")
        return
    await _notice(at_lookup, event, "没找到",
                  [f"没有匹配「{name}」的武器、护甲或 perk",
                   "可以发 <code>/武器查询 名称</code>、<code>/护甲查询 名称</code>"
                   " 或 <code>/perk查询 名称</code>"],
                  kind="warn", fallback=f"没找到「{name}」的武器或 perk")


# ---------- 战绩类指令（对齐小日向：/raid、/地牢、/pvp、/pve、/智谋 …） ----------
# 卡片直接复用查询站 webui.py 的排版（bot_cards 里做了委托），
# 保证 QQ 里出的图和网页端同一套视觉；数据全部来自 destiny_data。

raid_query = on_command("raid", aliases={"突袭", "突袭战绩", "d2raid"}, priority=8,
                        block=True, force_whitespace=True)
dungeon_query = on_command("地牢", aliases={"dungeon", "d2地牢"}, priority=8, block=True,
                           force_whitespace=True)
pvp_query = on_command("pvp", aliases={"熔炉", "d2pvp"}, priority=8, block=True,
                       force_whitespace=True)
pve_query = on_command("pve", aliases={"d2pve"}, priority=8, block=True,
                       force_whitespace=True)
gambit_query = on_command("智谋", aliases={"gambit", "d2智谋"}, priority=8, block=True,
                          force_whitespace=True)
history_query = on_command("历史", aliases={"战绩", "最近对局", "d2历史"}, priority=8,
                           block=True, force_whitespace=True)
heat_query = on_command("热力图", aliases={"活跃", "d2热力图"}, priority=8, block=True,
                        force_whitespace=True)
forge_query = on_command("锻造", aliases={"图案", "锻造图案", "d2锻造"}, priority=8,
                         block=True, force_whitespace=True)
title_query = on_command("称号", aliases={"d2称号"}, priority=8, block=True,
                         force_whitespace=True)
wpvp_query = on_command("常用武器", aliases={"武器统计", "mvp", "d2武器统计",
                                          "生涯武器", "pvp生涯武器", "pvp武器"},
                        priority=8, block=True, force_whitespace=True)
# PVE 场次远多于 PVP，所以默认只统计当前赛季（写 全生涯 才跑全量）
wpve_query = on_command("pve生涯武器", aliases={"pve武器", "pve常用武器"},
                        priority=8, block=True, force_whitespace=True)
gm_query = on_command("宗师", aliases={"宗师战绩", "征服", "gm战绩"}, priority=8, block=True,
                      force_whitespace=True)
help_query = on_command("帮助", aliases={"help", "菜单", "指令"}, priority=6, block=True,
                        force_whitespace=True)


async def _need_player(matcher, event: Event, args: Message) -> str | None:
    return await _player_arg(matcher, event, args)


async def _not_found(matcher, event: Event, name: str):
    await _notice(matcher, event, "没找到玩家",
                  [f"确认名字和 <code>#编号</code> 后重试：{name}"],
                  kind="warn", fallback=f"没找到玩家 {name}")


async def _working(matcher, event: Event, title: str, lines: list[str]):
    """长任务先回一张「统计中」卡片（热力图/常用武器要翻几百页对局）"""
    try:
        png = await card_render.html_to_png(bot_cards.notice(title, lines))
    except Exception:  # noqa: BLE001  渲染器起不来就退回纯文本，别把这一步变成阻塞
        await matcher.send(_at_sender(event, MessageSegment.text(f"{title}\n" + "\n".join(lines))))
        return
    _log_out(event, f"[图片] {title}")
    await matcher.send(_at_sender(event, MessageSegment.image(
        "base64://" + base64.b64encode(png).decode())))


async def _wait_job(jid: str, timeout: float = 1200.0) -> dict:
    """等后台任务跑完（destiny_data.JOBS）；超时返回当前状态

    生涯任务现在排队串行（避免同时跑被 Bungie 限流拖慢），排在后面的要等前面的跑完，
    所以给足 20 分钟；排到第几位会先在「统计中」卡片里告诉用户。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = d2.JOBS.get(jid) or {}
        if j.get("status") in ("done", "error"):
            return j
        await asyncio.sleep(2)
    return d2.JOBS.get(jid) or {}


def _queue_line(jid: str) -> list[str]:
    """任务状态提示。返回若干行：命中去重/缓存时不说「要翻几百页」，免得用户以为又在全量重跑"""
    j = d2.JOBS.get(jid) or {}
    if j.get("status") == "done":
        if j.get("cached") and not j.get("reused"):
            # 热力图缓存直出：他确实没打新的，再发多少次都是这份，别承诺「过会儿能强制重跑」
            return ["上号时间没超过已统计到的那场，没有新数据，直接给你上次的结果"]
        return ["这份数据刚跑过，直接给你上次的结果（"
                f"{d2.job_reuse_window() // 60} 分钟后再发可以强制重跑）"]
    if d2.job_shared(jid):  # 复用了别人正在跑的那个任务
        return ["这份数据已经在统计了，跑完直接出图，不用重复排队"]
    pos = d2.queue_position(jid)
    if not pos:
        return ["生涯任务一个个跑（避免同时拉 PGCR 被 Bungie 限流拖慢），现在正在统计…"]
    return [f"前面还有 <b>{pos}</b> 位在统计，已排队；生涯任务一个个跑反而更快，跑完会自动出图"]


async def _jobs_card(matcher, event: Event, jid: str, title: str, card_fn, label: str):
    """后台任务 → 等结果 → 出卡片（失败给提示卡）"""
    j = await _wait_job(jid)
    if j.get("status") != "done":
        await _notice(matcher, event, f"{title}失败",
                      ["统计没跑完（接口超时或网络中断），稍后再试"],
                      kind="warn", fallback=f"{title}失败")
        return
    await _send_card(matcher, event, card_fn(j["result"]), label, title)


async def _mode_cmd(matcher, event: Event, args: Message, mode: int, title: str,
                    life_group: str = "", life_extra: tuple = ()):
    name = await _need_player(matcher, event, args)
    if not name:
        return
    try:
        rep = await d2.mode_report(name, mode)
    except LookupError:
        await _not_found(matcher, event, name)
        return
    life = None
    if life_group:
        try:
            life = await d2.lifetime_stats(name, life_group)
        except Exception:  # noqa: BLE001  生涯聚合失败不影响近期战绩
            life = None
    await _send_card(matcher, event, bot_cards.mode_card(rep, title, str(mode), life, life_extra),
                     f"{title} {rep['display']}", f"{rep['display']} · {title}")


async def _raid_cmd(matcher, event: Event, args: Message, mode: int, title: str):
    name = await _need_player(matcher, event, args)
    if not name:
        return
    try:
        data = await d2.raid_report(name, mode)
    except LookupError:
        await _not_found(matcher, event, name)
        return
    await _send_card(matcher, event, bot_cards.raid_card(data, title, name, mode),
                     f"{title} {data['display']}", f"{data['display']} · {title}")


@raid_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _raid_cmd(raid_query, event, args, 4, "Raid 突袭战绩")


@dungeon_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _raid_cmd(dungeon_query, event, args, 82, "地牢战绩")


@pvp_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _mode_cmd(pvp_query, event, args, 5, "PVP 熔炉竞技场战绩", "allPvP",
                    ("activitiesWon", "assists", "precisionKills"))


@pve_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _mode_cmd(pve_query, event, args, 7, "PVE 战绩", "allPvE", ("precisionKills",))


@gambit_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _mode_cmd(gambit_query, event, args, 63, "智谋战绩")


@history_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _need_player(history_query, event, args)
    if not name:
        return
    try:
        data = await d2.history_report(name)
    except LookupError:
        await _not_found(history_query, event, name)
        return
    await _send_card(history_query, event, bot_cards.history_card(data),
                     f"最近对局 {data['display']}", f"{data['display']} · 最近对局")


@heat_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _need_player(heat_query, event, args)
    if not name:
        return
    jid = await d2.start_heatmap(name, who=_who(event))
    if not jid:
        await _not_found(heat_query, event, name)
    await _working(heat_query, event, "正在统计活跃度",
                   [*_queue_line(jid),
                    "要翻几百页对局历史（每角色上限 60 页 × 250 场），请稍候…"])
    await _jobs_card(heat_query, event, jid, "热力图", bot_cards.heat_card,
                     f"热力图 {name}")


@forge_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _need_player(forge_query, event, args)
    if not name:
        return
    try:
        data = await d2.node_report(name, "patterns")
    except LookupError:
        await _not_found(forge_query, event, name)
        return
    await _send_card(forge_query, event, bot_cards.nodes_card(data),
                     f"锻造图案 {data['display']}",
                     f"{data['display']} · 锻造 {data['done']}/{data['total']}")


@title_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    name = await _need_player(title_query, event, args)
    if not name:
        return
    try:
        data = await d2.node_report(name, "titles")
    except LookupError:
        await _not_found(title_query, event, name)
        return
    await _send_card(title_query, event, bot_cards.nodes_card(data),
                     f"称号 {data['display']}",
                     f"{data['display']} · 称号 {data['done']}/{data['total']}")


@wpvp_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    raw, scope = _split_scope(args.extract_plain_text().strip())
    name = await _resolve_name(wpvp_query, event, raw, args)
    if not name:
        return
    jid = await d2.start_pvp_weapons(name, scope or "all", who=_who(event))
    if not jid:
        await _not_found(wpvp_query, event, name)
        return
    _, _, label = d2.scope_window(scope or "all")
    await _working(wpvp_query, event, "正在统计常用武器",
                   [*_queue_line(jid),
                    f"范围：<b>{label}</b>；要逐场拉对局详情统计武器击杀与爆头率，请稍候…",
                    "首次查全生涯要跑一会儿，跑过的场次会缓存，之后切赛季很快"])
    await _jobs_card(wpvp_query, event, jid, "常用武器", bot_cards.wpvp_card,
                     f"常用武器 {name}")


@wpve_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    raw, scope = _split_scope(args.extract_plain_text().strip())
    name = await _resolve_name(wpve_query, event, raw, args)
    if not name:
        return
    jid = await d2.start_pve_weapons(name, scope or "current", who=_who(event))
    if not jid:
        await _not_found(wpve_query, event, name)
        return
    _, _, label = d2.scope_window(scope or "current")
    await _working(wpve_query, event, "正在统计 PVE 武器使用",
                   [*_queue_line(jid),
                    f"范围：<b>{label}</b>；要逐场拉对局详情统计武器击杀与爆头率，请稍候…",
                    "默认只统计当前赛季；想查别的赛季在末尾加 <code>s27</code> / <code>赛季27</code>",
                    "全生涯 PVE 场次极多，要跑很久，写 <code>全生涯</code> 才会跑"])
    await _jobs_card(wpve_query, event, jid, "PVE武器使用", bot_cards.wpve_card,
                     f"PVE武器使用 {name}")


@gm_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    raw, scope = _split_scope(args.extract_plain_text().strip())
    name = await _resolve_name(gm_query, event, raw, args)
    if not name:
        return
    jid = await d2.start_gm_report(name, scope or "current", who=_who(event))
    if not jid:
        await _not_found(gm_query, event, name)
        return
    _, _, label = d2.scope_window(scope or "current")
    await _working(gm_query, event, "正在统计宗师战绩",
                   [*_queue_line(jid),
                    f"范围：<b>{label}</b>；统计宗师征服与宗师警戒的通关率 / 最快 / 平均，请稍候…",
                    "默认只统计当前赛季；想查别的赛季在末尾加 <code>s27</code> / <code>赛季27</code>"])
    await _jobs_card(gm_query, event, jid, "宗师战绩", bot_cards.gm_card,
                     f"宗师战绩 {name}")


HELP_PLAIN = ("指令一览：/玩家 /生涯 /raid /地牢 /pvp /pve /智谋 /历史 /热力图 /称号 /锻造 "
              "/生涯武器 /pve生涯武器 /宗师 /武器查询 /perk查询 /护甲查询 /护甲套装 /每日光尘 /轮换 /绑定 /我的 /解绑")


@help_query.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _allowed_group(event):
        return
    await _send_card(help_query, event, bot_cards.help_card(),
                     "指令一览", HELP_PLAIN)
