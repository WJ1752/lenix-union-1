"""bot_runtime.on_bot_connect 钩子的回归测试：证明「同步写法必炸、async 写法才生效」。

复现 nonebot 的真实调用链（nonebot/internal/driver/abstract.py `_bot_connect`）：
连接钩子是 Dependent，逐个以 bot=/stack=/dependency_cache= 调用；**同步函数**会被
nonebot/dependencies 里的 run_sync 丢进 anyio 工作线程执行 → 那里没有运行中的事件循环，
`asyncio.get_running_loop()` 抛 RuntimeError（exe_stdout.log 里那句
"Error when running WebSocketConnection hook: no running event loop"）。

    .venv/Scripts/python.exe _rtest/test_bot_hook.py
"""
import asyncio
import os
import sys
from contextlib import AsyncExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nonebot

nonebot.init(driver="~fastapi")
import bot_scheduler  # noqa: E402  只用来记录 attach_loop 接到了哪条循环

from nonebot import get_driver  # noqa: E402
from nonebot.internal.driver.abstract import BOT_HOOK_PARAMS  # noqa: E402

OK = FAIL = 0


def check(label, cond, extra=""):
    global OK, FAIL
    OK, FAIL = OK + bool(cond), FAIL + (not bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {label} {extra}")


async def call_hooks():
    """照 nonebot 的调法把注册过的连接钩子跑一遍，返回异常列表"""
    errs = []
    for hook in list(get_driver()._bot_connection_hook):
        try:
            async with AsyncExitStack() as stack:
                await hook(bot=object(), stack=stack, dependency_cache={})
        except Exception as exc:  # noqa: BLE001  真实运行时由 nonebot 的 catch 打成 ERROR 日志
            errs.append(exc)
    return errs


async def main():
    # —— 旧写法：同步函数（修之前的样子）——
    def _old_sync_hook(bot) -> None:
        bot_scheduler.attach_loop(asyncio.get_running_loop())

    get_driver().on_bot_connect(_old_sync_hook)
    bot_scheduler._LOOP["loop"] = None
    errs = await call_hooks()
    check("同步钩子确实复现 no running event loop",
          errs and "no running event loop" in str(errs[0]), f"→ {errs[0] if errs else '未报错'}")
    check("同步钩子没能挂上循环（等于从没生效）",
          bot_scheduler._LOOP["loop"] is None)
    get_driver()._bot_connection_hook.clear()

    # —— 新写法：async def（现在的 bot_runtime._sched_attach）——
    async def _new_async_hook(bot) -> None:
        bot_scheduler.attach_loop(asyncio.get_running_loop())

    get_driver().on_bot_connect(_new_async_hook)
    errs = await call_hooks()
    check("async 钩子不报错", not errs, f"→ {errs}")
    check("async 钩子把驱动循环交给了调度器",
          bot_scheduler._LOOP["loop"] is asyncio.get_running_loop())

    print(f"\n通过 {OK} / 失败 {FAIL}")
    return 1 if FAIL else 0


raise SystemExit(asyncio.run(main()))
