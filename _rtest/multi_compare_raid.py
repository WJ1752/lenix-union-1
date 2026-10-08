"""多账号三方对比 v2：本地卡片 vs raid.report/dungeon.report 页面 vs api.raidreport.dev。
页面=前端实时聚合官方历史（等它算完再抓）；API=rr 数据库（可能滞后）；卡片取两者 max 口径。
"""
import asyncio
import json
import re
import sys

import httpx
from playwright.async_api import async_playwright
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（本脚本在 _rtest/ 下）
import destiny_data  # noqa: E402

EN2ZH = {
    "Crota's End": "克洛塔的末日", "King's Fall": "国王的陨落",
    "Salvation's Edge": "救赎的边缘", "Root of Nightmares": "梦魇根源",
    "Vault of Glass": "玻璃拱顶", "The Desert Perpetual": "永恒沙漠",
    "Last Wish": "最后一愿", "Garden of Salvation": "救赎花园",
    "Deep Stone Crypt": "深岩墓室", "Vow of the Disciple": "门徒誓约",
    "Leviathan": "利维坦", "Eater of Worlds": "世界吞噬者，利维坦",
    "Spire of Stars": "利维坦，星之塔", "Scourge of the Past": "往日之苦",
    "Crown of Sorrow": "忧愁王冠",
    "Shattered Throne": "破碎王座", "Pit of Heresy": "异端深渊",
    "Prophecy": "预言", "Grasp of Avarice": "贪婪之握", "Duality": "二象性",
    "Spire of the Watcher": "守望者尖塔", "Ghosts of the Deep": "深渊机灵",
    "Warlord's Ruin": "战争领主的废墟", "Vesper's Host": "晚星之主",
    "Sundered Doctrine": "分离教义", "Equilibrium": "平衡",
}
DIFF_TOKEN = r"\s*(?:标准|普通|大师|巅峰|竞赛|永恒|史诗|自定义|传说|专家|高级|宗师)"


def t2s(txt):
    if not txt:
        return None
    m = re.match(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", txt.strip())
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def strip_tags(html):
    t = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t]+", " ", t)


def parse_card(html):
    """直接解析 rrow 行结构（<b>名</b><span class='rdiff'>难度</span>…最快全程 X<br>最近 Y）"""
    im = html.find("大师难度")
    segs = [(html[:im], False)] + ([(html[im:], True)] if im >= 0 else [])
    out = {}
    for seg, master in segs:
        for row in re.findall(r"<a class='rrow'.*?</a>", seg, re.S):
            nm = re.search(r"<b>([^<]*)</b>", row)
            if not nm:
                continue
            cl = re.search(r"通关<b>(\d+)</b>", row)
            ffc = None
            mm = re.search(r"最快全程 (\d+)分(\d+)秒", row)
            if mm:
                ffc = int(mm.group(1)) * 60 + int(mm.group(2))
            out[(nm.group(1).strip(), master)] = {
                "clears": int(cl.group(1)) if cl else 0, "ffc": ffc}
    return out


def parse_page(text):
    """逐行解析：名/徽章/总数/Recent/CLEARS/最快/.../STATS行/难度行。→ {EN: {...}}"""
    lines = text.split("\n")
    out = {}
    for en in EN2ZH:
        idx = [i for i, l in enumerate(lines) if l.strip() == en]
        if not idx:
            continue
        i = idx[0]
        d = {"total": None, "fastest": None, "std": None, "std_ffc": None,
             "mst": None, "mst_ffc": None, "loaded": False}
        j = i + 1
        while j < len(lines) and lines[j] not in ("Recent Raids", "Recent Dungeons"):
            if lines[j].strip().isdigit():
                d["total"] = int(lines[j].strip())
            j += 1
        while j < len(lines):
            l = lines[j]
            if l.startswith("STATS"):
                k = j + 1
                while k < len(lines):
                    parts = lines[k].split("\t")
                    if parts[0] in ("Master", "Normal", "Prestige", "Epic", "Contest",
                                    "Guided Games"):
                        cl = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else None
                        tm = t2s(parts[2]) if len(parts) > 2 else None
                        if parts[0] == "Master":
                            d["mst"], d["mst_ffc"] = cl, tm
                        elif parts[0] != "Guided Games":
                            # 多个非大师难度行（Normal/Prestige/Epic/Contest）求和
                            if d["std"] is None:
                                d["std"], d["std_ffc"] = 0, None
                            d["std"] += cl or 0
                            if tm and (d["std_ffc"] is None or tm < d["std_ffc"]):
                                d["std_ffc"] = tm
                        if cl is not None:
                            d["loaded"] = True
                    elif parts[0] == "Kills":
                        if len(parts) > 1 and re.match(r"[\d,]+", parts[1].strip() or ""):
                            d["loaded"] = True
                        break
                    k += 1
                break
            if l == "CLEARS" and j + 1 < len(lines):
                d["fastest"] = t2s(lines[j + 1])
            j += 1
        out[en] = d
    return out


