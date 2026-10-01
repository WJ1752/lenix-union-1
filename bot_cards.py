"""小日向式图片卡片排版：把查询结果拼成 HTML（交给 card_render 截图成 PNG）

配色/圆角/字体对齐 webui.py 的 CARD_CSS，保证机器人的图片与网页查询站同一套视觉：
深色卡片 + 蓝色分组标题 + 金色数值 + 绿/红增减标签。
每个 build_* 返回完整 HTML 文档字符串。
"""
from __future__ import annotations

import json
import os
import re
import sys

import destiny_data as d2

# ---------- 通用 ----------

FONT = '"Microsoft YaHei",sans-serif'

CSS = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0b0f19;color:#e8eef7;
     width:760px;padding:20px}
.card{background:linear-gradient(160deg,#141c2e,#0e1524);border:1px solid #2c3a52;
      border-radius:14px;padding:22px;box-shadow:0 6px 24px rgba(0,0,0,.5)}
h1{margin:0 0 2px;font-size:24px}
.sub{color:#8fa3bd;font-size:13px;margin-bottom:14px}
h2{font-size:15px;color:#5ea8ff;border-left:3px solid #2f6fed;padding-left:8px;margin:16px 0 8px}
h3{margin:0 0 6px;font-size:13px;color:#8fa3bd;font-weight:normal}
.row{display:flex;justify-content:space-between;padding:7px 10px;border-radius:6px;font-size:14px}
.row:nth-child(odd){background:rgba(255,255,255,.04)}
.row b{color:#ffd76e}
.row.hl b{color:#7dff9c}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.grid.two{grid-template-columns:1fr 1fr}
section{background:rgba(255,255,255,.03);border-radius:8px;padding:4px 8px 10px}
.char{display:flex;align-items:center;gap:12px;background-size:cover;border-radius:8px;
      padding:10px 14px;margin:8px 0;background-color:#1a2438}
.char img{height:44px;border-radius:4px}
.ci{display:flex;flex-direction:column;line-height:1.5;font-size:14px;background:rgba(11,15,25,.72);
    padding:4px 10px;border-radius:6px}
.ci .dim{color:#8fa3bd;font-size:12px}
/* 武器头部 */
.whead{display:flex;gap:16px;align-items:flex-start}
.wicon{position:relative;width:96px;height:96px;flex-shrink:0}
.wicon>img{position:absolute;left:0;top:0;width:96px;height:96px;object-fit:contain}
.wicon img.wm{z-index:2}
.winfo{flex:1;min-width:0}
.flavor{color:#a8b8cc;font-size:13px;line-height:1.6;margin:8px 0 0}
/* 同名多版本条 */
.vers{margin-top:10px;padding:8px 12px;border:1px solid #2c3b52;border-radius:8px;font-size:13px;color:#cfe0f2;line-height:1.9}
.vers em{font-style:normal;color:#ffd166;font-weight:700;margin:0 2px}
.vers b{color:#9db4cc;font-weight:600;margin-left:6px}
.vers b.on{color:#ffd166;text-shadow:0 0 6px rgba(255,209,102,.35)}
.vers span{display:block;color:#7f93aa;font-size:12px;margin-top:2px}
/* 属性条 */
.stat{display:flex;align-items:center;gap:10px;font-size:13px;margin:5px 0}
.stat>span{width:92px;color:#a8b8cc;flex-shrink:0}
.stat i{flex:1;height:8px;background:#1a2438;border-radius:4px;overflow:hidden}
.stat i u{display:block;height:100%;background:linear-gradient(90deg,#2f6edb,#5ea8ff)}
.stat>b{width:36px;text-align:right;color:#ffd76e}
.stats2{display:grid;grid-template-columns:1fr 1fr;gap:1px 22px}
/* 绝对数量（射速/弹匣/充能时间）：不画条，单独一行写数字 */
.plainstat{display:flex;flex-wrap:wrap;gap:2px 20px;font-size:13px;color:#a8b8cc;margin:0 0 8px}
.plainstat b{color:#ffd76e;font-weight:700;margin-left:4px}
/* 插件列 */
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.pcol{background:rgba(255,255,255,.03);border-radius:8px;padding:8px 9px}
.chip{display:flex;align-items:center;gap:7px;background:#0e1524;border:1px solid #22304a;
      border-radius:6px;padding:4px 8px;margin:5px 0;font-size:12.5px}
.chip img{width:24px;height:24px;object-fit:contain;border-radius:4px;flex-shrink:0}
.chip b{font-weight:600}
.notes{margin-top:8px}
.note{font-size:12.5px;color:#a8b8cc;line-height:1.65;background:#0e1524;border-radius:7px;
      padding:7px 10px;margin:6px 0;white-space:pre-wrap}
.note b{color:#5ea8ff}
/* 异域催化的数值加成 */
.catstat{color:#ffd76e;font-weight:700;margin-left:6px}
.catfx{color:#cfd8e3;margin-top:3px}
.catfx b{color:#c9a6ff}
.catci{color:#8fa3bd;font-size:12px;margin-top:4px;padding-top:4px;border-top:1px dashed #2c3a52}
.catunlock{color:#8fa3bd;font-size:12px;margin-top:2px}
/* 升金（强化 perk）后的数值：金色高亮 */
.enh{color:#ffd76e;font-weight:700;background:rgba(255,215,110,.14);
     border-radius:4px;padding:0 3px}
.legend{font-size:11.5px;color:#8fa3bd;margin:2px 0 0}
.vnote{font-size:12.5px;color:#a8b8cc;line-height:1.65;margin:6px 0 0}
/* perk 卡 */
.ptags{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.up{color:#7dff9c;background:rgba(55,192,110,.15);border-radius:5px;padding:2px 8px;font-size:12.5px}
.dn{color:#ff8d85;background:rgba(217,72,63,.15);border-radius:5px;padding:2px 8px;font-size:12.5px}
/* 提示卡 */
.nt{font-size:20px;font-weight:bold;padding:2px 0 2px 12px;border-left:4px solid #2f6fed;margin-bottom:14px}
.nt.ok{border-color:#7dff9c;color:#7dff9c}
.nt.warn{border-color:#ffd76e;color:#ffd76e}
.nt.err{border-color:#ff8d85;color:#ff8d85}
.nline{font-size:14px;line-height:1.9;color:#cfd8e3;margin:6px 0}
.nline code{background:#0e1524;border-radius:5px;padding:2px 7px;color:#5ea8ff;font-size:13px}
/* 帮助卡 */
.help{display:grid;grid-template-columns:76px 1fr;gap:8px 12px;align-items:start}
.help .cat{font-size:14px;font-weight:bold;color:#ffd76e;text-align:right;padding-top:5px}
.help .cmds{font-size:14px;line-height:2.1;color:#cfd8e3}
.help .cmds code{background:#0e1524;border-radius:5px;padding:2px 7px;color:#5ea8ff;font-size:13px}
.help .note{color:#8fa3bd;font-size:12.5px;margin-left:6px}
.help .tip{grid-column:1 / -1;background:rgba(255,255,255,.04);border-radius:8px;
           padding:9px 12px;font-size:13px;line-height:1.9;color:#cfd8e3;margin-top:6px}
.help .tip b{color:#ffd76e}
.dim{color:#8fa3bd;font-size:12.5px}
.others{margin-top:10px}
.foot{margin-top:14px;padding-top:10px;border-top:1px solid #22304a;color:#8fa3bd;font-size:12px}
/* 光尘商店 */
.evhead{display:flex;align-items:baseline;gap:8px;font-size:15px;color:#5ea8ff;font-weight:700;
        border-left:3px solid #2f6fed;padding-left:8px;margin:16px 0 8px}
.evhead span{color:#8fa3bd;font-size:12px;font-weight:400}
.evhero{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.evhero.one{grid-template-columns:1fr}
.evbig{position:relative;height:176px;border-radius:12px;overflow:hidden;border:1px solid #22304a;
       background:#0e1524 center/cover no-repeat}
.evbig .scrim{position:absolute;left:0;right:0;bottom:0;height:62%;
              background:linear-gradient(180deg,rgba(6,10,20,0),rgba(6,10,20,.94))}
.evbig .evtier{position:absolute;top:9px;left:11px;font-size:11px;font-weight:700;letter-spacing:.5px;
               text-shadow:0 1px 4px #000}
.evbigg{position:absolute;left:11px;right:11px;bottom:9px;display:flex;align-items:flex-end;gap:8px}
.evbigg .who{flex:1;min-width:0;line-height:1.35}
.evbigg b{display:block;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
          text-shadow:0 1px 4px #000}
.evbigg .ty{color:#c3cede;font-size:12px;text-shadow:0 1px 4px #000}
.evgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.evcard{display:flex;align-items:center;gap:12px;background:#0e1524;border:1px solid #22304a;
        border-radius:12px;padding:10px 12px}
.evico{width:92px;height:92px;border-radius:10px;flex-shrink:0;background:#1a2438;
       display:flex;align-items:center;justify-content:center;border:1px solid #22304a;overflow:hidden}
.evico img{width:88px;height:88px;object-fit:contain}
.evico .noi{color:#8fa3bd;font-size:12px;font-style:normal;text-align:center;padding:0 6px}
.evico.t5{background:linear-gradient(160deg,#4a3b16,#2a2110);border-color:#a9842f}
.evico.t6{background:linear-gradient(160deg,#332a44,#211a2c);border-color:#7d68ab}
.evico.t4{background:linear-gradient(160deg,#183349,#0e1e2b);border-color:#2f6a9e}
.evmeta{flex:1;min-width:0;line-height:1.4}
.evrar{font-size:11px;font-weight:700;letter-spacing:.5px}
.evrar.t5{color:#ffd76e}.evrar.t6{color:#c9a6ff}.evrar.t4{color:#6fb0ff}.evrar.t1{color:#9aa7b8}
.evmeta b{display:block;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:1px 0}
.evsub{color:#8fa3bd;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
.evcost{display:flex;align-items:center;gap:5px;flex-shrink:0;color:#ffd76e;font-weight:700;font-size:16px}
.evcost img{width:18px;height:18px}
/* 武器筛选：三列网格 */
.wfgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px}
.wfcell{display:flex;gap:8px;align-items:center;background:#0e1524;border:1px solid #22304a;
        border-radius:8px;padding:6px 8px;min-width:0}
.wfico{position:relative;width:44px;height:44px;flex-shrink:0}
.wfico>img{position:absolute;left:0;top:0;width:44px;height:44px;object-fit:contain}
.wfico img.wm{z-index:2}
.wftxt{min-width:0;line-height:1.32}
.wftxt b{display:block;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wftxt b.ex{color:#ffd76e}
.wftxt span{display:block;font-size:10.5px;color:#8fa3bd;white-space:nowrap;
            overflow:hidden;text-overflow:ellipsis}
.wftxt span.fr{color:#5ea8ff}
.wfmeta{color:#8fa3bd;font-size:12.5px;margin:-6px 0 10px}
.wfmeta em{color:#ffd76e;font-style:normal}
.as-tag{display:inline-block;background:#223047;color:#9fc1e8;border-radius:9px;
        padding:1px 8px;font-size:11px;margin-left:6px;vertical-align:2px}
.as-bonus{background:#161e2c;border:1px solid #24344d;border-radius:10px;
          padding:10px 12px;margin:10px 0}
.as-head{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:15px}
.as-piece{background:#2b5f9e;color:#fff;border-radius:8px;padding:2px 8px;
          font-size:12px;white-space:nowrap}
.as-text{font-size:13.5px;line-height:1.75;color:#c9d6e8;white-space:pre-wrap}
.as-text b{color:#ffd76e}
.as-row{background:#161e2c;border:1px solid #24344d;border-radius:8px;
        padding:7px 10px;margin:6px 0;font-size:13.5px}
.as-row b{color:#e8f0fb}
.as-src{color:#8fa3bd;font-size:12px;margin-left:6px}
.as-bn{color:#8fa3bd;font-size:12px;margin-top:2px}
.as-rowtop{display:flex;align-items:center;min-width:0}
.as-tags{margin-left:auto;padding-left:10px;white-space:nowrap;overflow:hidden}
.as-tags .as-tag{margin-left:4px;margin-right:0}
.as-set{margin:18px 0 6px}
.as-set h2{font-size:19px;margin:0 0 4px;color:#e8f0fb}
.as-meta{color:#8fa3bd;font-size:12.5px;margin-bottom:8px}
.as-search{display:flex;justify-content:center;margin:0 0 14px}
.as-search input{background:#141c2e;border:1px solid #2c3a52;border-radius:8px;
                 color:#e8f0fb;font-size:14px;padding:9px 14px;width:320px;outline:none}
.as-search input:focus{border-color:#5ea8ff}
.as-cats{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin:0 0 14px}
.as-cat{color:#cfd8e3;background:#141c2e;border:1px solid #2c3a52;border-radius:8px;
        padding:5px 12px;font-size:13px;text-decoration:none;white-space:nowrap}
.as-cat:hover{border-color:#5ea8ff;color:#fff}
.as-cat.on{background:#2f6edb;border-color:#2f6edb;color:#fff;font-weight:bold}
</style></head><body><div class="card">
__BODY__
</div></body></html>"""

_ZH = None


def _zh(name: str) -> str | None:
    """Starside 中文精确数值（按 perk 名索引），没有则 None"""
    global _ZH
    if _ZH is None:
        try:
            _ZH = {k: v.get("text", "") for k, v in
                   json.load(open(d2._idx_file("perk_zh.json"), encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001
            _ZH = {}
    return _ZH.get(name) or None


_CATA_ZH = None


def _cata_zh(name: str) -> str | None:
    """Starside 中文催化/升金文案（按金枪名索引），没有则 None"""
    global _CATA_ZH
    if _CATA_ZH is None:
        try:
            _CATA_ZH = json.load(open(d2._idx_file("exotic_catalysts_zh.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _CATA_ZH = {}
    return _CATA_ZH.get(name) or None


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;"))


def _cut(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


# Starside 中文文本里「↑数值」= 把该 perk 升金（强化特性）后的数值；行首裸「↑」= 强化版补充说明。
# 先转义再高亮（↑ / 数字 / % / × / 问号 / 正负号都不受 HTML 转义影响）。
_ENH = re.compile(r"↑([+\-]?[\d?][\d.?]*[×%]?)?")


def hl_enh_text(text: str) -> str:
    """转义文本并把升金（↑）数值/标记染成金色，返回可直接入 HTML 的片段"""
    return _ENH.sub(
        lambda m: f"<span class='enh'>↑{m.group(1)}</span>" if m.group(1)
        else "<span class='enh'>↑</span>",
        esc(str(text or "")))


def _page(body: str) -> str:
    return CSS.replace("__BODY__", body)


def _dedupe(items: list[dict], key) -> list[dict]:
    """同名变体很多（同武器不同赛季版本 / perk 普通版与强化版），按 key 去重后展示"""
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _stats_grid(rows: list[tuple[str, str, str]]) -> str:
    """三列小统计块：[(标题, [(标签, 值, 类), ...]), ...]"""
    cells = ""
    for title, items in rows:
        body = "".join(f"<div class='row {cls}'><span>{esc(k)}</span><b>{esc(v)}</b></div>"
                       for k, v, cls in items)
        cells += f"<section><h3>{esc(title)}</h3>{body}</section>"
    return f"<div class='grid'>{cells}</div>"


def _mode_items(st: dict, comp: bool) -> list[tuple[str, str, str]]:
    ent = st.get("activitiesEntered") or 0
    out = [("击杀", f"{st.get('kills', 0):,.0f}", ""),
           ("死亡", f"{st.get('deaths', 0):,.0f}", ""),
           ("K/D", f"{st.get('kd', 0):.2f}", "hl")]
    if comp and ent:
        out.append(("胜率", f"{(st.get('activitiesWon') or 0) / ent * 100:.1f}%", "hl"))
    out.append(("场次", f"{ent:,.0f}", ""))
    return out


# ---------- 提示 / 错误 / 绑定 ----------

def notice(title: str, lines: list[str], kind: str = "info") -> str:
    body = (f"<div class='nt {kind if kind != 'info' else ''}'>{esc(title)}</div>"
            + "".join(f"<div class='nline'>{ln}</div>" for ln in lines)
            + "<div class='foot'>命运2 查询 · 数据来自 Bungie.net</div>")
    return _page(body)


def help_card() -> str:
    def cat(name, cmds, note=""):
        n = f"<span class='note'>{note}</span>" if note else ""
        return f"<div class='cat'>{esc(name)}</div><div class='cmds'>{cmds}{n}</div>"

    c = lambda s: f"<code>{esc(s)}</code>"
    rows = (
        cat("玩家", f"{c('/玩家')} {c('/生涯')} {c('/raid')} {c('/地牢')} {c('/pvp')} {c('/pve')} {c('/智谋')}") +
        cat("记录", f"{c('/历史')} {c('/热力图')} {c('/称号')} {c('/锻造')} {c('/生涯武器')} {c('/pve生涯武器')} {c('/宗师')}",
            "pvp 版同理") +
        cat("资料", f"{c('/武器查询 武器名')} {c('/perk查询 perk名')} {c('/护甲套装')} {c('/每日光尘')} {c('/轮换')}") +
        cat("掉落表", f"{c('/掉落 副本名')}", "裸指令也行：/二象性掉落、/ron掉落…，发 /掉落 看列表") +
        cat("武器筛选", f"{c('/武器筛选 关键词…')}", "空格分隔多词，例：/武器筛选 主手 锻造 微冲 900") +
        cat("账号", f"{c('/绑定 玩家名#1234')} {c('/我的')} {c('/解绑')}") +
        "<div class='tip'>"
        "<b>赛季参数</b>：生涯武器末尾加 <code>s27</code> / <code>赛季27</code> 只看该赛季，"
        "PVE 默认当前赛季、PVP 默认全生涯<br>"
        "<b>查别人</b>：玩家类指令后加 <code>@某人</code>（对方绑定过即可）<br>"
        "<b>省事</b>：绑定后玩家类指令可不带名字；群里可直接 <b>@机器人 武器名/perk名</b>；"
        "指令必须带 <code>/</code> 前缀"
        "</div>"
    )
    body = (f"<div class='nt'>指令一览</div><div class='help'>{rows}</div>"
            "<div class='foot'>命运2 查询 · 数据来自 Bungie.net</div>")
    return _page(body)


# ---------- /玩家 ----------

def player_card(data: dict) -> str:
    chars = "".join(
        f"<div class='char' style=\"background-image:url('{esc(c['emblem_bg'])}')\">"
        f"<img src='{esc(c['emblem'])}'><div class='ci'>"
        f"<b>{esc(c['class'])} · {esc(c['race'])}</b>"
        f"<span>光能 {c['light']} · {d2.fmt_hours(c['playtime_min'])}</span>"
        f"<span class='dim'>上线 {esc(c['last_played'])}</span></div></div>"
        for c in data["chars"]
    )
    body = (
        f"<h1>{esc(data['display'])}</h1><div class='sub'>生涯总览 · 数据来自 Bungie.net</div>"
        f"<div class='row'><span>最高光能</span><b>{data['max_light']}</b></div>"
        f"<div class='row'><span>总游戏时长</span><b>{d2.fmt_hours(data['total_playtime'])}</b></div>"
        f"<h2>角色</h2>{chars}"
        f"<h2>三模式速览</h2>"
        + _stats_grid([("PVP", _mode_items(data["pvp"], True)),
                       ("PVE", _mode_items(data["pve"], False)),
                       ("智谋", _mode_items(data["gambit"], True))])
        + "<div class='foot'>PVP/智谋 胜率按对局胜负统计；更细的近期战绩请到查询站对应标签页</div>"
    )
    return _page(body)


# ---------- /生涯 ----------

def career_card(data: dict) -> str:
    tot_k = sum((data[m].get("kills") or 0) for m in ("pvp", "pve", "gambit"))
    tot_d = sum((data[m].get("deaths") or 0) for m in ("pvp", "pve", "gambit"))
    tot_e = sum((data[m].get("activitiesEntered") or 0) for m in ("pvp", "pve", "gambit"))
    tot_kd = (tot_k / tot_d) if tot_d else 0.0
    body = (
        f"<h1>{esc(data['display'])}</h1><div class='sub'>生涯统计 · Bungie 官方 allTime</div>"
        + _stats_grid([("PVP", _mode_items(data["pvp"], True)),
                       ("PVE", _mode_items(data["pve"], False)),
                       ("智谋", _mode_items(data["gambit"], True))])
        + "<h2>总计</h2>"
        + f"<div class='row'><span>总击杀</span><b>{tot_k:,.0f}</b></div>"
        + f"<div class='row'><span>总死亡</span><b>{tot_d:,.0f}</b></div>"
        + f"<div class='row hl'><span>总 K/D</span><b>{tot_kd:.2f}</b></div>"
        + f"<div class='row'><span>总场次</span><b>{tot_e:,.0f}</b></div>"
        + f"<div class='row'><span>总游戏时长</span><b>{d2.fmt_hours(data['total_playtime'])}</b></div>"
        + "<div class='foot'>数据来自 Bungie.net · 生涯累计为官方聚合接口</div>"
    )
    return _page(body)


# ---------- /武器查询 ----------

def _chips(lst: list[dict], n: int = 8) -> str:
    return "".join(
        f"<div class='chip'><img src='{esc(p.get('i', ''))}'><b>{esc(p['n'])}</b></div>"
        for p in lst[:n])


# 属性条的展示顺序（对齐游戏内/DIM：射速→伤害→射程→稳定性→操控性→填装→弹匣→…）
STAT_ORDER = ["每分钟发射数", "伤害", "射程", "稳定性", "操控性", "填装速度", "弹匣",
              "辅助瞄准", "变焦", "后坐方向", "空中效率", "弹药生成", "冲击", "精度",
              "充能时间", "蓄力时间", "充能速度", "爆炸范围", "挥舞速度", "护盾穿透", "举盾速度"]

# 这几种是绝对数量，不在 0–100 量程上（射速 257、弹匣 24、充能 800ms、弹头速度 100），
# 按属性条画会一律顶格，游戏内和 DIM 里也只写数字不画条
PLAIN_STATS = {"每分钟发射数", "射击速度", "弹匣", "弹药容量", "弹头速度",
               "充能时间", "蓄力时间", "蓄能时间"}


def _stat_sorted(stats: list[dict]) -> list[dict]:
    def key(s):
        try:
            return STAT_ORDER.index(s["n"])
        except ValueError:
            return len(STAT_ORDER)
    return sorted(stats, key=key)


def _plugs_cols(pl: dict) -> list[dict]:
    """没重建索引时（plugs 里没有 cols）的兜底列顺序，仅供老数据使用。"""
    out = []
    if pl.get("barrels"):
        out.append({"t": "枪管 / 发射", "items": pl["barrels"]})
    if pl.get("magazines"):
        out.append({"t": "弹匣 / 电池", "items": pl["magazines"]})
    for i, col in enumerate(pl.get("trait_cols") or [], 1):
        out.append({"t": f"特性 {i}", "items": col})
    if pl.get("origins"):
        out.append({"t": "起源特性", "items": pl["origins"]})
    if pl.get("stocks"):
        out.append({"t": "枪托", "items": pl["stocks"]})
    if pl.get("fixed"):
        out.append({"t": "固定配件", "items": pl["fixed"]})
    return out


def weapon_card(w: dict, others: list[str] | None = None,
                vers: list[str] | None = None, ver_cur: int = 0,
                ver_names: list[str] | None = None) -> str:
    pl = w.get("plugs", {})
    wm = f"<img class='wm' src='{esc(w['watermark'])}'>" if w.get("watermark") else ""
    frame = ((pl.get("intrinsic") or [{}])[0]).get("n", "")   # 框架：游戏里就在武器名旁边
    meta = " · ".join(x for x in (frame, w.get("cat"), w.get("type"), w.get("ammo")) if x)
    head = (f"<div class='whead'><span class='wicon'>{wm}"
            f"<img src='{esc(w['icon'])}'></span><div class='winfo'>"
            f"<h1>{esc(w['name'])}</h1>"
            f"<div class='sub'>{esc(meta)}</div>"
            + (f"<p class='flavor'>{esc(_cut(w.get('flavor') or w.get('desc') or '', 120))}</p>"
               if (w.get('flavor') or w.get('desc')) else "")
            + "</div></div>")

    body = head
    if vers and len(vers) > 1:
        cur_tags = "".join(
            f"<b class='on'>{i}</b>·{esc(tag)}" if i == ver_cur else f"<b>{i}</b>·{esc(tag)}"
            for i, tag in enumerate(vers, 1))
        names = ver_names or [""] * len(vers)
        detail = "　".join(f"{i}·{tag}" + (f" {esc(nm)}" if nm else "")
                           for i, (tag, nm) in enumerate(zip(vers, names), 1))
        body += ("<div class='vers'>同名版本 <em>%d</em> 个（1 最旧，默认最新）：%s"
                 "<span>%s</span>"
                 "<span>其它版本：<code>/武器查询 %s 序号</code></span></div>"
                 % (len(vers), cur_tags, detail, esc(w["name"])))
    stats = _stat_sorted(w.get("stats") or [])
    if stats:
        plain = [s for s in stats if s["n"] in PLAIN_STATS]
        bars = [s for s in stats if s["n"] not in PLAIN_STATS]
        body += "<h2>武器素体</h2>"
        if plain:
            body += "<div class='plainstat'>" + "".join(
                f"<span>{esc(s['n'])} <b>{s['v']}</b></span>" for s in plain) + "</div>"
        if bars:
            rows = "".join(
                f"<div class='stat'><span>{esc(s['n'])}</span>"
                f"<i><u style='width:{max(0, min(100, int(s['v'])))}%'></u></i><b>{s['v']}</b></div>"
                for s in bars)
            body += f"<div class='stats2'>{rows}</div>"

    # 插件分列：索引里已按游戏内 socket 顺序排好（plugs.cols），照搬即可，
    # 不再按「枪管→弹匣→特性→起源→枪托」硬编码（那样剑的握把、异域的固定配件会错位）
    cols = pl.get("cols") or _plugs_cols(pl)
    if cols:
        body += "<h2>Perk 池</h2><div class='cols'>" + "".join(
            f"<div class='pcol'><h3>{esc(c['t'])}</h3>{_chips(c['items'], 12)}</div>"
            for c in cols) + "</div>"

    # 异域催化：只有金枪有，给出数值加成与具体效果
    #   stats：催化插件自带的属性增减（Manifest，很多金枪这里是空的）
    #   zh：Starside 中文催化说明（含精确数值写法）；fx：sandbox perk 中文说明
    #   ci：Clarity 社区英文原文（Manifest 没数值的催化只有它有，如「Grants 30 Reload」）
    cata = pl.get("catalysts") or []
    if cata:
        blocks = ""
        for c in cata:
            stats = "　".join(f"{k} +{v}" for k, v in (c.get("stats") or {}).items())
            desc = (c.get("d") or "").strip()
            # 催化自身的描述前半段是通用的「升级为大师杰作」说明，只留最后一句解锁条件
            unlock = ""
            if desc:
                parts = [x.strip() for x in desc.split("。") if x.strip()]
                if parts:
                    unlock = parts[-1] + "。"
            # 催化说明优先中文（Starside 页含精确数值）：拿得到中文就不再贴 Clarity 的英文原文
            zh_txt = _cata_zh(w.get("name") or "") or (c.get("zh") or "")
            zh = "".join(f"<div class='catfx'>{hl_enh_text(x)}</div>"
                         for x in zh_txt.split("\n") if x.strip())
            fx = "".join(f"<div class='catfx'><b>{esc(f['n'])}</b>　{esc(_cut(f['d'], 200))}</div>"
                         for f in (c.get("fx") or []))
            ci = "" if zh_txt.strip() else "".join(
                f"<div class='catci'>{esc(_cut(x, 220))}</div>"
                for x in (c.get("ci") or "").split("\n") if x.strip())
            if not zh and not fx and unlock:
                fx = f"<div class='catfx'>{esc(unlock)}</div>"
                unlock = ""
            blocks += ("<div class='note'><b>" + esc(c.get("n") or "催化") + "</b>"
                       + (f"<span class='catstat'>{esc(stats)}</span>" if stats else "")
                       + zh + fx + ci
                       + (f"<div class='catunlock'>解锁：{esc(unlock)}</div>"
                          if unlock and unlock != "。" else "")
                       + "</div>")
        body += f"<h2>异域催化</h2><div class='notes'>{blocks}</div>"

    if others:
        uniq = _dedupe([{"name": o} for o in others], lambda x: x["name"])
        if uniq:
            body += ("<div class='others dim'>其他匹配：" +
                     "、".join(esc(o["name"]) for o in uniq) + "</div>")
    body += "<div class='foot'>命运2 查询 · 数据来自 Bungie.net / Starside / Clarity</div>"
    return _page(body)


def weapons_list_card(results: list[dict], q: str) -> str:
    """没找到唯一目标时给个带图标的候选列表（小日向式列表卡）"""
    uniq = _dedupe(results, lambda w: (w["name"], w["type"]))
    cards = "".join(
        f"<div class='chip' style='font-size:14px;padding:7px 10px'>"
        f"<span class='wicon' style='width:34px;height:34px'>"
        + (f"<img class='wm' src='{esc(w['watermark'])}'>" if w.get("watermark") else "")
        + f"<img src='{esc(w['icon'])}'></span>"
        f"<b>{esc(w['name'])}</b><span class='dim'>{esc(w['cat'])} · {esc(w['type'])}</span></div>"
        for w in uniq[:12])
    extra = f"（共 {len(results)} 条，含同名不同版本）" if len(uniq) < len(results) else ""
    body = (f"<h1>武器「{esc(q)}」</h1>"
            f"<div class='sub'>找到 {len(uniq)} 个匹配{extra}，把名字打全可以看详情</div>"
            f"{cards}<div class='foot'>命运2 查询 · 数据来自 Bungie.net</div>")
    return _page(body)


# ---------- /武器筛选 ----------

def weapon_filter_card(res: dict, q: str) -> str:
    """筛选结果：小日向式三列网格，每格 图标 + 名称 + 类型·弹药 + 框架。
    同名多版本武器逐条列出，多版本的名字加赛季角标（首发/S 号）区分"""
    items = res.get("items") or []
    multi: dict[str, int] = {}
    for w in items:
        multi[w["n"]] = multi.get(w["n"], 0) + 1
    cells = ""
    for w in items:
        water = f"<img class='wm' src='{esc(w['w'])}'>" if w.get("w") else ""
        icon = f"<img src='{esc(w['i'])}'>" if w.get("i") else ""
        cls = " class='ex'" if w.get("x") else ""
        tag = ""
        if multi.get(w["n"], 0) > 1:
            st = "首发" if w.get("s", 0) <= 0 else f"S{w['s']}"
            tag = f"<span class='dim' style='font-size:11px;margin-left:6px'>{st}</span>"
        cells += (f"<div class='wfcell'><span class='wfico'>{water}{icon}</span>"
                  f"<div class='wftxt'><b{cls}>{esc(w['n'])}</b>{tag}"
                  f"<span>{esc(w['t'])} · {esc(w['a'])}</span>"
                  f"<span class='fr'>{esc(w['f'])}</span></div></div>")

    notes = []
    if res.get("dropped"):
        notes.append(f"已忽略冲突词：<em>{esc('、'.join(res['dropped']))}</em>")
    if res.get("relaxed"):
        notes.append("没有武器同时满足全部关键词，已放宽为 <em>任一关键词命中</em>")
    shown = res.get("shown", len(res.get("items") or []))
    if res.get("total", 0) > shown:
        notes.append(f"共 <em>{res['total']}</em> 把，只展示前 {shown} 把")
    meta = "".join(f"<div class='wfmeta'>{n}</div>" for n in notes)

    body = (f"<h1>武器筛选</h1>"
            f"<div class='sub'>关键词：{esc(q)} · 命中 <b>{res.get('total', 0)}</b> 把</div>"
            f"{meta}<div class='wfgrid'>{cells}</div>"
            f"<div class='foot'>命运2 查询 · 数据来自 Bungie.net</div>")
    return _page(body)


# ---------- /perk查询 ----------

def perk_card(perks: list[dict], q: str = "") -> str:
    # 同名条目（同武器不同赛季版本 / 普通版与强化版）在 Manifest 里是不同 hash 但同名，
    # 精确数值按名字索引，逐条贴会原样重复一遍 —— 这里按名字合并成一张卡。
    groups: dict[str, list[dict]] = {}
    for p in perks:
        lst = groups.setdefault(p["name"], [])
        if not any(v.get("desc") == p.get("desc") for v in lst):
            lst.append(p)

    blocks = ""
    for name, items in list(groups.items())[:3]:
        base = items[0]
        tags = "".join(
            f"<span class='{('up' if v > 0 else 'dn')}'>{esc(n)} {v:+d}</span>"
            for n, v in (base.get("stats") or {}).items())
        # 首条为正文说明，其余同名版本作为变体说明附在后面（去重后通常只有强化版那一条）
        descs = [it.get("desc") or "（无官方说明）" for it in items]
        desc_html = f"<div class='nline'>{esc(descs[0])}</div>" + "".join(
            f"<div class='vnote'><span class='dim'>同名变体</span>　{esc(d)}</div>"
            for d in descs[1:])
        prec = _zh(name) or base.get("ci")
        note = ""
        if prec:
            # 保留换行（.note 是 pre-wrap）：强化版补充说明自成一行，不再挤在正文末尾被截掉
            txt = hl_enh_text(re.sub(r"[ \t]+", " ", str(prec).strip())[:800])
            legend = "<div class='legend'>金色 ↑ 为升金（强化特性）后的数值</div>" if "↑" in prec else ""
            note = f"<div class='note'><b>精确数值</b>　{txt}{legend}</div>"
        blocks += (
            f"<div class='pcol' style='margin:10px 0'>"
            f"<div class='chip' style='font-size:15px;padding:8px 10px'>"
            f"<img src='{esc(base.get('icon', ''))}' style='width:34px;height:34px'>"
            f"<b>{esc(name)}</b></div>"
            + (f"<div class='ptags'>{tags}</div>" if tags else "")
            + desc_html + note + "</div>")
    title = f"Perk「{esc(q)}」" if q else "Perk 查询"
    body = (f"<h1>{title}</h1><div class='sub'>官方说明 + 中文精确数值</div>{blocks}"
            f"<div class='foot'>数据来自 Bungie.net / Starside / Clarity</div>")
    return _page(body)


# ---------- /护甲套装（Starside 中文套装效果） ----------

def _hl_nums(text: str) -> str:
    """把套装效果里的数值/百分比加粗（先转义再匹配，数字不受转义影响）"""
    return re.sub(r"([+\-]?\d[\d.?]*[%×]?|\[\?\])", r"<b>\1</b>", esc(text))


def armor_set_card(s: dict) -> str:
    """单个套装：2 件 / 4 件效果全文 + 数值"""
    tags = "".join(f"<span class='as-tag'>{esc(t)}</span>" for t in s.get("tags", []))
    src = esc(s.get("source") or "")
    body = (f"<h1>{esc(s['name'])}</h1>"
            f"<div class='sub'>{esc(s['category'])}"
            + (f" · 来源 {src}" if src else "") + f"　{tags}</div>")
    for b in s["bonuses"]:
        body += (f"<div class='as-bonus'><div class='as-head'>"
                 f"<span class='as-piece'>{esc(b['piece'])}</span>"
                 f"<b>{esc(b['name'])}</b></div>"
                 f"<div class='as-text'>{_hl_nums(b['text'])}</div></div>")
    body += "<div class='foot'>数据来自 Starside 中文护甲套装资料 · [?] 为待核实 / 方括号为 PvP 数值</div>"
    return _page(body)


def armor_sets_card(sets: list[dict], q: str = "") -> str:
    """多套 / 全部套装：按类别分组的索引（每套列出 2 件、4 件效果名）"""
    title = f"护甲套装「{esc(q)}」" if q else "护甲套装效果一览"
    body = (f"<h1>{title}</h1>"
            f"<div class='sub'>共 {len(sets)} 套 · 发 <code>/护甲套装 套装名</code> 看单套完整数值"
            "（支持别名：炽天使套 / 一愿 / vog / kf …）</div>")
    cats: dict[str, list[dict]] = {}
    for s in sets:
        cats.setdefault(s["category"], []).append(s)
    for cat, items in cats.items():
        body += f"<div class='evhead'>{esc(cat)}<span>{len(items)} 套</span></div>"
        for s in items:
            bn = "　".join(f"{b['piece']} {b['name']}" for b in s["bonuses"][:2])
            src = esc(s.get("source") or "")
            tags = "".join(f"<span class='as-tag'>{esc(t)}</span>"
                           for t in s.get("tags", [])[:4])
            body += ("<div class='as-row'><div class='as-rowtop'>"
                     f"<b>{esc(s['name'])}</b>"
                     + (f"<span class='as-src'>· {src}</span>" if src else "")
                     + f"<span class='as-tags'>{tags}</span></div>"
                     f"<div class='as-bn'>{esc(bn)}</div></div>")
    body += "<div class='foot'>数据来自 Starside 中文护甲套装资料</div>"
    return _page(body)


def armor_sets_full_card(sets: list[dict]) -> str:
    """网页端全量详情版：所有套装的 2/4 件效果全文照搬 Starside（页面可长，QQ 卡片仍用索引版）"""
    body = ("<h1>护甲套装效果一览</h1>"
            f"<div class='sub'>共 {len(sets)} 套 · 2/4 件效果全文与数值，照搬 Starside 中文资料"
            "　[?] 为待核实 / 方括号为 PvP 数值</div>")
    cats: dict[str, list[dict]] = {}
    for s in sets:
        cats.setdefault(s["category"], []).append(s)
    for cat, items in cats.items():
        body += f"<div class='evhead'>{esc(cat)}<span>{len(items)} 套</span></div>"
        for s in items:
            tags = "".join(f"<span class='as-tag'>{esc(t)}</span>" for t in s.get("tags", []))
            src = esc(s.get("source") or "")
            body += (f"<div class='as-set'><h2>{esc(s['name'])}</h2>"
                     f"<div class='as-meta'>{esc(cat)}"
                     + (f" · 来源 {src}" if src else "") + f"　{tags}</div>")
            for b in s["bonuses"]:
                body += (f"<div class='as-bonus'><div class='as-head'>"
                         f"<span class='as-piece'>{esc(b['piece'])}</span>"
                         f"<b>{esc(b['name'])}</b></div>"
                         f"<div class='as-text'>{_hl_nums(b['text'])}</div></div>")
            body += "</div>"
    body += "<div class='foot'>数据来自 Starside 中文护甲套装资料</div>"
    return _page(body)


# ---------- 战绩类卡片：直接复用查询站(webui.py)的排版 ----------
# 这些页面（副本徽章 / 称号镀金 / 热力图 / 常用武器排名 …）排版已与网页端一致，
# 机器人没必要重写一遍：webui 与本插件同进程，取它的 render_* 出 HTML 即可。

def _webui():
    """同进程里的 webui 模块。

    exe 由 d2query_launcher 以模块名 `webui` 导入；源码直跑 `python webui.py` 时它叫 `__main__`；
    都没有（如单跑 bot.py）再按模块名导入一次（只建对象、不会起服务）。
    """
    for key in ("webui", "__main__"):
        mod = sys.modules.get(key)
        if mod is not None and hasattr(mod, "render_raid_card"):
            return mod
    import webui
    return webui


def raid_card(rep: dict, title: str, name: str = "", amode: int = 4) -> str:
    """/raid、/地牢：副本徽章（通关/无暇/大师/单人双人三人/无暇）"""
    return _webui().render_raid_card(rep, title, name, amode)


def nodes_card(rep: dict) -> str:
    """/称号、/锻造：按分组网格 + 每条进度条（称号带镀金标记）"""
    return _webui().render_nodes(rep)


def history_card(rep: dict) -> str:
    """/历史：最近对局流"""
    return _webui().render_history_card(rep)


def mode_card(rep: dict, title: str, mode: str, lifetime: dict | None = None,
              lifetime_extra: tuple = ()) -> str:
    """/pvp、/pve、/智谋：生涯统计 + 近期战绩 + 模式细分"""
    return _webui().render_match_card(rep, title, mode, lifetime, tuple(lifetime_extra))


def wpvp_card(rep: dict) -> str:
    """/常用武器：PVP 生涯武器排名"""
    return _webui().render_wpvp(rep)


def wpve_card(rep: dict) -> str:
    """/pve生涯武器：PVE 生涯武器排名（默认当前赛季）"""
    return _webui().render_wpvp(rep)


def gm_card(rep: dict) -> str:
    """/宗师：宗师征服 + 宗师警戒战绩"""
    return _webui().render_gm(rep)


def heat_card(rep: dict) -> str:
    """/热力图：按赛季分组的全历史活跃日历"""
    return _webui().render_heat(rep)


# ---------- /每日光尘（Eververse 光尘商店） ----------

_TIER_CLS = {"异域": "t5", "传说": "t6", "稀有": "t4", "基本": "t1"}


# ---------- /轮换（本周突袭 & 地牢） ----------

def rotation_card(rot: dict) -> str:
    """本周轮换卡片：突袭①② / 地牢①②，各配一张活动横图（pgcr 大图）。"""
    raids = rot.get("raids") or []
    dungeons = rot.get("dungeons") or []
    if not raids and not dungeons:
        return notice("暂时拿不到本周轮换",
                      ["Bungie 里程碑接口没有返回可识别的突袭/地牢，稍后再试。"], kind="warn")
    body = ("<h1>本周轮换 · 突袭 &amp; 地牢</h1>"
            f"<div class='sub'>{esc(rot.get('label') or '本周')} · 每周三凌晨 1 点刷新</div>")
    for title, names, tag in (("突袭", raids, "突袭"), ("地牢", dungeons, "地牢")):
        if not names:
            continue
        body += f"<div class='evhead'>{title}<span>{len(names)} 个</span></div>"
        cards = ""
        for i, nm in enumerate(names, 1):
            rec = d2.rot_activity(nm)
            shot = rec.get("pgcr") or ""
            style = (f" style=\"background-image:url('{esc(shot)}')\""
                     if shot and "missing_icon" not in shot else "")
            cards += ("<div class='evbig'" + style + ">"
                      "<div class='scrim'></div>"
                      f"<span class='evtier'>{tag} {i}</span>"
                      "<div class='evbigg'><div class='who'>"
                      f"<b>{esc(nm)}</b></div></div></div>")
        body += f"<div class='evhero'>{cards}</div>"
    body += ("<div class='foot'>数据来自 Bungie.net / Starside"
             + ("" if rot.get("matched") else " · 配对表没对上，只列出了官方周常突袭")
             + "</div>")
    return _page(body)


def _cur_img(url: str) -> str:
    return f"<img src='{esc(url)}'>" if url else ""


def eververse_card(store: dict) -> str:
    """光尘商店卡片：按游戏内「主要光尘优惠 / 其他光尘优惠 / 银币优惠」三节展示。

    武器皮肤与三职业护甲皮肤用游戏里点开物品的那张竖版大图（screenshot）当背景，
    其余的（表情/机灵/飞船/快雀/着色器/传送特效/包裹）只出方形缩略图。
    """
    secs = store.get("sections") or []
    if not secs:
        return notice("光尘商店暂无数据",
                      ["数据源今天没有返回内容，稍后再试。"], kind="warn")
    total = sum(len(s["items"]) for s in secs)
    day = store.get("day") or "今日"
    body = ("<h1>光尘商店 · Eververse</h1>"
            f"<div class='sub'>{esc(day)} 这一轮上架 · 每天凌晨 1 点刷新 · "
            "皮肤 / 飞船 / 载具出大图，其余缩略图</div>")
    for sec in secs:
        items = sec["items"]
        cico = _cur_img(d2.ev_cur_icon(sec["cur"]))
        body += (f"<div class='evhead'>{esc(sec['name'])}"
                 f"<span>{len(items)} 件 · {esc(sec['cur'])}</span></div>")
        big, small = [], []
        for it in items:
            (big if (it.get("big") and it.get("shot")) else small).append(it)
        if big:
            cards = ""
            for it in big:
                tcls = _TIER_CLS.get(it["tier"], "t1")
                cards += ("<div class='evbig' "
                          f"style=\"background-image:url('{esc(it['shot'])}')\">"
                          "<div class='scrim'></div>"
                          f"<span class='evtier {tcls}'>{esc(it['tier'])}</span>"
                          "<div class='evbigg'><div class='who'>"
                          f"<b>{esc(it['n'])}</b><span class='ty'>{esc(it['ty'])}</span></div>"
                          f"<div class='evcost'>{cico}{it['cost']:,}</div></div></div>")
            body += f"<div class='evhero{' one' if len(big) == 1 else ''}'>{cards}</div>"
        if small:
            cards = ""
            for it in small:
                tcls = _TIER_CLS.get(it["tier"], "t1")
                cost = f"{it['cost']:,}" if it["cost"] else "-"
                thumb = (f"<img src='{esc(it['icon'])}'>" if it["icon"]
                         else f"<em class='noi'>{esc(_cut(it['ty'] or '商店物品', 6))}</em>")
                cards += ("<div class='evcard'>"
                          f"<span class='evico {tcls}'>{thumb}</span>"
                          "<div class='evmeta'>"
                          f"<span class='evrar {tcls}'>{esc(it['tier'])}</span>"
                          f"<b>{esc(it['n'])}</b>"
                          f"<span class='evsub'>{esc(it['ty'])}</span></div>"
                          f"<div class='evcost'>{cico}{cost}</div></div>")
            body += f"<div class='evgrid'>{cards}</div>"
    body += (f"<div class='foot'>共 {total} 件 · 当前上架（Bungie 商店接口，需账号授权）"
             " · 数字单位：光尘</div>")
    return _page(body)
