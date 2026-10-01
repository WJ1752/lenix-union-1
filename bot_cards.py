"""小日向式图片卡片排版：把查询结果拼成 HTML（交给 card_render 截图成 PNG）

配色/圆角/字体对齐 webui.py 的 CARD_CSS，保证机器人的图片与网页查询站同一套视觉：
深色卡片 + 蓝色分组标题 + 金色数值 + 绿/红增减标签。
每个 build_* 返回完整 HTML 文档字符串。
"""
from __future__ import annotations

import json
import math
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
/* ===== 武器卡片 v2：900px 深色改版 =====
   仅 weapon_card 注入 body.pgw / .card.pgwc（见 _page_weapon），不影响其它卡片。
   配色：外底 #0f1113，面板 #16181b/#1b1e22，描边 #2a2e33，正文 #e8e6e3，
        弱化 #9aa0a6，绿 #35c66b，金 #d4b26a。 */
body.pgw{width:900px;background:#0f1113;color:#e8e6e3;padding:12px}
.card.pgwc{background:none;border:none;border-radius:0;box-shadow:none;padding:0;
           display:flex;flex-direction:column;gap:10px}
/* 1. 头部横幅：赛季水印整张低透明叠加 + 右侧武器 screenshot 透底大图 */
.w2-head{position:relative;height:230px;flex-shrink:0;border:1px solid #2a2e33;border-radius:12px;
         background:linear-gradient(115deg,#1b1e22 0%,#141619 52%,#0f1113 100%);overflow:hidden}
.w2-wm{position:absolute;inset:0;opacity:.15;background-repeat:repeat-x;background-position:0 center;
       background-size:206px auto;pointer-events:none}
.w2-shot{position:absolute;right:-12px;top:50%;transform:translateY(-50%);height:220px;max-width:60%;
         -webkit-mask-image:linear-gradient(90deg,transparent,#000 32%);
         mask-image:linear-gradient(90deg,transparent,#000 32%)}
.w2-headtxt{position:absolute;left:24px;top:50%;transform:translateY(-50%);z-index:3;max-width:52%}
.w2-meta{font-size:12.5px;color:#c2c7cc;letter-spacing:.5px}
.w2-title{font-size:42px;font-weight:700;color:#fff;line-height:1.16;margin:4px 0 6px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 2px 12px rgba(0,0,0,.65)}
.w2-sub{font-size:20px;color:#9aa0a6}
.w2-season{font-size:14px;color:#6d737b;margin-top:5px}
/* 2. 特长横条 */
.w2-int{display:flex;align-items:center;gap:14px;background:#16181b;border:1px solid #2a2e33;
        border-radius:12px;padding:10px 16px}
.w2-intico{width:56px;height:56px;flex-shrink:0;background:#0f1113;border:1px solid #2a2e33;
           border-radius:9px;display:flex;align-items:center;justify-content:center}
.w2-intico img,.w2-intico .w2-noico{width:44px;height:44px;object-fit:contain}
.w2-inttx{min-width:0;flex:1}
.w2-intname{display:block;font-size:22px;font-weight:700;color:#d4b26a;line-height:1.25}
.w2-intdesc{display:block;font-size:15px;color:#9aa0a6;margin-top:2px;white-space:nowrap;
            overflow:hidden;text-overflow:ellipsis}
/* 3. 版本胶囊 */
.w2-vers{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.w2-pill{display:inline-flex;align-items:center;gap:6px;background:#16181b;border:1px solid #2a2e33;
         border-radius:999px;padding:4px 12px;font-size:13px;color:#9aa0a6;max-width:250px;white-space:nowrap}
.w2-pill .nm{font-size:12px;color:#6d737b;overflow:hidden;text-overflow:ellipsis}
.w2-pill.on{border-color:#35c66b;background:rgba(53,198,107,.08);color:#d7f5e2}
.w2-pill.on .nm{color:#9ad4ae}
.w2-cur{background:#35c66b;color:#0b1a10;font-size:10.5px;font-weight:700;border-radius:999px;padding:1px 7px}
.w2-vhint{margin-left:auto;font-size:11px;color:#5c626a}
/* 4. 主体两块：左素体数值 / 右热门组合 */
.w2-main{display:flex;gap:10px;align-items:stretch}
.w2-main.solo .w2-stats{flex:1;width:auto}
.w2-panel{background:#16181b;border:1px solid #2a2e33;border-radius:12px}
.w2-blocktitle{font-size:12px;color:#6d737b;letter-spacing:2px;margin-bottom:10px}
.w2-stats{width:240px;flex-shrink:0;padding:12px 14px}
.w2-srow{height:37px;position:relative}
.w2-srow .top{display:flex;justify-content:space-between;align-items:center;height:20px}
.w2-srow .top span{font-size:13px;color:#9aa0a6}
.w2-srow .top b{font-size:15px;color:#e8e6e3;display:inline-flex;align-items:center;gap:5px}
.w2-sbar{position:absolute;left:0;right:0;bottom:3px;height:5px;background:#1b1e22;border-radius:3px;overflow:hidden}
.w2-sbar u{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,#2c9c58,#35c66b)}
.w2-sdiv{height:1px;background:#24282c;margin:10px 0 7px}
.w2-splain2{height:27px;display:flex;justify-content:flex-end;align-items:center;gap:10px}
.w2-splain2 span{font-size:13px;color:#9aa0a6}
.w2-splain2 b{font-size:15px;color:#e8e6e3;display:inline-flex;align-items:center;gap:5px}
.w2-rec{display:block;flex-shrink:0}
.w2-combos{flex:1;min-width:0;padding:12px 14px;display:flex;flex-direction:column}
.w2-cgrid{flex:1;display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:1fr;gap:8px;align-content:center}
.w2-cell{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;
         background:#1b1e22;border:1px solid #2a2e33;border-radius:10px;padding:8px 6px;min-width:0}
.w2-cell.on{border-color:#35c66b;background:rgba(53,198,107,.09)}
.w2-pair{display:flex;gap:8px;align-items:flex-start}
.w2-pair .pk{display:flex;flex-direction:column;align-items:center;gap:3px;width:60px;min-width:0}
.w2-pair img,.w2-pair .w2-noico{width:34px;height:34px;object-fit:contain}
.w2-pair .pk span{font-size:12px;color:#c5cacd;line-height:1.25;text-align:center;
                  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.w2-pct{font-size:22px;font-weight:700;color:#35c66b;line-height:1}
.w2-cempty{flex:1;min-height:140px;display:flex;align-items:center;justify-content:center;
           border:1px dashed #2a2e33;border-radius:10px;color:#5c626a;font-size:13px}
/* 5. perk 分列（列色：发射管蓝/弹匣琥珀/特性1绿/特性2紫/起源橙红） */
.w2-pcols{display:grid;gap:10px}
.w2-pcol{background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:10px 9px;min-width:0}
.w2-ctag{display:block;text-align:center;font-size:12px;font-weight:700;border-radius:999px;
         padding:3px 0;margin-bottom:9px;background:rgba(154,160,166,.14);color:#9aa0a6}
.w2-prow{display:flex;align-items:center;gap:7px;padding:3px 5px;border-radius:7px}
.w2-prow+.w2-prow{margin-top:2px}
.w2-prow img,.w2-prow .w2-noico{width:30px;height:30px;object-fit:contain;flex-shrink:0;border-radius:5px}
.w2-prow .tx{min-width:0;line-height:1.3}
.w2-prow .nm{display:block;font-size:13px;color:#e8e6e3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.w2-prow .pc{display:block;font-size:15px;font-weight:700;color:#e8e6e3}
/* 6/7. 大师杰作 / 武器模组行 */
.w2-acc{display:flex;align-items:center;gap:12px;background:#16181b;border:1px solid #2a2e33;
        border-radius:12px;padding:8px 14px}
.w2-acclabel{writing-mode:vertical-rl;text-orientation:upright;font-size:11px;color:#9aa0a6;
             letter-spacing:4px;flex-shrink:0}
.w2-accgrid{flex:1;display:grid;gap:8px}
.w2-acccell{display:flex;align-items:center;gap:9px;background:#1b1e22;border:1px solid #2a2e33;
            border-radius:9px;padding:6px 10px;min-width:0}
.w2-acccell img,.w2-acccell .w2-noico{width:30px;height:30px;object-fit:contain;flex-shrink:0}
.w2-acccell .tx{min-width:0;line-height:1.3}
.w2-acccell .tt{display:block;font-size:12px;color:#9aa0a6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.w2-acccell .pc{display:block;font-size:16px;font-weight:700;color:#e8e6e3}
/* 8. 异域催化：旧数据结构换肤 */
.w2-sec{font-size:12px;color:#6d737b;letter-spacing:2px}
body.pgw .note{background:#16181b;border:1px solid #2a2e33;border-radius:10px}
body.pgw .note b{color:#d4b26a}
body.pgw .catstat{color:#d4b26a}
body.pgw .catfx b{color:#9b6bd4}
body.pgw .catci{color:#6d737b;border-top-color:#2a2e33}
body.pgw .catunlock{color:#6d737b}
body.pgw .enh{color:#d4b26a;background:rgba(212,178,106,.16)}
.w2-noico{display:inline-block;background:#22262b;border-radius:5px;flex-shrink:0}
/* 其他匹配 + 9. 页脚 */
.w2-others{font-size:11px;color:#5c626a}
.w2-foot{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #2a2e33;
         padding-top:8px;font-size:11px;color:#5c626a}
/* ===== 异域护甲卡（armor_card）：w2 家族 900px 深色版式，配色与武器卡一致 ===== */
.a2-q{font-style:normal;font-weight:700;color:#d4b26a;letter-spacing:1px}
.a2-en{color:#6d737b;margin-left:8px}
.a2-icon{position:absolute;right:26px;top:50%;transform:translateY(-50%);width:168px;height:168px;
         display:flex;align-items:center;justify-content:center;z-index:2}
.a2-icon::before{content:"";position:absolute;inset:-30px;border-radius:50%;
                 background:radial-gradient(circle,rgba(212,178,106,.16),rgba(212,178,106,0) 66%)}
.a2-icon img{position:relative;width:156px;height:156px;object-fit:contain;
             filter:drop-shadow(0 8px 18px rgba(0,0,0,.55))}
.a2-flav{position:absolute;left:24px;bottom:16px;right:210px;font-size:12.5px;color:#6d737b;
         font-style:italic;line-height:1.55;z-index:3;
         display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
/* 异域特性金名横条：描述完整展开不截断 */
.a2-int{border-left:3px solid #d4b26a}
.a2-intdesc{display:block;font-size:15px;color:#b8bdc2;line-height:1.8;margin-top:3px;white-space:normal}
.a2-intdesc+.a2-intdesc{margin-top:4px}
/* 职业金：两列特性池（列内 图标+名称+完整描述）+ 组合一览 */
.a2-perk{display:flex;align-items:flex-start;gap:9px;padding:6px 2px}
.a2-perk+.a2-perk{border-top:1px dashed #262a2e}
.a2-perk img,.a2-perk .w2-noico{width:32px;height:32px;object-fit:contain;flex-shrink:0;margin-top:2px}
.a2-perk .tx{min-width:0;flex:1}
.a2-perk .nm{display:block;font-size:14px;font-weight:700;color:#e8e6e3;line-height:1.4}
.a2-perk .ds{display:block;font-size:12.5px;color:#9aa0a6;line-height:1.65;margin-top:2px;white-space:normal}
.a2-hint{font-size:12px;color:#5c626a}
.a2-hint b{color:#d4b26a;font-weight:700}
.a2-cgrid2{display:grid;grid-template-columns:1fr 1fr;gap:6px 10px}
.a2-combo{display:flex;align-items:center;gap:8px;background:#1b1e22;border:1px solid #2a2e33;
          border-radius:8px;padding:5px 10px;font-size:13px;min-width:0}
.a2-combo .l,.a2-combo .r{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.a2-combo .l{color:#7fd49c}.a2-combo .r{color:#c3a6e8}.a2-combo .pl{color:#5c626a}
/* 社区称呼 / 候选列表 */
.a2-alias{font-size:12px;color:#5c626a}
.a2-alias b{color:#9aa0a6;font-weight:600}
.a2-cands{display:flex;flex-direction:column;gap:8px}
.a2-cand{display:flex;align-items:center;gap:12px;background:#16181b;border:1px solid #2a2e33;
         border-radius:10px;padding:8px 14px;min-width:0}
.a2-cand>img,.a2-cand>.noi{width:46px;height:46px;object-fit:contain;flex-shrink:0}
.a2-cand>.noi{display:inline-block;background:#22262b;border-radius:5px}
.a2-cand .tx{min-width:0;flex:1;line-height:1.3}
.a2-cand .nm{display:block;font-size:16px;font-weight:700;color:#d4b26a}
.a2-cand .pk{display:block;font-size:12.5px;color:#6d737b;white-space:nowrap;overflow:hidden;
             text-overflow:ellipsis;margin-top:1px}
.a2-cand .mt{font-size:12.5px;color:#9aa0a6;flex-shrink:0}
/* ===== /轮换 · 扭曲星球板块（w2-* 深色同款：面板 #16181b，描边 #2a2e33，绿 #35c66b，金 #d4b26a） ===== */
.rotdist{background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:12px 14px;margin-top:4px}
.rd-now{display:flex;align-items:center;justify-content:space-between;gap:12px;
        background:rgba(53,198,107,.09);border:1px solid #35c66b;border-radius:10px;padding:10px 14px}
.rd-k{display:block;font-size:12px;color:#9ad4ae;letter-spacing:1px}
.rd-dest{display:block;font-size:22px;font-weight:700;color:#35c66b;line-height:1.25;margin-top:2px}
.rd-next{text-align:right;flex-shrink:0}
.rd-next b{font-size:15px;color:#d4b26a}
.rd-next span{display:block;font-size:12px;color:#9aa0a6;margin-top:2px}
.rd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px;margin-top:10px}
.rd-cell{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
         background:#1b1e22;border:1px solid #2a2e33;border-radius:10px;padding:8px 4px;min-width:0}
.rd-cell span{font-size:11px;color:#6d737b}
.rd-cell b{font-size:13px;color:#e8e6e3;white-space:nowrap}
.rd-cell.on{border-color:#35c66b;background:rgba(53,198,107,.09)}
.rd-cell.on span{color:#9ad4ae}
.rd-cell.on b{color:#35c66b}
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
        cat("资料", f"{c('/武器查询 武器名')} {c('/perk查询 perk名')} {c('/护甲查询 护甲名')} {c('/护甲套装')} {c('/每日光尘')} {c('/轮换')}") +
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
# 按游戏内/DIM 面板顺序：射速/充能在前，随后冲击、爆炸范围、弹头速度等武器特有项
STAT_ORDER = ["每分钟发射数", "射击速度", "充能时间", "蓄力时间", "蓄能时间", "充能速度",
              "冲击", "爆炸范围", "弹头速度", "射程", "稳定性", "操控性", "填装速度", "弹匣",
              "辅助瞄准", "变焦", "后坐方向", "空中效率", "弹药生成",
              "挥舞速度", "护盾穿透", "举盾速度", "精度", "伤害"]

# 游戏内面板底栏的数量型/仪表型属性：射速、弹匣、后坐方向等，只写数字不画条
# （其余属性全部画条，含变焦/弹药生成；顺序见 STAT_ORDER）
W2_PLAIN_BOTTOM = {"每分钟发射数", "射击速度", "弹匣", "弹药容量",
                   "充能时间", "蓄力时间", "蓄能时间", "充能速度", "后坐方向"}


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


_WCAT = None


def _wcat(hash_: str) -> dict:
    """weapon_catalog.json 懒加载查询（品质 q / 伤害属性 e），供武器卡片头部 meta 用"""
    global _WCAT
    if _WCAT is None:
        try:
            _WCAT = json.load(open(d2._idx_file("weapon_catalog.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _WCAT = {}
    return _WCAT.get(str(hash_ or "")) or {}


# 赛季→年份（按 d2ai d2-season-info 发布日期与资料片分界推算：Y7=S24-26、Y8=S27 起）
SEASON_YEAR = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 3, 12: 4,
               13: 4, 14: 4, 15: 4, 16: 5, 17: 5, 18: 5, 19: 5, 20: 6, 21: 6, 22: 6, 23: 6,
               24: 7, 25: 7, 26: 7, 27: 8, 28: 8}

_W2_TIER = {2: "普通", 3: "罕见", 4: "稀有", 5: "传说", 6: "异域"}
# perk 分列列色（发射管蓝 / 弹匣琥珀 / 特性1绿 / 特性2紫 / 起源橙红），按列序号取用
_W2_PAL = ["#4b8fd4", "#c9a227", "#35c66b", "#9b6bd4", "#d0603f"]


def _recoil_gauge(v) -> str:
    """游戏内同款后坐方向半圆仪表：自右端起按 v/100 逆时针填充实心扇形，
    100=满半圆（完全竖直），缺口方向即水平漂移方向。"""
    try:
        v = max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return ""
    th = math.radians(v / 100 * 180)
    x1 = 13 - 11 * math.cos(th)
    y1 = 13 - 11 * math.sin(th)
    return ("<svg class='w2-rec' viewBox='0 0 26 14' width='26' height='14'>"
            "<path d='M2 13A11 11 0 0 1 24 13Z' fill='#24282c'/>"
            f"<path d='M13 13L24 13A11 11 0 0 0 {x1:.2f} {y1:.2f}Z' fill='#e8e6e3'/></svg>")


def _mw_name(name) -> str:
    """大师杰作插头名自带「大师杰作：」前缀，渲染时剥掉避免双前缀"""
    return re.sub(r"^大师杰作[：:]\s*", "", str(name or ""))


def _pct(v) -> str:
    """使用率文本：24.0 → 24%，20.4 → 20.4%，取不到 → 空串"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{f:.1f}%".replace(".0%", "%")


def _ico(url: str) -> str:
    url = url or ""
    return f"<img src='{esc(url)}'>" if url else "<i class='w2-noico'></i>"


def _page_weapon(body: str) -> str:
    """weapon_card 专用页面：复用公共 CSS，只把 body/card 换成 900px 深色版的类"""
    return (CSS.replace("__BODY__", body)
               .replace('<body><div class="card">',
                        '<body class="pgw"><div class="card pgwc">'))


def weapon_card(w: dict, others: list[str] | None = None,
                vers: list[str] | None = None, ver_cur: int = 0,
                ver_names: list[str] | None = None,
                usage: dict | None = None) -> str:
    """武器查询卡（900px 深色改版）。

    usage：light.gg 社区使用率快照（结构见 README / 调用方），全部字段可缺；
    缺 cols 时 perk 区降级为 plugs.cols（无百分比），缺 combos 时组合区显示占位，
    masterworks / mods 为空则整行隐藏。
    """
    pl = w.get("plugs", {})
    usage = usage if isinstance(usage, dict) else {}
    cat_def = _wcat(w.get("hash") or "")

    # ---- 1. 头部横幅：meta（品质/伤害属性/弹药）+ 武器名 + 类型·槽位 + 赛季行 ----
    meta = " / ".join(x for x in (_W2_TIER.get(cat_def.get("q") or 0),
                                  cat_def.get("e"), w.get("ammo")) if x)
    slot = (w.get("cat") or "") + "武器" if w.get("cat") else ""
    sub = " · ".join(x for x in (w.get("type"), slot) if x)
    head = "<div class='w2-head'>"
    if w.get("watermark"):
        head += f"<div class='w2-wm' style=\"background-image:url('{esc(w['watermark'])}')\"></div>"
    shot = w.get("screenshot") or w.get("icon") or ""
    if shot:
        head += f"<img class='w2-shot' src='{esc(shot)}'>"
    head += "<div class='w2-headtxt'>"
    if meta:
        head += f"<div class='w2-meta'>{esc(meta)}</div>"
    head += f"<div class='w2-title'>{esc(w['name'])}</div>"
    if sub:
        head += f"<div class='w2-sub'>{esc(sub)}</div>"
    # 赛季行：沿用版本数据的赛季文案（S 号 → 年/赛季号，英文名照搬）
    if vers:
        idx = ver_cur if 1 <= ver_cur <= len(vers) else len(vers)
        names = ver_names or [""] * len(vers)
        tag = vers[idx - 1] or ""
        nm = names[idx - 1] if idx - 1 < len(names) else ""
        m = re.search(r"\d+", tag)
        if m:
            n = int(m.group())
            year = SEASON_YEAR.get(n) or max(SEASON_YEAR.values()) + max(0, (n - max(SEASON_YEAR) + 2) // 3)
            season = f"年 {year} 第 {n} 赛季" + (f" · {nm}" if nm else "")
        else:
            season = " · ".join(x for x in (tag, nm) if x)
        head += f"<div class='w2-season'>{esc(season)}</div>"
    head += "</div></div>"

    # ---- 2. 特长（intrinsic）横条 ----
    intr = (pl.get("intrinsic") or [{}])[0]
    body = head
    if intr.get("n") or intr.get("i"):
        desc = _cut(intr.get("d") or "", 110)
        body += ("<div class='w2-int'><span class='w2-intico'>" + _ico(intr.get("i"))
                 + f"</span><span class='w2-inttx'><span class='w2-intname'>{esc(intr.get('n') or '')}</span>"
                 + (f"<span class='w2-intdesc'>{esc(desc)}</span>" if desc else "")
                 + "</span></div>")

    # ---- 3. 版本选择条：#N 赛季文案 胶囊，当前版本绿描边 + 「当前」徽标 ----
    if vers and len(vers) > 1:
        names = ver_names or [""] * len(vers)
        pills = ""
        for i, tag in enumerate(vers, 1):
            nm = names[i - 1] if i - 1 < len(names) else ""
            on = i == ver_cur
            pills += (f"<span class='w2-pill{' on' if on else ''}'>#{i} {esc(tag)}"
                      + (f"<span class='nm'>{esc(nm)}</span>" if nm else "")
                      + ("<span class='w2-cur'>当前</span>" if on else "") + "</span>")
        body += ("<div class='w2-vers'>" + pills
                 + f"<span class='w2-vhint'>其它版本：/武器查询 {esc(w['name'])} 序号</span></div>")

    # ---- 4. 主体两块：左素体数值（240px），右热门组合 2×4 ----
    stats = _stat_sorted(w.get("stats") or [])
    stats_html = ""
    if stats:
        # 变焦/弹药生成量纲不在 0–100 内，设计稿要求只显示数字，不画条
        bottom = W2_PLAIN_BOTTOM
        bars = [s for s in stats if s["n"] not in bottom]
        plains = [s for s in stats if s["n"] in bottom]
        rows = ""
        for s in bars:
            vw = max(0, min(100, int(s["v"])))
            rows += ("<div class='w2-srow'><div class='top'>"
                     f"<span>{esc(s['n'])}</span><b>{s['v']}</b></div>"
                     f"<div class='w2-sbar'><u style='width:{vw}%'></u></div></div>")
        if plains:
            rows += "<div class='w2-sdiv'></div>"
            for s in plains:
                tail = _recoil_gauge(s["v"]) if s["n"] == "后坐方向" else ""
                rows += (f"<div class='w2-splain2'><span>{esc(s['n'])}</span>"
                         f"<b>{s['v']}{tail}</b></div>")
        stats_html = ("<div class='w2-panel w2-stats'><div class='w2-blocktitle'>素体数值</div>"
                      + rows + "</div>")
    combos = usage.get("combos") or []
    is_exotic = (_wcat(str(w.get("hash") or "")).get("q") == 6
                 or bool((w.get("plugs") or {}).get("catalysts")))
    if combos:
        cells = ""
        for j, c in enumerate(combos[:8]):
            cnames = list(c.get("names") or [])[:2]
            cicons = list(c.get("icons") or [])[:2]
            cnames += [""] * (2 - len(cnames))
            cicons += [""] * (2 - len(cicons))
            pair = "".join(f"<span class='pk'>{_ico(cicons[k])}<span>{esc(cnames[k])}</span></span>"
                           for k in (0, 1))
            pct = _pct(c.get("pct"))
            cells += (f"<div class='w2-cell{' on' if j == 0 else ''}'>"
                      f"<div class='w2-pair'>{pair}</div>"
                      + (f"<div class='w2-pct'>{pct}</div>" if pct else "") + "</div>")
        combo_html = f"<div class='w2-cgrid'>{cells}</div>"
        combo_panel = ("<div class='w2-panel w2-combos'><div class='w2-blocktitle'>热门组合</div>"
                       + combo_html + "</div>")
    elif is_exotic:
        combo_panel = ""  # 非锻造异域：不显示使用率区，素体数值拉通全宽
    else:
        combo_panel = ("<div class='w2-panel w2-combos'><div class='w2-blocktitle'>热门组合</div>"
                       "<div class='w2-cempty'>暂无社区使用率数据</div></div>")
    body += (f"<div class='w2-main{' solo' if not combo_panel else ''}'>" + stats_html
             + combo_panel + "</div>")

    # ---- 5. perk 分列：usage.cols 优先（带列色/百分比/第一名高亮），否则 plugs.cols 兜底 ----
    ucols = usage.get("cols") or []
    col_items = []
    if ucols:
        for i, c in enumerate(ucols):
            color = _W2_PAL[i % len(_W2_PAL)]
            rows = ""
            for j, p in enumerate((c.get("plugs") or [])[:8]):
                first = j == 0 and p.get("pct") is not None
                st = f" style='background:{color}26'" if first else ""
                pct = _pct(p.get("pct"))
                pc = ""
                if pct:
                    pc = (f"<span class='pc' style='color:{color}'>{pct}</span>" if first
                          else f"<span class='pc'>{pct}</span>")
                rows += (f"<div class='w2-prow'{st}>" + _ico(p.get("icon") or p.get("i"))
                         + f"<span class='tx'><span class='nm'>{esc(p.get('name') or p.get('n') or '')}</span>"
                         + pc + "</span></div>")
            col_items.append(
                "<div class='w2-pcol'><span class='w2-ctag' "
                f"style='color:{color};background:{color}26'>{esc(c.get('title') or '')}</span>"
                + rows + "</div>")
    else:
        # 索引里已按游戏内 socket 顺序排好（plugs.cols），照搬即可；
        # 兜底列顺序（枪管→弹匣→特性→起源→枪托）仅老数据用
        cols = pl.get("cols") or _plugs_cols(pl)
        for c in cols or []:
            rows = "".join(
                f"<div class='w2-prow'>" + _ico(p.get("i"))
                + f"<span class='tx'><span class='nm'>{esc(p.get('n') or '')}</span></span></div>"
                for p in (c.get("items") or [])[:8])
            col_items.append(f"<div class='w2-pcol'><span class='w2-ctag'>{esc(c['t'])}</span>"
                             + rows + "</div>")
    if col_items:
        body += ("<div class='w2-pcols' style='grid-template-columns:repeat(%d,1fr)'>"
                 % len(col_items)) + "".join(col_items) + "</div>"

    # ---- 6/7. 大师杰作 / 武器模组行（usage 数据，为空整行隐藏） ----
    def _acc(label: str, items) -> str:
        items = [m for m in (items or []) if isinstance(m, dict)][:4]
        if not items:
            return ""
        cells = "".join(
            f"<div class='w2-acccell'>{_ico(m.get('icon'))}"
            f"<span class='tx'><span class='tt'>{esc(label)}：{esc(_mw_name(m.get('name')))}</span>"
            f"<span class='pc'>{_pct(m.get('pct'))}</span></span></div>"
            for m in items)
        return ("<div class='w2-acc'><span class='w2-acclabel'>%s</span>"
                "<div class='w2-accgrid' style='grid-template-columns:repeat(%d,1fr)'>%s</div></div>"
                % (esc(label), len(items), cells))
    # light.gg 的「MW Bonus」列表混着武器模组（如 备用弹匣），按「大师杰作：」前缀分拣；
    # 数据里全无前缀时（旧本地数据）则整体留在杰作行
    _mws = [m for m in (usage.get("masterworks") or []) if isinstance(m, dict)]
    _mods = [m for m in (usage.get("mods") or []) if isinstance(m, dict)]
    if any("大师杰作" in str(m.get("name") or "") for m in _mws):
        _mw_row = [m for m in _mws if "大师杰作" in str(m.get("name") or "")]
        _mod_row = [m for m in _mws if m not in _mw_row] + _mods
    else:
        _mw_row, _mod_row = _mws, _mods
    body += _acc("大师杰作", _mw_row) + _acc("武器模组", _mod_row)

    # ---- 8. 异域催化：只有金枪有，给出数值加成与具体效果（沿用旧数据逻辑，换肤） ----
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
        body += f"<div class='w2-sec'>异域催化</div><div class='notes'>{blocks}</div>"

    if others:
        uniq = _dedupe([{"name": o} for o in others], lambda x: x["name"])
        if uniq:
            body += ("<div class='w2-others'>其他匹配：" +
                     "、".join(esc(o["name"]) for o in uniq) + "</div>")

    # ---- 9. 页脚 ----
    if usage.get("cols") or usage.get("combos"):
        foot_r = "Bungie Manifest · light.gg 社区快照"
        if usage.get("fetched_at"):
            foot_r += f"（{usage['fetched_at']}）"
        foot_r += " · 非精确概率"
    else:
        foot_r = "数据来自 Bungie Manifest / Starside / Clarity"
    body += ("<div class='w2-foot'><span>雷尼克斯联合 · 武器图谱</span>"
             f"<span>{foot_r}</span></div>")
    return _page_weapon(body)


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


# ---------- /轮换（本周突袭 & 地牢 + 扭曲星球） ----------

def _dist_block(dist: dict) -> str:
    """扭曲星球板块：当前时段高亮（w2-cell.on 同款绿），今日剩余时段 + 下一个目的地/倒计时。"""
    if not (dist or {}).get("ok"):
        return ""
    mins, secs = divmod(dist["next_in_sec"], 60)
    left = f"{mins // 60} 时 {mins % 60:02d} 分" if mins >= 60 else f"{mins} 分 {secs:02d} 秒"
    cells = "".join(
        f"<div class='rd-cell{' on' if t['current'] else ''}'>"
        f"<span>{t['range']}</span><b>{esc(t['dest'])}</b></div>"
        for t in dist.get("today") or [])
    return ("<div class='evhead'>扭曲星球轮换<span>每小时换目的地 · 7 小时一轮 · 本机时间</span></div>"
            "<div class='rotdist'>"
            "<div class='rd-now'>"
            f"<div><span class='rd-k'>当前时段 · {esc(dist['range'])}</span>"
            f"<b class='rd-dest'>{esc(dist['dest'])}</b></div>"
            "<div class='rd-next'>下一个"
            f"<b> {esc(dist['next_dest'])}</b>"
            f"<span>{esc(dist['next_hm'])} 切换 · 还剩 {left}</span></div>"
            "</div>"
            f"<div class='rd-grid'>{cells}</div>"
            "</div>")


def rotation_card(rot: dict, dist: dict | None = None) -> str:
    """本周轮换卡片：突袭①② / 地牢①②（pgcr 横图）+ 扭曲星球实时时段板块。"""
    raids = rot.get("raids") or []
    dungeons = rot.get("dungeons") or []
    dist_block = _dist_block(dist or {})
    if not raids and not dungeons and not dist_block:
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
    body += dist_block
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


# ---------- /护甲查询（异域护甲，数据：manifest_index/exotic_armor.json） ----------

_ARMOR_SLOT_ZH = {"头": "头部", "胸": "胸部", "臂": "臂部", "腿": "腿部", "职业": "职业"}
_ARMOR_COLS_PAL = ["#35c66b", "#9b6bd4"]  # 职业金两列特性池的列色，对齐武器卡特性1/2


def _armor_slot(slot: str) -> str:
    return _ARMOR_SLOT_ZH.get(slot or "", slot or "")


def armor_card(w, candidates: list[dict] | None = None) -> str:
    """异域护甲卡（900px 深色，w2 家族版式）。

    w：护甲条目 dict（契约见 manifest_index/exotic_armor.json）；candidates 给出时
    w 退为查询词，出候选列表卡。普通异域件 = 头部（品质/名称/槽位·职业/图标）+ 特性金名
    横条（描述完整展开）+ flavor + 社区称呼；职业金（slot=职业，perk_cols 两列特性池）
    = 头部 + 两列特性池 + 组合一览（有 combos 逐条列，否则注明两列各选其一共 N 种）。
    """
    if candidates is not None:
        return _armor_list_card(w if isinstance(w, str) else (w or {}).get("name") or "",
                                candidates)
    return _armor_detail_card(w or {})


def _armor_head(a: dict) -> str:
    meta = "<em class='a2-q'>异域</em>"
    if a.get("en"):
        meta += f"<span class='a2-en'>{esc(a['en'])}</span>"
    sub = " · ".join(x for x in (_armor_slot(a.get("slot")), a.get("class") or "") if x)
    head = "<div class='w2-head'><div class='w2-headtxt'>"
    head += f"<div class='w2-meta'>{meta}</div>"
    head += f"<div class='w2-title'>{esc(a.get('name') or '')}</div>"
    if sub:
        head += f"<div class='w2-sub'>{esc(sub)}</div>"
    head += "</div>"
    if a.get("icon"):
        head += f"<div class='a2-icon'><img src='{esc(a['icon'])}'></div>"
    if a.get("flavor"):
        head += f"<div class='a2-flav'>{esc(a['flavor'])}</div>"
    return head + "</div>"


def _armor_aliases(a: dict) -> str:
    al = [str(x).strip() for x in (a.get("aliases") or []) if str(x).strip()]
    if not al:
        return ""
    return ("<div class='a2-alias'><b>社区称呼</b>　"
            + "、".join(esc(x) for x in al) + "</div>")


def _armor_foot(a: dict) -> str:
    foot_r = "数据来自 Bungie Manifest"
    if a.get("updated"):
        foot_r += f"（{esc(a['updated'])}）"
    return ("<div class='w2-foot'><span>雷尼克斯联合 · 异域护甲图谱</span>"
            f"<span>{foot_r}</span></div>")


def _armor_detail_card(a: dict) -> str:
    body = _armor_head(a)

    # 异域特性金名横条：描述完整展开（数值不截断），多行文本按行分段
    perk_name = str(a.get("perk_name") or "").strip()
    perk_desc = str(a.get("perk_desc") or "").strip()
    if perk_name or perk_desc:
        descs = "".join(f"<span class='a2-intdesc'>{esc(x)}</span>"
                        for x in perk_desc.split("\n") if x.strip())
        body += ("<div class='w2-int a2-int'><div class='w2-inttx'>"
                 + (f"<span class='w2-intname'>{esc(perk_name)}</span>" if perk_name else "")
                 + descs + "</div></div>")

    # 职业金（slot=职业）：两列特性池（w2-pcols 同款列色卡，图标+名称+完整描述）+ 组合
    perk_cols = [c for c in (a.get("perk_cols") or []) if isinstance(c, list) and c]
    if perk_cols:
        cols_html = ""
        for i, col in enumerate(perk_cols[:2]):
            color = _ARMOR_COLS_PAL[i % 2]
            rows = "".join(
                "<div class='a2-perk'>" + _ico(p.get("icon"))
                + f"<span class='tx'><span class='nm'>{esc(p.get('name') or '')}</span>"
                + (f"<span class='ds'>{esc(p.get('desc') or '')}</span>"
                   if p.get("desc") else "") + "</span></div>"
                for p in col if isinstance(p, dict))
            cols_html += ("<div class='w2-pcol'><span class='w2-ctag' "
                          f"style='color:{color};background:{color}26'>特性列 {'ⅠⅡ'[i]}</span>"
                          + rows + "</div>")
        body += ("<div class='w2-pcols' style='grid-template-columns:"
                 f"repeat({min(len(perk_cols), 2)},1fr)'>{cols_html}</div>")

        def _nm(x) -> str:
            return str(x.get("name") or "") if isinstance(x, dict) else str(x or "")

        combos = a.get("combos")
        cells = ""
        if isinstance(combos, list) and combos:
            # 数据里直接带了组合列表：逐条列（每条 = 列1名字 ＋ 列2名字）
            for c in combos[:30]:
                pair = list(c.get("names") or []) if isinstance(c, dict) else (
                    list(c) if isinstance(c, (list, tuple)) else [])
                pair = [_nm(x) for x in (pair + ["", ""])[:2]]
                cells += (f"<div class='a2-combo'><span class='l'>{esc(pair[0])}</span>"
                          f"<span class='pl'>＋</span>"
                          f"<span class='r'>{esc(pair[1])}</span></div>")
            body += ("<div class='w2-panel' style='padding:12px 14px'>"
                     f"<div class='w2-blocktitle'>组合一览 · 共 {len(combos)} 种</div>"
                     f"<div class='a2-cgrid2'>{cells}</div></div>")
        else:
            n_combo = len(perk_cols[0]) * (len(perk_cols[1]) if len(perk_cols) > 1 else 1)
            body += (f"<div class='a2-hint'>两列各选其一，共 <b>{n_combo}</b> 种组合"
                     "（列内条目任意搭配）</div>")

    body += _armor_aliases(a) + _armor_foot(a)
    return _page_weapon(body)


def _armor_list_card(q: str, items: list[dict]) -> str:
    """模糊命中多件：候选列表卡（图 46px + 金名 + 特性名 + 槽位·职业）"""
    rows = ""
    for a in items[:12]:
        icon = (f"<img src='{esc(a.get('icon') or '')}'>" if a.get("icon")
                else "<i class='noi'></i>")
        meta = " · ".join(x for x in (_armor_slot(a.get("slot")), a.get("class") or "") if x)
        rows += (f"<div class='a2-cand'>{icon}<span class='tx'>"
                 f"<span class='nm'>{esc(a.get('name') or '')}</span>"
                 + (f"<span class='pk'>{esc(a.get('perk_name') or '')}</span>"
                    if a.get("perk_name") else "")
                 + f"</span><span class='mt'>{esc(meta)}</span></div>")
    extra = f"（共 {len(items)} 件）" if len(items) > 12 else ""
    body = ("<div class='w2-int a2-int'><div class='w2-inttx'>"
            f"<span class='w2-intname'>护甲「{esc(q)}」</span>"
            f"<span class='a2-intdesc'>找到 {len(items)} 件匹配{extra}，"
            "把名字打全可以看详情（支持外号 / 英文名）</span></div></div>"
            f"<div class='a2-cands'>{rows}</div>"
            "<div class='w2-foot'><span>雷尼克斯联合 · 异域护甲图谱</span>"
            "<span>数据来自 Bungie Manifest</span></div>")
    return _page_weapon(body)