async def fetch_rr(page, mid, kind):
    raw = await page.evaluate(
        """async u => { try { const r = await fetch('https://api.raidreport.dev'+u,
            {credentials:'omit'}); return {status:r.status, body:await r.text()}; }
            catch(e){ return {status:-1, body:String(e)}; } }""", f"/{kind}/player/{mid}")
    if raw["status"] != 200:
        return None
    acts = {}
    for a in ((json.loads(raw["body"]).get("response") or {}).get("activities") or []):
        v = a.get("values") or {}
        ffc = (v.get("fastestFullClear") or {}).get("value")
        acts[str(a["activityHash"])] = {"clears": int(v.get("clears") or 0),
                                        "ffc": int(ffc) if ffc else None}
    return acts


def rr_expect(acts):
    groups = {}
    destiny_data._merge_rr_stats(groups, acts)
    return {(g["name"], g["master_mode"]): g for g in groups.values()}


PLAYERS = [
    {"name": "Benson", "code": "7463", "plat": "steam", "mid": "4611686018524290716"},
    {"name": "Niko", "code": "2967", "plat": "psn", "mid": "4611686018514067126"},
    {"name": "NativeJuggler97", "code": "5150", "plat": "xbox", "mid": "4611686018457683054"},
    {"name": "xBiscuiiitx", "code": "7994", "plat": "epic", "mid": "4611686018530479989"},
    {"name": "Nerclid", "code": "4076", "plat": "xbox", "mid": "4611686018441300568"},
    {"name": "Wj", "code": "8984", "plat": "steam", "mid": "4611686018494788027"},
]
KINDS = [("raid", 4, "raid.report"), ("dungeon", 82, "dungeon.report")]


