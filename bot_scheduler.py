"""后台定时调度：每日预取 + Bungie token 保活 + 新轮换检测推送。

为什么不用 APScheduler：这里只有三类节奏（每 30 分钟 tick、按日缓存键、按周
缓存键），一个 sleep 循环就够，少一个依赖。协程全部投递到 nonebot 驱动的事件
循环上跑（destiny_data / card_render 的客户端和浏览器实例都是按事件循环各持
一份的，跟主查询共用同一条循环最稳）。

轮换推送只走 NapCat 通道：QQ 官方机器人只有 5 分钟被动回复窗口，发不了主动
消息（bot_platform.py 顶部注释）。收件人是 bot_config.json 的 enabled_groups
（NapCat 群号白名单）——它同时是「响应指令」的白名单，空 = 所有群都响应，
但推送没有"所有群"可查，所以**列表为空就不推**。上次推送记在
bot_config.json 的 rot_push_day（ISO 日期，按周三 01:00 的自然周算），
exe 中途重启也能补推本周期还没推过的那一轮。
"""
import asyncio
import datetime
import threading
import time
import traceback
from concurrent import futures

import bot_log
import bot_runtime
import bungie_status as bst

_TZ8 = datetime.timezone(datetime.timedelta(hours=8))     # 游戏复位按北京时间算

TICK_SEC = 30 * 60        # 例行巡检间隔
FIRST_DELAY_SEC = 45      # 启动后先等协议端连上再跑第一轮
TOKEN_REFRESH_AHEAD = 3600   # access_token 还剩不到 1 小时就续
PUSH_GROUP_GAP = 1.5      # 逐群推送的间隔，避免触发频控

_LOOP: dict = {"loop": None}
_WOKEN = threading.Event()
# 维护恢复时戳一下：立刻补跑本轮（日/周缓存刷新、错过的轮换推送），不用等满 30 分钟。
# 「维护恰好压在每天 1 点的光尘刷新上，之后一直没刷出来」就是靠这个补上的。
_CATCHUP = threading.Event()
_STATE: dict = {"sync_day": None}     # 绑定改名同步上次跑的日期（每天一次）


def attach_loop(loop) -> None:
    """把 nonebot 驱动的事件循环交给调度线程（bot_runtime._serve 的钩子里调）"""
    _LOOP["loop"] = loop
    _WOKEN.set()
    # 维护结束 → 立刻补跑（回调可能来自任意线程/事件循环，只置个事件最安全）
    bst.on_clear(_CATCHUP.set)


def bot_loop():
    """nonebot 驱动的事件循环（面板要把「发送失败的图片」补发到群里时用它提交协程）

    返回 None = 协议端还没起来/循环已关，调用方据此提示「等协议端恢复再重发」。
    """
    loop = _LOOP["loop"]
    return loop if loop is not None and not loop.is_closed() else None


# ---------- 周界（轮换每周三凌晨 1 点换） ----------

def rotation_week_key(now: datetime.datetime | None = None) -> str:
    """当前轮换周期的标识：最近一个周三 01:00（北京时间）的日期（ISO）。"""
    now = now or datetime.datetime.now(_TZ8)     # 全盘时钟口径：中国北京时间
    days = (now.weekday() - 2) % 7          # 周三 weekday()==2
    # combine 要带上 tzinfo：now 是 aware 的，naive 的 b 一比大小就报 naive/aware 混用
    b = datetime.datetime.combine(now.date(), datetime.time(1, 0), now.tzinfo) \
        - datetime.timedelta(days=days)
    if now < b:
        b -= datetime.timedelta(days=7)
    return b.date().isoformat()


# ---------- 三类任务（都在驱动循环上执行） ----------

async def _token_job() -> None:
    """token 保活：临期就续。失败打日志——refresh_token 死了得去面板重新授权。"""
    import bungie_auth
    if not bungie_auth.authorized():
        return
    left = float(bungie_auth.status().get("expires_at") or 0) - time.time()
    if left > TOKEN_REFRESH_AHEAD:
        return
    try:
        # 必须带提前量：access_token() 默认把缓存 token 用到最后一刻才刷，
        # 不传参的话这个「临期续期」任务永远只返回旧 token，一次也不会真刷
        await bungie_auth.access_token(refresh_ahead=TOKEN_REFRESH_AHEAD)
        print(f"[sched] Bungie token 已续期（上次剩余 {left/60:.0f} 分钟）")
    except Exception as exc:  # noqa: BLE001
        print(f"[sched] Bungie token 刷新失败（去面板重新授权前先别急）："
              f"{type(exc).__name__}: {exc}")


