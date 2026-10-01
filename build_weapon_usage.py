# -*- coding: utf-8 -*-
"""批量抓 light.gg 使用率，生成 manifest_index/weapon_usage_snapshot.json（运行时提供者①）。

直连 light.gg 必被 Cloudflare 拦（403/Turnstile，已实测），两条可用通道二选一：

    python build_weapon_usage.py --via cdp            # 推荐：CDP 接入用户已验证浏览器
    python build_weapon_usage.py --via cdp --limit 50
    python build_weapon_usage.py --via cdp --hashes 42435996,717150101
    python build_weapon_usage.py --proxy http://127.0.0.1:7890   # 老代理通道

CDP 模式（--via cdp）：
- 前置：先双击 start_edge_debug.bat 让 Edge 带独立调试 profile + --remote-debugging-port=9222
  重启（新版 Edge 对默认配置目录忽略调试端口），用户浏览器已过 light.gg 验证，无需代理；
- 连一次 CDP，默认 2 个标签页并发（--workers 可调 2~3），每页间隔 1~2s（--sleep）；
- 页面仍是 Cloudflare 挑战页则重试一次，再不行跳过并记失败日志；
- 独立浏览器通道（--proxy）：
  httpx 走代理抓，被拦/解析失败再交 wu 的 Playwright 真 Edge 通道。

行为（两种模式相同）：
- 遍历 weapons_full.json 里所有带特性列的武器 hash；断点续跑（已在快照里的跳过）；
- 失败写日志并继续；每次成功即落盘（崩溃不丢进度）；
- 快照只存 raw（{hash: {"source","fetched_at","plugs","mw","mods"}}），中文名/图标运行时 join；
- 复用 weapon_usage.py 的解析函数，解析失败原始 HTML 落盘 logs/usage_debug/。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time

import httpx

import weapon_usage as wu

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MI = "manifest_index"
_CF_MARKS = ("Just a moment", "cf-chl", "challenge-platform", "Attention Required")


def main():
    ap = argparse.ArgumentParser(description="批量抓 light.gg 武器使用率 → 快照")
    ap.add_argument("--via", choices=("cdp", "proxy"), default=None,
                    help="抓取通道：cdp=接入用户已验证浏览器(9222，推荐)；proxy=独立浏览器走代理。"
                         "缺省：给了 --proxy 走 proxy，否则 cdp")
    ap.add_argument("--proxy", default=os.environ.get("D2_USAGE_PROXY"),
                    help="（--via proxy 时）HTTP 代理，如 http://127.0.0.1:7890")
    ap.add_argument("--limit", type=int, default=0, help="最多抓 N 把（0=全部）")
    ap.add_argument("--hashes", default="", help="逗号分隔的武器 hash，只抓这些")
    ap.add_argument("--sleep", type=float, default=None,
                    help="每页间隔秒数（默认：cdp=1.5，proxy=1.5）")
    ap.add_argument("--workers", type=int, default=2,
                    help="（--via cdp 时）同时开的标签页数 2~3（默认 2，上限 4）")
    ap.add_argument("--out", default=os.path.join(MI, "weapon_usage_snapshot.json"))
    args = ap.parse_args()

    wf = wu._load_weapons_full()
    if not wf:
        print("错误：读不到 manifest_index/weapons_full.json")
        sys.exit(2)

    if args.hashes:
        targets = [h.strip() for h in args.hashes.split(",") if h.strip()]
    else:  # 所有带特性列的武器
        targets = [h for h, w in wf.items()
                   if any("特性" in (c.get("t") or "") for c in (w.get("plugs", {}).get("cols") or []))]
    snap = wu._load_json(args.out, {}) or {}
    todo = [h for h in targets if h not in snap]
    if args.limit:
        todo = todo[:args.limit]
    if args.via is None:
        args.via = "proxy" if args.proxy else "cdp"
    print(f"武器总数 {len(targets)}，快照已有 {len(snap)}，本次待抓 {len(todo)}")

    if args.via == "cdp":
        args.sleep = args.sleep if args.sleep is not None else 1.5
        print(f"通道 CDP（用户已验证浏览器 127.0.0.1:9222，无需代理）；{args.workers} tab 并发，节流 {args.sleep}s/页")
        ok, fail = asyncio.run(_cdp_run(args, todo, snap, wf))
    else:
        args.sleep = args.sleep if args.sleep is not None else 1.5
        print(f"通道 独立浏览器+代理"
              + (f"（代理 {args.proxy}）" if args.proxy else "（无代理！直连大概率被 Cloudflare 拦）")
              + f"；节流 {args.sleep}s/页")
        ok, fail = _proxy_run(args, todo, snap, wf)

    print(f"完成：成功 {ok}，失败 {fail}，快照现共 {len(snap)} 条 → {args.out}")
    if ok == 0 and fail > 0:
        if args.via == "cdp":
            print("CDP 通道全失败：确认已双击 start_edge_debug.bat 重启 Edge，"
                  "且浏览器保持开着；重跑（支持断点续跑）。")
        else:
            print("网络不可达或全部被拦截，快照未更新。检查代理/网络后重跑（支持断点续跑）。")
        sys.exit(1)


# ---------------------------------------------------------------- CDP 模式

async def _cdp_run(args, todo, snap, wf):
    if not await wu._cdp_probe():
        print("CDP 端口 127.0.0.1:9222 不通：请先双击 start_edge_debug.bat 重启 Edge"
              "（独立调试 profile，登录态/验证全保留），再重跑本脚本。")
        sys.exit(1)
    workers = max(1, min(int(args.workers), 4))
    total = len(todo)
    ok = fail = 0
    snap_lock = asyncio.Lock()
    q: asyncio.Queue = asyncio.Queue()
    for h in todo:
        q.put_nowait(h)
    idx = {h: i for i, h in enumerate(todo, 1)}

    async def one(sess, h):
        nonlocal ok, fail
        name = wf.get(h, {}).get("name", "")
        url = f"https://www.light.gg/db/items/{h}/"
        tag = f"[{idx[h]}/{total}]"
        html = ""
        try:
            html = await sess.fetch(url)      # 内部已等挑战放行最长 25s + 固定沉降 3s
        except Exception as e:  # noqa: BLE001
            print(f"{tag} {h} ({name}) CDP 开页失败: {type(e).__name__}: {str(e)[:90]}")
        if html and any(m in html for m in _CF_MARKS):    # 仍是挑战页 → 重试一次
            print(f"{tag} {h} ({name}) 仍是挑战页，重试一次…")
            try:
                html = await sess.fetch(url)
            except Exception as e:  # noqa: BLE001
                html = ""
                print(f"{tag} {h} 重试失败: {type(e).__name__}: {str(e)[:90]}")
        raw = None
        if html and not any(m in html for m in _CF_MARKS):
            try:
                raw = wu.parse_lightgg_html(html)
            except Exception:  # noqa: BLE001
                wu._dump_debug_html(int(h), html)
                print(f"{tag} {h} 解析失败，HTML 已存 logs/usage_debug/")
        elif html:
            print(f"{tag} {h} ({name}) 重试后仍是 Cloudflare 挑战页，跳过")
        async with snap_lock:
            if raw:
                snap[h] = {"source": "lightgg", "fetched_at": datetime.date.today().isoformat(),
                           "cols": [[list(p) for p in col] for col in raw.get("cols") or []],
                           "plugs": [list(p) for p in raw.get("plugs") or []],
                           "mw": [list(p) for p in raw.get("mw") or []],
                           "mods": [list(p) for p in raw.get("mods") or []],
                           "combos": [list(c) for c in raw.get("combos") or []]}
                wu._dump_json(args.out, snap)
                ok += 1
                print(f"{tag} {h} ({name}) ok, cols={len(raw.get('cols') or [])}, "
                      f"plugs={len(raw.get('plugs') or [])}, combos={len(raw.get('combos') or [])}")
            else:
                fail += 1

    async def worker(sess, _wid):
        while True:
            try:
                h = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            await one(sess, h)
            await asyncio.sleep(args.sleep)

    async with wu._CdpSession() as sess:
        print(f"已接入浏览器会话（contexts={len(sess._browser.contexts)}），"
              f"{workers} 个标签页并发，节流 {args.sleep}s/页。只新开/关闭自己的标签页，"
              f"不动你已开的页面。")
        await asyncio.gather(*(worker(sess, i) for i in range(workers)))
    return ok, fail


# ---------------------------------------------------------------- 代理模式（原通道）

def _proxy_run(args, todo, snap, wf):
    ok = fail = 0
    cli = httpx.Client(timeout=wu._HTTP_TIMEOUT,
                       headers={"User-Agent": wu.EDGE_UA},
                       proxy=args.proxy, follow_redirects=True)
    for i, h in enumerate(todo, 1):
        url = f"https://www.light.gg/db/items/{h}/"
        status, html = None, ""
        try:
            r = cli.get(url)
            status, html = r.status_code, r.text
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(todo)}] {h} ({wf.get(h, {}).get('name')}) 请求失败: {type(e).__name__}: {str(e)[:90]}")
        raw = None
        if status == 200 and html and not any(m in html for m in _CF_MARKS):
            try:
                raw = wu.parse_lightgg_html(html)
            except Exception:  # noqa: BLE001
                wu._dump_debug_html(int(h), html)
                print(f"[{i}/{len(todo)}] {h} 解析失败，HTML 已存 logs/usage_debug/")
        elif status is not None:
            cf = any(m in html for m in _CF_MARKS) if html else False
            print(f"[{i}/{len(todo)}] {h} HTTP {status}{' (Cloudflare 挑战页)' if cf else ''}")
            if cf or status == 403:
                if not args.proxy:
                    print(">>> 直连 light.gg 被 Cloudflare 拦截（本机已实测无法直连）。"
                          "请加 --proxy http://127.0.0.1:7890 或设置 D2_USAGE_PROXY 后重试。")
                fail += 1
                time.sleep(args.sleep)
                continue
        if raw:
            snap[h] = {"source": "lightgg", "fetched_at": datetime.date.today().isoformat(),
                       "cols": [[list(p) for p in col] for col in raw.get("cols") or []],
                       "plugs": [list(p) for p in raw.get("plugs") or []],
                       "mw": [list(p) for p in raw.get("mw") or []],
                       "mods": [list(p) for p in raw.get("mods") or []],
                       "combos": [list(c) for c in raw.get("combos") or []]}
            wu._dump_json(args.out, snap)
            ok += 1
            print(f"[{i}/{len(todo)}] {h} ({wf.get(h, {}).get('name')}) ok, plugs={len(raw['plugs'])}")
        else:
            fail += 1
        time.sleep(args.sleep)
    cli.close()
    return ok, fail


if __name__ == "__main__":
    main()
