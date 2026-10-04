# -*- coding: utf-8 -*-
"""补齐 pattern_sources.json 缺失武器的「获取方式」——爬 light.gg source-hint。

背景：build_pattern_sources.py 依赖 Bungie 藏品定义的 sourceString，但往季
世界掉落老枪（Ψ永恒IV、塔霍马01…）官方已撤掉藏品，卡片上「获取方式」为空。
light.gg 物品页仍给这批枪维护了 source-hint（如 Purchasable from Weekly:
Vanguard Arms Rewards），本脚本经 CDP 通道（weapon_usage 同款，需先跑
start_edge_debug.bat）抓 zh-chs 物品页提取，翻译后并入 pattern_sources.json。

用法：
    .venv\\Scripts\\python build_pattern_sources_lightgg.py            # 抓缺失+合并
    .venv\\Scripts\\python build_pattern_sources_lightgg.py --dry     # 只抓不合并
    .venv\\Scripts\\python build_pattern_sources_lightgg.py --merge-only  # 跳过抓取直接合并
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import weapon_usage as wu

MI = "manifest_index"
RAW = os.path.join(MI, "lightgg_sources_raw.json")
OUT = os.path.join(MI, "pattern_sources.json")
_CF_MARKS = ("Just a moment", "cf-chl", "challenge-platform", "Attention Required")
_H5 = re.compile(r'<div class="source-hint">\s*<h5>(.*?)</h5>', re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _missing() -> dict[str, list[str]]:
    """ weapons_full/weapons 里不在 pattern_sources 的名字 → hash 列表"""
    ps = wu._load_json(OUT, {}) or {}
    out: dict[str, list[str]] = {}
    for f in ("weapons_full.json", "weapons.json"):
        try:
            data = json.load(open(os.path.join(MI, f), encoding="utf-8"))
        except OSError:
            continue
        for h, w in data.items():
            nm = w.get("name")
            if nm and nm not in ps:
                lst = out.setdefault(nm, [])
                if h not in lst:
                    lst.append(h)
    return out


async def _fetch_all(todo: dict[str, list[str]]) -> dict[str, str]:
    """逐 hash 抓 zh-chs 页，提取 source-hint 英文原文 → raw 缓存"""
    raw = wu._load_json(RAW, {}) or {}
    pairs = sorted({(h, nm) for nm, hs in todo.items() for h in hs if not raw.get(h)})
    if not pairs:
        print("raw 缓存已覆盖全部目标，跳过抓取")
        return raw
    if not await wu._cdp_probe():
        print("CDP 端口 127.0.0.1:9222 不通：先双击 start_edge_debug.bat 重启 Edge 再跑")
        sys.exit(1)
    print(f"待抓 {len(pairs)} 页（CDP 通道，节流 1.5s）")
    ok = fail = 0
    async with wu._CdpSession() as sess:
        for i, (h, nm) in enumerate(pairs, 1):
            url = f"https://www.light.gg/db/zh-chs/items/{h}/"
            hint = ""
            try:
                html = await sess.fetch(url)
            except Exception as e:  # noqa: BLE001
                html = ""
                print(f"[{i}/{len(pairs)}] {nm}({h}) 开页失败: {type(e).__name__}: {str(e)[:80]}")
            if html and not any(m in html for m in _CF_MARKS):
                m = _H5.search(html)
                if m:
                    hint = _WS.sub(" ", _TAG.sub(" ", m.group(1))).strip()
                    # 去掉 font-awesome 图标残留
                    hint = re.sub(r"^Purchasable from\s*", "Purchasable from ", hint)
            elif html:
                print(f"[{i}/{len(pairs)}] {nm}({h}) 仍是挑战页，跳过")
            if hint:
                raw[h] = hint
                wu._dump_json(RAW, raw)
                ok += 1
                print(f"[{i}/{len(pairs)}] {nm}({h}) → {hint[:70]}")
            else:
                raw.setdefault(h, "")
                fail += 1
            await asyncio.sleep(1.5)
    print(f"抓取完成：ok {ok}，空/失败 {fail} → {RAW}")
    return raw


# ---------------------------------------------------------------- 翻译 + 合并

# light.gg source-hint 原文 → 中文（2026-10 实抓去重后共 8 种）
HINT_ZH = {
    "Purchasable from Commander Zavala": "来源：指挥官萨瓦拉（声望升级包）",
    "Purchasable from Weekly: Vanguard Arms Rewards": "来源：周常「先锋军械库」奖励",
    "Purchasable from Lord Shaxx": "来源：萨克斯领主（声望升级包）",
    "Purchasable from Distorted Solstice Engram": "来源：日晷活动记忆水晶（扭曲）",
    "Purchasable from Xûr": "来源：仄（每周末轮换）",
    "Purchasable from Banshee-44": "来源：班西-44（声望升级包）",
    "Purchasable from Saint-14": "来源：圣-14（声望升级包）",
    "Purchasable from 31 vendors, including Lawless Frontier Gear":
        "来源：多个商人（含「无法之地边境」装备）",
}


def _translate(hint: str) -> str:
    if not hint:
        return ""
    if hint in HINT_ZH:
        return HINT_ZH[hint]
    return f"来源：{hint}"  # 兜底：直接放英文原文，至少不是空


def _merge(raw: dict[str, str]) -> None:
    ps = wu._load_json(OUT, {}) or {}
    todo = _missing()
    added = 0
    for nm, hs in sorted(todo.items()):
        zh = sorted({_translate(raw.get(h, "")) for h in hs} - {""})
        if not zh:
            continue
        best = " / ".join(zh)
        ps[nm] = {"group": _group(best), "raw": best, "dungeon": False}
        added += 1
    wu._dump_json(OUT, ps)
    print(f"合并完成：新增 {added} 条 → {OUT}（现共 {len(ps)} 条）")


def _group(hint: str) -> str:
    m = re.search(r"来源：([^（/]+)", hint)
    return (m.group(1) if m else hint)[:14]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只抓取落 raw 缓存，不改 pattern_sources.json")
    ap.add_argument("--merge-only", action="store_true", help="跳过抓取，只用 raw 缓存合并")
    args = ap.parse_args()

    todo = _missing()
    n_hash = sum(len(v) for v in todo.values())
    print(f"缺失来源的名字 {len(todo)} 个 / hash {n_hash} 个")
    if args.merge_only:
        raw = wu._load_json(RAW, {}) or {}
    else:
        raw = asyncio.run(_fetch_all(todo))
    if not args.dry:
        _merge(raw)


if __name__ == "__main__":
    main()