async def _prefetch_job() -> list:
    """把四类「按日/周换键」的缓存预热一遍：键没换时是纯内存/落盘命中，
    近零成本；换了键（每天 1 点 / 每周三 1 点）就把新一轮数据拉好，
    用户首次查询从十几秒抖动变秒回。"""
    import destiny_data as d2
    results = []
    jobs = [("光尘商店", _ev_safe), ("遗失区域", d2.lost_sectors_today),
            ("轮换", d2.rotation_week), ("宗师", d2.gm_this_week),
            ("老九", _xur_safe)]
    for name, fn in jobs:
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001  预取失败不影响别的，下次 tick 再试
            results.append((name, f"{type(exc).__name__}: {exc}"))
            continue
        results.append((name, ""))
    return results


async def _ev_safe():
    import destiny_data as d2
    try:
        await d2.eververse_store()
    except d2.BungieAuthRequired:
        pass                      # 没授权是常态，等用户在面板授权后自然恢复


async def _xur_safe():
    import destiny_data as d2
    try:
        await d2.xur_stock()
    except d2.BungieAuthRequired:
        pass


async def _push_rotation() -> bool:
    """渲染本周轮换卡并推给 enabled_groups；成功推送（或确定无事可做）返回 True。"""
    import bot_cards
    import card_render
    import destiny_data as d2

    try:
        rot = await d2.rotation_week()
    except Exception as exc:  # noqa: BLE001  数据没到手绝不标记已推，下个 tick 重试
        print(f"[sched] 轮换推送失败（取数据）：{type(exc).__name__}: {exc}")
        return False
    dist, ls, gm = {}, {}, {}
    try:
        dist = await d2.distortion_now()
    except Exception as exc:  # noqa: BLE001  附属板块缺了不挡主卡
        print(f"[sched] 轮换推送：扭曲星球板块缺省 {type(exc).__name__}")
    try:
        ls = await d2.lost_sectors_today()
    except Exception as exc:  # noqa: BLE001
        print(f"[sched] 轮换推送：遗失区域板块缺省 {type(exc).__name__}")
    try:
        gm = await d2.gm_this_week()
    except Exception as exc:  # noqa: BLE001
        print(f"[sched] 轮换推送：宗师板块缺省 {type(exc).__name__}")
    html = bot_cards.rotation_card(rot, dist, ls, gm)
    png = await card_render.html_to_png(html)

    from nonebot.adapters.onebot.v11 import MessageSegment
    # 只要 NapCat（官方通道发不了这儿的主动消息）。判据必须写 adapters.onebot：
    # 单查 "onebot" 会被 "nonebot" 这个子串误命中，官方通道的 bot 也会被算进来，
    # 然后每次推送都在它身上失败一遍（2026-10-08 实测到）
    bots = [b for b in bot_runtime.get_bots().values()
            if "adapters.onebot" in type(b).__module__]
    if not bots:
        print("[sched] 轮换推送：NapCat 协议端未连接，本周期不推（重启后会补推）")
        return False
    groups = bot_runtime.enabled_groups()
    sent = 0
    for bot in bots:
        for gid in groups:
            try:
                await bot.call_api("send_group_msg", group_id=int(gid),
                                   message=MessageSegment.image(png))
                bot_log.add("out", text="[定时推送] 本周轮换", group_id=str(gid))
                sent += 1
            except Exception as exc:  # noqa: BLE001  单群失败不影响其余群
                print(f"[sched] 轮换推送失败（群 {gid}）：{type(exc).__name__}: {exc}")
            await asyncio.sleep(PUSH_GROUP_GAP)
    print(f"[sched] 本周轮换已推送 {sent}/{len(groups)} 群")
    return True


# ---------- 调度线程 ----------

def _submit(coro):
    loop = _LOOP["loop"]
    if loop is None or loop.is_closed():
        return None
    return asyncio.run_coroutine_threadsafe(coro, loop)