async def main():
    pages_txt, rr_data = {}, {}
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
        ctx = b.contexts[0]
        page = await ctx.new_page()
        try:
            await page.goto("https://raid.report/steam/4611686018494788027",
                            timeout=60000, wait_until="commit")
            for _ in range(30):
                t = await page.title()
                if t and "moment" not in t.lower():
                    break
                await page.wait_for_timeout(1000)
            for pl in PLAYERS:
                for kind, _m, _s in KINDS:
                    rr_data[f"{pl['name']}:{kind}"] = await fetch_rr(
                        page, pl["mid"], kind)
                    print("rr", pl["name"], kind,
                          "ok" if rr_data[f"{pl['name']}:{kind}"] else "FAIL", flush=True)
            for pl in PLAYERS:
                for kind, _m, site in KINDS:
                    url = f"https://{site}/{pl['plat']}/{pl['mid']}"
                    try:
                        txt = ""
                        for attempt in range(2):
                            pg = await ctx.new_page()   # 每页新开标签，防 document 崩溃复用
                            try:
                                await pg.goto(url, timeout=60000, wait_until="commit")
                                for _ in range(30):
                                    t = await pg.title()
                                    if t and "moment" not in t.lower() and "just a" not in t.lower():
                                        break
                                    await pg.wait_for_timeout(1000)
                                # 等前端把官方历史聚合完（Kills 有数字才算加载好）
                                for _ in range(40):
                                    txt = await pg.evaluate("() => document.body.innerText")
                                    if "STATS" in txt and len(
                                            re.findall(r"Kills\t[\d,]+", txt)) >= 1:
                                        break
                                    await pg.wait_for_timeout(1500)
                                if "STATS" in txt and re.search(r"Kills\t[\d,]+", txt):
                                    break
                                await pg.wait_for_timeout(5000)
                            finally:
                                await pg.close()
                        pages_txt[f"{pl['name']}:{kind}"] = txt
                        print("page", pl["name"], kind, len(txt),
                              "loaded" if re.search(r"Kills\t[\d,]+", txt) else "UNLOADED",
                              flush=True)
                    except Exception as e:  # noqa: BLE001
                        print("page FAIL", pl["name"], kind, str(e)[:80], flush=True)
        finally:
            await page.close()
            await b.close()
    json.dump(pages_txt, open("_cmp_pages.json", "w", encoding="utf-8"), ensure_ascii=False)

    cli = httpx.Client(timeout=590)
    lines, ok_n, diff_n = [], 0, 0
    for pl in PLAYERS:
        qname = f"{pl['name']}%23{pl['code']}"
        for kind, mode, site in KINDS:
            key = f"{pl['name']}:{kind}"
            html = cli.get("http://127.0.0.1:8900/card?name=" + qname + "&mode="
                           + ("raid" if mode == 4 else "dungeon")).text
            card = parse_card(html)
            pg = parse_page(pages_txt.get(key, ""))
            acts = rr_data.get(key)
            exp = rr_expect(acts) if acts else {}
            lines.append(f"########## {pl['name']}#{pl['code']} {kind} ##########")
            for en, pd in sorted(pg.items(), key=lambda kv: -(kv[1]["total"] or 0)):
                if not pd["loaded"] and pd["total"] is None:
                    continue
                zh = EN2ZH[en]
                my = [card.get((zh, False)), card.get((zh, True))]
                my_total = sum(x["clears"] for x in my if x)
                my_ffc = min([x["ffc"] for x in my if x and x["ffc"]], default=None)
                ex = [exp.get((zh, False)), exp.get((zh, True))]
                e_total = sum(x["clears"] for x in ex if x)
                e_ffc = min([x["ffc"] for x in ex if x and x["ffc"]], default=None)
                p_std = pd["std"] or 0
                p_total = p_std + (pd["mst"] or 0)
                p_ffc = min([x for x in (pd["fastest"], pd["std_ffc"], pd["mst_ffc"])
                             if x], default=None)
                prob = []
                if pd["total"] is not None and pd["loaded"] and my_total != pd["total"] \
                        and my_total != p_total:
                    prob.append(f"总:卡片{my_total} 页面{pd['total'] or p_total}")
                elif pd["loaded"] and my_total != p_total and p_total:
                    prob.append(f"总:卡片{my_total} 页面行和{p_total}")
                if pd["loaded"] and my_ffc != p_ffc:
                    prob.append(f"快:卡片{my_ffc} 页面{p_ffc}")
                if prob:
                    diff_n += 1
                    lines.append(f"[DIFF] {en:<22} 页面:总{pd['total']}/快{p_ffc} "
                                 f"(普{p_std}/大{pd['mst']}) | 卡片:总{my_total}/快{my_ffc} "
                                 f"(普{(my[0] or {}).get('clears')}/大{(my[1] or {}).get('clears')})"
                                 f" | API:总{e_total}/快{e_ffc} → " + "; ".join(prob))
                else:
                    ok_n += 1
                    lines.append(f"[OK ] {en:<22} 总{my_total}/快{my_ffc} "
                                 f"(卡片普{(my[0] or {}).get('clears')}/大{(my[1] or {}).get('clears')})")
    lines.append(f"\n===== 汇总: OK {ok_n} / DIFF {diff_n} =====")
    out = "\n".join(lines)
    open("_cmp_result.txt", "w", encoding="utf-8").write(out)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