def _wait(fut, timeout: int, what: str):
    """等协程结果；超时必须 cancel——否则协程还在事件循环上继续跑，
    下个 tick 再提交一次就出现同一任务两份实例（轮换会向全部群重复推送）。"""
    try:
        return fut.result(timeout=timeout)
    except futures.TimeoutError:
        fut.cancel()   # 取消会传播到 loop 上的 Task
        print(f"[sched] {what}超时（>{timeout}s），已取消")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[sched] {what}异常：{type(exc).__name__}: {exc}")
        return None


async def _binding_sync_job() -> None:
    """绑定改名同步：玩家在棒鸡侧改名后自动更新绑定表（详情见 destiny_data.sync_bindings）"""
    import destiny_data as d2
    r = await d2.sync_bindings()
    for uid, old, new in r["updated"]:
        print(f"[sched] 绑定改名同步：{old} → {new}（uid {uid}）")
    if r["seeded"]:
        print(f"[sched] 绑定 meta 补种子 {r['seeded']} 条（老绑定补存 membershipId）")
    for uid, name in r["stale"]:
        print(f"[sched] 绑定 uid {uid} 的 {name} 在棒鸡侧搜不到（多半已改名），需人工核实新名字")
    # 失败条数必须报出来：以前核对的每一次请求都在抛 JSONDecodeError，这里却照样打
    # 「现名全部一致」，把「所有核对全废」瞒了很久（见 CHANGELOG 2026-10-07 的 /Platform 修复）
    if r["errors"]:
        print(f"[sched] 绑定核对有 {r['errors']} 条失败（原因见上面的 [bind] 行）")
    if not any((r["updated"], r["seeded"], r["stale"], r["errors"])):
        print("[sched] 绑定核对完成：现名全部一致")


def _run() -> None:
    # 第一轮：等协议端连上后跑一次（顺带把「重启后错过的推送」补上）
    _WOKEN.wait(timeout=600)
    time.sleep(FIRST_DELAY_SEC)
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001  调度线程绝不能死
            traceback.print_exc()
        # 维护恢复会戳 _CATCHUP：立刻补跑，别让日/周刷新白等一个 tick
        _CATCHUP.wait(timeout=TICK_SEC)
        _CATCHUP.clear()


def _tick() -> None:
    import bungie_auth
    if bst.is_down():
        # 维护期什么都不用试：token 续期/预取/推送全是必失败的请求，只会刷一屏错误，
        # 维护结束由 bst.on_clear → _CATCHUP 立刻补跑（时间戳见 [维护] 那几行）
        print(f"[sched] Bungie 服务器维护中，本轮预取/推送/续期全部跳过"
              f"（恢复后会自动补跑）")
        return
    fut = _submit(_token_job())
    if fut:
        _wait(fut, 120, "token 任务")

    today = datetime.date.today()
    if _STATE["sync_day"] != today:       # 绑定改名核对：启动后第一轮 + 每天一次
        _STATE["sync_day"] = today
        fut = _submit(_binding_sync_job())
        if fut:
            _wait(fut, 600, "绑定同步任务")

    fut = _submit(_prefetch_job())
    if fut:
        errs = [(n, e) for n, e in (_wait(fut, 600, "预取任务") or []) if e]
        for name, err in errs:
            print(f"[sched] 预取 {name} 失败：{err}")
        if errs:
            # 面板日志里留一行：日/周刷新失败（维护、未授权、接口抖动）以前只打 stdout，
            # 用户能察觉到的只是「光尘商店一整天是空的」「轮换没更新」
            try:
                bot_log.add("out", nickname="定时预取",
                            text="[系统] 本轮预取失败："
                                 + "；".join(f"{n}（{str(e)[:60]}）" for n, e in errs))
            except Exception:  # noqa: BLE001
                pass

    key = rotation_week_key()
    if bot_runtime.load_config().get("rot_push_day") == key:
        return
    groups = bot_runtime.enabled_groups()
    if not groups:
        # 没配推送目标：只把本周期标记掉，避免每次 tick 都白跑一遍渲染
        bot_runtime.update_config(lambda cfg: cfg.__setitem__("rot_push_day", key))
        return
    fut = _submit(_push_rotation())
    if fut and _wait(fut, 300, "轮换推送任务"):
        bot_runtime.update_config(lambda cfg: cfg.__setitem__("rot_push_day", key))


def start() -> None:
    threading.Thread(target=_run, daemon=True, name="bot-scheduler").start()
