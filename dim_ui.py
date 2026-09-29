"""DIM 板块的前端公共层：主题样式 + 外壳 + 全站共用的 JS。

配色/尺寸照抄 DIM 官方（src/app/themes/_theme.scss、variables.scss、ItemIcon.m.scss）：
  * 默认主题 --theme-accent-primary = #e8a534（橙），背景是 hsl(240,20%,28%)→hsl(240,27%,12%) 的径向渐变
  * 稀有度底色：异域 #ceae33 / 传说 #522f65 / 稀有 #5076a3 / 罕见 #366f42 / 普通 #c3bcb4
  * 物品格子：1px 实线边框（默认 #ddd），大师之作 #eade8b，已满级 #f5dc56
  * 物品弹窗：头 #0a0a0f / 体 #151523 / 边 #8e8e9e
标签沿用 DIM 的语义与快捷键：shift+1 收藏 / shift+2 保留 / shift+3 丢弃 / shift+4 注入 /
shift+5 归档 / shift+0 清除（DIM 的标签存在本地，官方 API 没有标签接口，我们也存本地）。
"""
from __future__ import annotations

import json

# DIM 的标签定义（顺序/快捷键/图标符号都跟 DIM 对齐）
TAGS = [
    {"t": "favorite", "n": "收藏", "sym": "♥", "c": "#ff5959", "key": "shift+1"},
    {"t": "keep",     "n": "保留", "sym": "⚑", "c": "#51a351", "key": "shift+2"},
    {"t": "junk",     "n": "丢弃", "sym": "✖", "c": "#ff3232", "key": "shift+3"},
    {"t": "infuse",   "n": "注入", "sym": "⚡", "c": "#b46cff", "key": "shift+4"},
    {"t": "archive",  "n": "归档", "sym": "🗄", "c": "#e8a534", "key": "shift+5"},
]


SHELL_CSS = """
:root{
  --item-size:48px;
  --accent:#e8a534; --accent-2:#68a0b7;
  --text:#efefef; --text-2:#aaa;
  --modal:#0d0f14; --modal-2:#161616;
  --tips-head:#0a0a0f; --tips-body:#151523; --tips-border:#8e8e9e;
  --polaroid:#ddd; --mw:#eade8b; --capped:#f5dc56;
  --common:#dcdcdc; --uncommon:#366e42; --rare:#5076a3; --legendary:#522f65; --exotic:#ceae33;
  --power:#f5dc56; --green:#51a351; --red:#ff3232;
  --legendary-bg:#522f65; --exotic-bg:#ceae33;
}
*{box-sizing:border-box}
html{scrollbar-color:#5d5970 transparent}
body{margin:0;min-height:100vh;color:var(--text);
  background:radial-gradient(circle at 50% 70px,hsl(240,20%,28%) 0%,hsl(240,27%,12%) 100%) fixed;
  font:14px/1.4 "Open Sans",Helvetica,Arial,"Microsoft YaHei",system-ui,sans-serif}
a{color:var(--accent);text-decoration:none}
button,select,input{font-family:inherit}

/* ---------- 顶栏（DIM header：logo + 页签 + 搜索 + 设置） ---------- */
.hdr{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:14px;
     padding:5px 12px;background:rgba(0,0,0,.55);backdrop-filter:blur(3px);
     border-bottom:1px solid rgba(255,255,255,.08)}
.hdr .logo{display:flex;align-items:center;gap:6px;font-weight:700;letter-spacing:.5px;
     color:#fff;font-size:15px;padding:0 4px}
.hdr .logo i{width:9px;height:9px;background:var(--accent);display:block;
     transform:rotate(45deg);border-radius:1px}
.hdr nav{display:flex;gap:2px}
.hdr nav a{padding:7px 13px;border-radius:5px;color:var(--text);font-size:14px;opacity:.85;
     border-bottom:2px solid transparent}
.hdr nav a:hover{background:rgba(255,255,255,.08);opacity:1}
.hdr nav a.on{color:var(--accent);border-bottom-color:var(--accent);opacity:1}
.hdr .sp{flex:1}
.swrap{position:relative;flex:0 1 460px}
.swrap input{width:100%;height:28px;border-radius:6px;border:1px solid transparent;
  background:rgba(0,0,0,.4);color:var(--text);padding:0 10px;font-size:13px}
.swrap input:focus{outline:none;border-color:var(--accent)}
.swrap input::placeholder{color:#888}
.iconbtn{width:28px;height:28px;display:grid;place-items:center;border-radius:5px;
  background:transparent;border:0;color:var(--text);cursor:pointer;font-size:15px}
.iconbtn:hover{background:rgba(255,255,255,.12)}
.wrap{padding:10px 12px 60px;margin:0 auto;max-width:2600px}
.mut{color:var(--text-2);font-size:12px}
.hidden{display:none!important}

/* ---------- 通用控件 ---------- */
.btn{background:rgba(255,255,255,.1);border:1px solid transparent;color:var(--text);
     border-radius:5px;padding:5px 11px;font-size:13px;cursor:pointer}
.btn:hover{background:rgba(255,255,255,.2)}
.btn.pri{background:var(--accent);color:#000;font-weight:600}
.btn.pri:hover{filter:brightness(1.12)}
.btn:disabled{opacity:.45;cursor:default}
.btn.sm{padding:3px 8px;font-size:12px}
.chk{display:inline-flex;align-items:center;gap:5px;font-size:13px;cursor:pointer}
select,input[type=number],input[type=text]{background:#333;border:1px solid transparent;
  color:var(--text);border-radius:5px;padding:4px 7px;font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}

/* ---------- 物品格子（DIM ItemIcon/InventoryItem） ---------- */
.item{position:relative;width:var(--item-size);height:var(--item-size);cursor:pointer;
      contain:layout paint style size;transition:opacity .2s}
.item.dragging{opacity:.4}
.item.expand{height:calc(var(--item-size) + 17px)}
.item-img{position:relative;width:var(--item-size);height:var(--item-size);
  border:1px solid var(--polaroid);background:#222 center/contain no-repeat}
.item-img .rar{position:absolute;inset:0;background-size:contain;background-position:center;
  background-repeat:no-repeat}
.item-img .ic{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block}
.item-img .wm{position:absolute;inset:0;background-size:100% 100%;background-repeat:no-repeat;
  pointer-events:none}
/* 稀有度底色（图标透明处透出来，跟 DIM 一样） */
.r0{background-color:#c3bcb4}.r1{background-color:#c3bcb4}.r2{background-color:#c3bcb4}
.r3{background-color:#366f42}.r4{background-color:#5076a3}
.r5{background-color:#522f65}.r6{background-color:#ceae33}
.item.mw>.item-img{border-color:var(--mw);box-shadow:inset 0 0 0 1px var(--mw)}
.item.eq>.item-img{border-color:var(--power)}
.item.searchHidden{opacity:.2}
.item.hl>.item-img{outline:1px solid var(--accent)}
.item:hover>.item-img{outline:1px solid #ddd}
.item .vl{position:absolute;left:1px;bottom:0;font-size:11px;font-weight:700;color:var(--power);
  text-shadow:0 0 2px #000,0 0 3px #000,0 0 3px #000}
.item .qty{position:absolute;right:1px;bottom:0;font-size:11px;font-weight:700;color:#fff;
  text-shadow:0 0 2px #000,0 0 3px #000,0 0 3px #000}
.item .ammo{position:absolute;left:1px;top:1px;font-size:10px;color:#fff;text-shadow:0 0 2px #000}
.item .etop{position:absolute;right:1px;top:1px;font-size:10px;font-weight:700;color:#fff;
  text-shadow:0 0 2px #000}
.item .icons{position:absolute;right:2px;bottom:calc(var(--item-size) - 17px);display:flex;gap:1px}
.item .ic-t{width:calc(var(--item-size)/5);height:calc(var(--item-size)/5);font-size:calc(var(--item-size)/5 - 1px);
  line-height:1;text-align:center;filter:drop-shadow(0 0 2px rgba(0,0,0,.9))}
.item .bar{position:absolute;left:2px;right:2px;top:2px;height:5px;background:rgba(0,0,0,.5)}
.item .bar i{display:block;height:100%;background:#5ea16a}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--item-size),var(--item-size)));
  gap:2px;justify-content:start}
.empty-slot{width:var(--item-size);height:var(--item-size);border:1px solid rgba(255,255,255,.25);
  border-radius:2px;background:rgba(0,0,0,.25)}

/* ---------- 角色/仓库列（DIM stores） ---------- */
.stores{display:flex;gap:8px;align-items:flex-start}
.store{flex:1 1 0;min-width:0;background:rgba(255,255,255,.035);border-radius:6px;
  border:2px solid transparent;display:flex;flex-direction:column;max-height:calc(100vh - 118px)}
.store.drop{border-color:var(--accent);background:rgba(232,165,52,.12)}
.store-head{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:4px;
  background:center/cover no-repeat;position:relative;overflow:hidden;flex:none}
.store-head::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.6),rgba(0,0,0,.25))}
.store-head>*{position:relative;z-index:1}
.store-head .cn{font-weight:600;font-size:15px;text-shadow:0 1px 3px #000}
.store-head .cl{font-size:20px;font-weight:700;color:var(--power);text-shadow:0 1px 3px #000}
.store-body{overflow:auto;padding:6px;flex:1}
.store.vault .store-head{background-color:#2b3a55}
.sec{border-radius:4px;padding:3px 4px 5px;margin-bottom:3px;border:1px solid transparent}
.sec.drop{background:rgba(232,165,52,.15);border-color:var(--accent)}
.sec-hd{display:flex;justify-content:space-between;align-items:baseline;padding:1px 3px 3px;
  font-size:12px;color:var(--text-2);text-transform:uppercase;letter-spacing:.4px}
.sec-hd b{text-transform:none;letter-spacing:0;color:var(--text);font-weight:600;font-size:12px}
.sec.full .sec-hd b{color:#ff9a4d}
.slots{display:flex;gap:3px;flex-wrap:wrap}
.slot{border-radius:2px;border:1px dashed rgba(255,255,255,.18);padding:1px}
.slot.drop{border:1px solid var(--accent);background:rgba(232,165,52,.18)}
.slot-tag{font-size:10px;color:var(--text-2);text-align:center;max-width:var(--item-size);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ---------- 右键菜单（DIM item actions） ---------- */
.menu{position:fixed;z-index:300;background:var(--modal);border:1px solid rgba(255,255,255,.14);
  border-radius:6px;padding:4px;min-width:196px;box-shadow:0 0 18px 0 #000}
.menu .mi{display:flex;justify-content:space-between;gap:12px;align-items:center;
  padding:6px 10px;border-radius:4px;font-size:13px;cursor:pointer;white-space:nowrap}
.menu .mi:hover{background:rgba(255,255,255,.14)}
.menu .mi.kbd::after{content:attr(data-k);font-size:11px;color:var(--text-2)}
.menu .sep{height:1px;background:rgba(255,255,255,.16);margin:3px 4px}
.menu .mh{padding:4px 10px;font-size:11px;color:var(--text-2)}
.menu .mi.dis{opacity:.35;cursor:not-allowed}
.menu .mi.dis:hover{background:none}
.menu .sw{display:inline-block;width:14px;text-align:center;margin-right:5px}

/* ---------- 物品弹窗（DIM item popup） ---------- */
.pop{position:fixed;z-index:290;width:400px;max-height:calc(100vh - 90px);overflow:auto;
  background:var(--tips-body);border:1px solid var(--tips-border);border-radius:6px;
  box-shadow:0 0 18px 0 #000;font-size:13px}
.pop>*{padding:0 12px}
.pop.hoveronly{pointer-events:none}
.pop-hd{background:var(--tips-head);padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.12);
  display:flex;gap:9px;align-items:flex-start}
.pop-hd .pic{width:48px;height:48px;border:1px solid var(--polaroid);border-radius:2px;flex:none;
  background:#222 center/contain no-repeat}
.pop-hd .t1{font-weight:700;font-size:15px;line-height:1.25}
.pop-hd .t2{font-size:12px;color:var(--text-2)}
.pop-hd .t3{font-size:12px;color:var(--accent)}
.pop-sec{margin:9px 0 0}
.pop-sec>h4{margin:0 0 5px;font-size:11px;letter-spacing:.6px;color:var(--text-2);
  text-transform:uppercase;font-weight:600}
.statrow{display:flex;align-items:center;gap:7px;margin:2px 0}
.statrow .sn{flex:0 0 74px;font-size:12px;color:var(--text-2)}
.statrow .sv{flex:0 0 34px;text-align:right;font-weight:700;font-size:12px}
.statrow .sb{flex:1;height:10px;background:#222;border-radius:2px;overflow:hidden;display:flex}
.statrow .sb i{display:block;height:100%;background:#4b7fb5}
.statrow .sb i.plus{background:var(--accent-2)}
.plugs{display:grid;grid-template-columns:repeat(auto-fill,minmax(27px,27px));gap:4px}
.plug{width:27px;height:27px;border:1px solid #888;border-radius:3px;background:#1c1c22 center/contain no-repeat}
.plug.off{opacity:.35;border-style:dashed}
.perk{display:flex;gap:7px;align-items:flex-start;margin:3px 0}
.perk img,.perk .pi{width:26px;height:26px;border-radius:3px;flex:none;background:#222 center/contain no-repeat}
.perk .pn{font-size:12px}
.perk .pd{font-size:11px;color:var(--text-2)}
.pop note,input.note{display:block}
.pop-act{display:flex;flex-wrap:wrap;gap:5px;padding:9px 12px 11px}
.tagbar{display:flex;gap:3px;padding:6px 12px 2px}
.tagbtn{width:26px;height:26px;border-radius:4px;border:1px solid rgba(255,255,255,.2);
  background:rgba(255,255,255,.06);color:var(--text);cursor:pointer;font-size:14px;line-height:1}
.tagbtn:hover{background:rgba(255,255,255,.18)}
.tagbtn.on{border-color:currentColor;background:rgba(255,255,255,.16);font-weight:700}

/* ---------- 设置抽屉（对应 DIM 设置页的几个显示项） ---------- */
.drawer{position:fixed;top:0;right:0;bottom:0;width:340px;z-index:320;overflow:auto;
  background:var(--modal);border-left:1px solid rgba(255,255,255,.14);padding:12px 14px;
  box-shadow:0 0 18px 0 #000}
.drawer h3{margin:0 0 10px;font-size:15px;display:flex;justify-content:space-between}
.dset{margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.1)}
.dset>label{display:block;font-size:11px;color:var(--text-2);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:4px}
.dset .row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:3px 0}
.dlist .di{display:flex;align-items:center;gap:6px;padding:3px 5px;border-radius:4px;font-size:13px;
  cursor:grab;background:rgba(255,255,255,.05);margin-bottom:2px}
.dlist .di:hover{background:rgba(255,255,255,.12)}
.dlist .di .ar{color:var(--text-2);cursor:pointer;padding:0 3px}
.ovl{position:fixed;inset:0;z-index:310;background:rgba(0,0,0,.45)}

/* ---------- 其它 ---------- */
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:400;
  background:var(--modal);border:1px solid var(--tips-border);border-radius:6px;
  padding:9px 16px;font-size:13px;box-shadow:0 0 18px 0 #000;max-width:70vw}
.toast.bad{border-color:var(--red);color:#ffbdbd}
.ld{width:15px;height:15px;border:2px solid rgba(255,255,255,.25);border-top-color:var(--accent);
  border-radius:50%;display:inline-block;animation:sp .8s linear infinite;vertical-align:-3px}
@keyframes sp{to{transform:rotate(360deg)}}
.chips{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin:0 0 8px}
.chip{background:rgba(255,255,255,.1);border-radius:11px;padding:2px 9px;font-size:12px;
  cursor:pointer;color:var(--text-2)}
.chip:hover{background:rgba(255,255,255,.2)}
.chip.on{background:var(--accent);color:#000;font-weight:600}
.bar2{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:0 0 8px}
.sleep{display:block;min-height:30px}
.note-flag{position:absolute;left:1px;top:1px;font-size:10px;color:#fff;text-shadow:0 0 2px #000}
"""


SHELL_JS = r"""
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
function esc(s){return String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function api(path, opt){
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch(e){ throw new Error('HTTP ' + r.status); }
  if (!r.ok || j.ok === false) throw new Error(j.error || j.detail || ('HTTP ' + r.status));
  return j;
}
function json(o){ return { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(o) }; }
let _tt = null;
function toast(msg, bad){
  let el = $('#tst');
  if (!el){ el = document.createElement('div'); el.id='tst'; el.className='toast';
    document.body.appendChild(el); }
  el.textContent = msg;
  el.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(_tt); _tt = setTimeout(() => el.remove(), bad ? 5200 : 2600);
}
function loading(txt){ return '<div class="sleep"><span class="ld"></span> <span class="mut">' +
  esc(txt) + '</span></div>'; }

// ---- 标签（DIM：收藏/保留/丢弃/注入/归档） ----
const TAGS = __TAGS__;
const TAG_MAP = {}; TAGS.forEach(t => TAG_MAP[t.t] = t);
const TAG_KEYS = {}; TAGS.forEach(t => TAG_KEYS[t.key] = t.t);
function tagOf(x){ return (TAGS_DB[x.tid] || {}).t || ''; }
function noteOf(x){ return (TAGS_DB[x.tid] || {}).n || ''; }
function tagHTML(x){
  const t = TAG_MAP[tagOf(x)], n = noteOf(x);
  let h = '';
  if (t) h += '<span class="ic-t" style="color:' + t.c + '" title="' + esc(t.n) + '">' + t.sym + '</span>';
  if (n) h += '<span class="ic-t" title="' + esc(n) + '">🗒</span>';
  return h;
}

// ---- 本地设置（DIM 的显示设置存在本地，我们也放 localStorage） ----
const DEF_SET = {size: 48, sort: 'rarity', sortDir: -1, itemSort: 'name', showTagged: true,
                 hideJunk: false, equippedFirst: true, showNotes: true};
let SET = Object.assign({}, DEF_SET);
try { Object.assign(SET, JSON.parse(localStorage.getItem('dim_set') || '{}')); } catch(e) {}
function saveSet(){ try { localStorage.setItem('dim_set', JSON.stringify(SET)); } catch(e) {} 
  document.documentElement.style.setProperty('--item-size', SET.size + 'px'); }
saveSet();

// ---- 搜索语法（对齐 DIM 常用写法） ----
//   is:weapon / is:armor / is:equipped / is:locked / is:tagged / is:masterwork
//   tag:favorite / notes:xxx / hashtag:#xxx / not:xxx / 裸词=名称+类型+来源
let QTERMS = [];
function parseQ(s){
  QTERMS = [];
  const parts = String(s || '').match(/(?:[^\s"]+|"[^"]*")+/g) || [];
  for (let p of parts){
    p = p.replace(/^"|"$/g, '');
    let neg = false;
    if (/^not:|^!/.test(p)){ neg = true; p = p.replace(/^not:|^!/, ''); }
    const m = /^(is|tag|notes?|hashtag|source|perk|name|type|slot|class|kw):(.*)$/i.exec(p);
    QTERMS.push(m ? {k: m[1].toLowerCase(), v: m[2].toLowerCase(), neg}
                  : {k: 'text', v: p.toLowerCase(), neg});
  }
}
function matchQ(x){
  for (const t of QTERMS){
    let hit = true;
    if (t.k === 'text'){
      hit = (x.n + ' ' + x.ty + ' ' + x.tn + ' ' + (x.src || '')).toLowerCase().includes(t.v);
    } else if (t.k === 'is'){
      const v = t.v;
      hit = v === 'weapon' ? (x.kind === 'weapon')
          : v === 'armor' ? (x.kind === 'armor')
          : v === 'equipped' ? !!x.eq
          : v === 'locked' ? !!(x.s & 1)
          : v === 'tagged' ? !!tagOf(x)
          : v === 'masterwork' ? !!x.mw
          : v === 'note' ? !!noteOf(x)
          : (x.ty || '').toLowerCase().includes(v);
    } else if (t.k === 'tag'){
      const cur = tagOf(x);
      hit = t.v === 'none' ? !cur : (t.v === 'tagged' ? !!cur : cur === t.v);
    } else if (t.k === 'notes' || t.k === 'note' || t.k === 'hashtag'){
      hit = noteOf(x).toLowerCase().includes(t.v.replace(/^#/, ''));
    } else if (t.k === 'source'){ hit = (x.src || '').toLowerCase().includes(t.v);
    } else if (t.k === 'name'){ hit = (x.n || '').toLowerCase().includes(t.v);
    } else if (t.k === 'type'){ hit = ((x.ty || '') + ' ' + (x.tn || '')).toLowerCase().includes(t.v);
    } else if (t.k === 'class'){ hit = (x.clsName || '').includes(t.v);
    } else if (t.k === 'slot'){ hit = (x.slotName || '').includes(t.v);
    } else if (t.k === 'kw'){ hit = (x.n + ' ' + (x.src || '')).toLowerCase().includes(t.v); }
    if (t.neg ? hit : !hit) return false;
  }
  return true;
}

// ---- 物品排序 ----
function cmpItems(a, b){
  const s = SET.itemSort, d = SET.sortDir;
  let r = 0;
  if (s === 'rarity') r = (a.tt || 0) - (b.tt || 0);
  else if (s === 'type') r = (a.ty || '').localeCompare(b.ty || '');
  else if (s === 'power') r = (a.pw || 0) - (b.pw || 0);
  else if (s === 'quantity') r = (a.q || 0) - (b.q || 0);
  else if (s === 'tier') r = (a.armorTier || 0) - (b.armorTier || 0);
  else r = (a.n || '').localeCompare(b.n || '');
  if (r === 0) r = (a.pw || 0) - (b.pw || 0);
  if (r === 0) r = (a.n || '').localeCompare(b.n || '');
  return r * (d < 0 ? -1 : 1);
}

// ---- 物品弹窗（DIM item popup：跟随格子，自动翻转） ----
let POP_IID = null, POP_PIN = false, _hv = null, _hc = null;
function closePop(){ const p = $('#pop'); if (p) p.classList.add('hidden');
  POP_IID = null; POP_PIN = false; }
function cancelHover(){ clearTimeout(_hv); clearTimeout(_hc); }
async function openPop(x, anchor, pin){
  if (!x || !x.tid) return;
  POP_PIN = !!pin;                      // 点开的弹窗固定住，悬停出来的鼠标移开就收
  const p = $('#pop');
  // 悬停弹窗不可交互（DIM 同款）：否则它会盖住相邻格子，鼠标根本移不过去
  p.classList.toggle('hoveronly', !POP_PIN);
  p.classList.remove('hidden');
  if (POP_IID === x.tid && p.dataset.h === String(x.h)) { placePop(anchor); return; }
  POP_IID = x.tid; p.dataset.h = String(x.h);
  if (!x.i){   // 没有实例 id 的堆叠物品（材料/消耗品等）：只能用本地信息渲染
    p.innerHTML = popHTML(x, {name: x.n, icon: x.ic, typeName: x.ty, tierName: x.tn,
      power: x.pw, stats: [], perks: [], sockets: [], source: x.src});
    bindPop(x, {});
    placePop(anchor);
    return;
  }
  p.innerHTML = '<div style="padding:12px">' + loading('读取详情…') + '</div>';
  placePop(anchor);
  let d;
  try { d = POPCACHE[x.tid] = POPCACHE[x.tid] || await api('/api/dim/item/' + x.i + '?h=' + x.h); }
  catch(e){ p.innerHTML = '<div style="padding:12px" class="mut">详情获取失败：' +
    esc(e.message) + '</div>'; return; }
  if (POP_IID !== x.tid) return;
  p.innerHTML = popHTML(x, d);
  bindPop(x, d);
  placePop(anchor);
}
function placePop(anchor){
  const p = $('#pop'); if (!p || p.classList.contains('hidden')) return;
  const r = anchor.getBoundingClientRect();
  const w = p.offsetWidth, h = p.offsetHeight;
  let left = r.left, top = r.bottom + 6;
  if (top + h > innerHeight - 4) top = Math.max(60, r.top - h - 6);
  if (left + w > innerWidth - 4) left = Math.max(4, innerWidth - w - 4);
  p.style.left = left + 'px'; p.style.top = top + 'px';
}
const POPCACHE = {};
function popHTML(x, d){
  const tn = TAG_MAP[tagOf(x)];
  const st = (d.stats || []).map(s => {
    const pct = Math.min(100, (s.v / (s.big || 100)) * 100);
    return '<div class="statrow"><span class="sn">' + esc(s.n) + '</span>' +
      '<span class="sb"><i style="width:' + pct + '%"></i></span>' +
      '<span class="sv">' + s.v + '</span></div>';
  }).join('');
  const perks = (d.perks || []).map(p => '<div class="perk"><div class="pi" style="background-image:url(' +
    p.ic + ')"></div><div><div class="pn">' + esc(p.n) + '</div>' +
    '<div class="pd">' + esc(p.d || '') + '</div></div></div>').join('');
  const sockets = (d.sockets || []).map(s => '<div class="plug' + (s.en === false ? ' off' : '') +
    '" style="background-image:url(' + s.ic + ')" title="' + esc(s.n) + '"></div>').join('');
  const tags = TAGS.map(t => '<button class="tagbtn' + (tagOf(x) === t.t ? ' on' : '') +
    '" style="color:' + t.c + '" data-tag="' + t.t + '" title="' + t.n + ' ' +
    t.key + '">' + t.sym + '</button>').join('');
  return '<div class="pop-hd"><div class="pic r' + (x.tt || 0) + '" style="background-image:url(' +
      (d.icon || x.ic) + ')"></div><div><div class="t1">' + esc(d.name || x.n) + '</div>' +
      '<div class="t2">' + esc(d.tierName || '') + ' ' + esc(d.typeName || x.ty || '') +
      (d.power ? ' · <b style="color:var(--power)">' + d.power + '</b>' : '') + '</div>' +
      (d.source ? '<div class="t3">' + esc(d.source) + '</div>' : '') + '</div></div>' +
    '<div class="tagbar">' + tags + '<button class="tagbtn" data-tag="" title="清除标签 shift+0">∅</button></div>' +
    (st ? '<div class="pop-sec"><h4>属性</h4>' + st + '</div>' : '') +
    (perks ? '<div class="pop-sec"><h4>Perk</h4>' + perks + '</div>' : '') +
    (sockets ? '<div class="pop-sec"><h4>插槽 / 模组</h4><div class="plugs">' + sockets + '</div></div>' : '') +
    '<div class="pop-sec"><h4>备注</h4><input class="note" placeholder="写点备注，支持 #标签，可被 notes: 搜索" ' +
      'value="' + esc(noteOf(x)) + '"></div>' +
    '<div class="pop-act">' + popActions(x) + '</div>';
}
function popActions(x){
  if (!x.i) return '<button class="btn" data-a="close">关闭</button>';
  let h = '';
  const isVault = x.w === 'vault';
  if (x.eqo){
    if (isVault){ (ST.chars || []).forEach(c => { if (x.cx & (1 << c.cls))
      h += '<button class="btn" data-a="equip" data-c="' + c.id + '">装备到' + esc(c.clsName) + '</button>'; }); }
    else { const c = (ST.chars || []).find(c => c.id === x.w);
      if (c) h += '<button class="btn pri" data-a="equip" data-c="' + x.w + '">装备</button>'; }
  }
  if (!isVault){
    const cur = (ST.chars || []).find(c => c.id === x.w);
    if (x.b === 215593132 && cur) h += '<button class="btn" data-a="char" data-c="' + x.w + '">从邮政官取回</button>';
    else if (x.vc) h += '<button class="btn" data-a="vault">移到仓库</button>';
    (ST.chars || []).forEach(c => { if (c.id !== x.w && (x.cx & (1 << c.cls)))
      h += '<button class="btn" data-a="char" data-c="' + c.id + '">移到' + esc(c.clsName) + '</button>'; });
  } else {
    (ST.chars || []).forEach(c => { if (x.cx & (1 << c.cls))
      h += '<button class="btn" data-a="char" data-c="' + c.id + '">移到' + esc(c.clsName) + '</button>'; });
  }
  h += '<button class="btn" data-a="lock">' + (x.s & 1 ? '解锁' : '锁定') + '</button>';
  return h;
}
function bindPop(x, d){
  const p = $('#pop');
  p.onclick = async ev => {
    const tb = ev.target.closest('[data-tag]');
    if (tb){
      const t = tb.dataset.tag;
      try { await setTag(x, tagOf(x) === t ? '' : t); toast(t ? '已标记「' + TAG_MAP[t].n + '」' : '已清除标签'); }
      catch(e){ toast(e.message, true); }
      return;
    }
    const b = ev.target.closest('[data-a]'); if (!b) return;
    const a = b.dataset.a;
    if (a === 'close'){ closePop(); return; }
    try {
      if (a === 'equip'){ await api('/api/dim/equip', json({iid: x.i, h: x.h, char: b.dataset.c, frm: x.w}));
        toast('已装备 ' + x.n); closePop(); reloadSoon(); }
      else if (a === 'vault'){ await api('/api/dim/move', json({iid: x.i, h: x.h, to: 'vault', frm: x.w}));
        toast('已移入仓库 ' + x.n); closePop(); reloadSoon(); }
      else if (a === 'char'){ await api('/api/dim/move', json({iid: x.i, h: x.h, to: b.dataset.c, frm: x.w}));
        toast('已移到 ' + x.n); closePop(); reloadSoon(); }
      else if (a === 'lock'){ await api('/api/dim/lock', json({iid: x.i, h: x.h, on: (x.s & 1) ? 0 : 1}));
        x.s = (x.s & 1) ? (x.s & ~1) : (x.s | 1); openPop(x, b.closest('.pop') ? document.body : b);
        toast(x.s & 1 ? '已锁定' : '已解锁'); }
    } catch(e){ toast(e.message, true); }
  };
  const note = p.querySelector('input.note');
  if (note) note.onchange = async () => {
    try { await api('/api/dim/note', json({iid: x.i, h: x.h, note: note.value}));
      TAGS_DB[x.tid] = Object.assign({}, TAGS_DB[x.tid], {n: note.value}); toast('备注已保存'); }
    catch(e){ toast(e.message, true); }
  };
}
// 悬停出弹窗（跟 DIM 一样，左键也出）
function bindHover(root){
  root.addEventListener('mouseover', e => {
    if (e.target.closest('#pop')){ clearTimeout(_hc); return; }   // 鼠标进弹窗：别收，要点按钮
    const it = e.target.closest('.item');
    if (!it || !it.dataset.i){ return; }
    if (it.closest('[data-nohover]')) return;                     // 编辑器这类地方禁掉悬停详情
    clearTimeout(_hc);
    if (POP_PIN) return;                                          // 固定住的弹窗不跟着悬停换
    clearTimeout(_hv);
    _hv = setTimeout(() => openPop(itemOf(it.dataset.i), it), 180);
  });
  root.addEventListener('mouseout', e => {
    const to = e.relatedTarget;
    if (to && to.closest && (to.closest('#pop') || to.closest('.item'))) return;
    clearTimeout(_hv);
    if (!POP_PIN){ clearTimeout(_hc); _hc = setTimeout(closePop, 300); }
  });
}

// ---- 拖拽（HTML5 DnD：拖到角色/仓库 = 搬运，拖到装备槽 = 装备） ----
let DRAG = null;
function bindDnD(root){
  root.addEventListener('dragstart', e => {
    const it = e.target.closest('.item'); if (!it || !it.dataset.i) return;
    DRAG = itemOf(it.dataset.i);
    it.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', it.dataset.i); } catch(err) {}
  });
  root.addEventListener('dragend', e => {
    const it = e.target.closest('.item'); if (it) it.classList.remove('dragging');
    $$('.drop').forEach(n => n.classList.remove('drop'));
    DRAG = null;
  });
  root.addEventListener('dragover', e => {
    if (!DRAG) return;
    const t = nearestTarget(e.target);
    if (!t) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    $$('.drop').forEach(n => { if (n !== t) n.classList.remove('drop'); });
    t.classList.add('drop');
  });
  root.addEventListener('dragleave', e => {
    const t = e.target.closest && e.target.closest('.drop');
    if (t && !t.contains(e.relatedTarget)) t.classList.remove('drop');
  });
  root.addEventListener('drop', async e => {
    if (!DRAG) return;
    e.preventDefault();
    const t = nearestTarget(e.target);
    $$('.drop').forEach(n => n.classList.remove('drop'));
    const x = DRAG; DRAG = null;
    if (!t || !x) return;
    try {
      if (t.dataset.slotBucket !== undefined){
        const bkt = Number(t.dataset.slotBucket);
        if (x.b !== bkt){ toast('「' + x.n + '」不能放进这个装备槽', true); return; }
        await api('/api/dim/equip', json({iid: x.i, h: x.h, char: t.dataset.store, frm: x.w}));
        toast('已装备 ' + x.n);
      } else {
        const to = t.dataset.store;
        if (to === x.w){ return; }
        await api('/api/dim/move', json({iid: x.i, h: x.h, to: to, frm: x.w}));
        toast(to === 'vault' ? '已移入仓库 ' + x.n : '已移到 ' + x.n);
      }
      reloadSoon();
    } catch(err){ toast(err.message, true); }
  });
}
function nearestTarget(el){
  if (!el || !el.closest) return null;
  return el.closest('[data-slot-bucket]') || el.closest('[data-store]');
}

// ---- 右键菜单（DIM item actions） ----
function openMenu(x, ev){
  const m = $('#menu');
  const isVault = x.w === 'vault';
  let h = '<div class="mh">' + esc(x.n) + (x.pw ? ' · 光等 ' + x.pw : '') + '</div>';
  const movable = !!x.i;    // 没实例 id 的堆叠物品搬不动，菜单里就别给搬运项
  if (movable && x.eqo){
    if (isVault) (ST.chars || []).forEach(c => { if (x.cx & (1 << c.cls))
      h += '<div class="mi" data-a="equip" data-c="' + c.id + '">装备到' + esc(c.clsName) + '</div>'; });
    else h += '<div class="mi" data-a="equip" data-c="' + x.w + '">装备</div>';
  }
  if (movable){
    h += '<div class="sep"></div>';
    if (!isVault){
      if (x.b === 215593132) h += '<div class="mi" data-a="char" data-c="' + x.w + '">从邮政官取回</div>';
      else if (x.vc) h += '<div class="mi" data-a="vault">移到仓库</div>';
      (ST.chars || []).forEach(c => { if (c.id !== x.w && (x.cx & (1 << c.cls)))
        h += '<div class="mi" data-a="char" data-c="' + c.id + '">移到' + esc(c.clsName) + '</div>'; });
    } else {
      (ST.chars || []).forEach(c => { if (x.cx & (1 << c.cls))
        h += '<div class="mi" data-a="char" data-c="' + c.id + '">移到' + esc(c.clsName) + '</div>'; });
    }
  }
  h += '<div class="sep"></div><div class="mh">标签</div>';
  TAGS.forEach(t => h += '<div class="mi kbd" data-a="tag" data-t="' + t.t + '" data-k="' +
    t.key + '"><span><span class="sw" style="color:' + t.c + '">' + t.sym + '</span>' + t.n +
    '</span></div>');
  h += '<div class="mi kbd" data-a="tag" data-t="" data-k="shift+0">清除标签</div>';
  h += '<div class="sep"></div><div class="mi" data-a="detail">详情</div>';
  m.innerHTML = h;
  m.classList.remove('hidden');
  m.style.left = Math.min(ev.clientX, innerWidth - m.offsetWidth - 8) + 'px';
  m.style.top = Math.min(ev.clientY, innerHeight - m.offsetHeight - 8) + 'px';
  m.onclick = async e2 => {
    const d = e2.target.closest('[data-a]'); if (!d) return;
    m.classList.add('hidden');
    const a = d.dataset.a;
    if (a === 'detail'){ closePop(); openPop(x, m); return; }
    try {
      if (a === 'tag'){ await setTag(x, d.dataset.t);
        toast(d.dataset.t ? '已标记「' + TAG_MAP[d.dataset.t].n + '」' : '已清除标签'); return; }
      if (a === 'equip'){ await api('/api/dim/equip', json({iid: x.i, h: x.h, char: d.dataset.c, frm: x.w}));
        toast('已装备 ' + x.n); closePop(); }
      else if (a === 'vault'){ await api('/api/dim/move', json({iid: x.i, h: x.h, to: 'vault', frm: x.w}));
        toast('已移入仓库 ' + x.n); }
      else { await api('/api/dim/move', json({iid: x.i, h: x.h, to: d.dataset.c, frm: x.w}));
        toast('已移到 ' + x.n); }
      reloadSoon();
    } catch(err){ toast(err.message, true); }
  };
}

// ---- 标签写入（本地存储，DIM 也是本地） ----
let TAGS_DB = {};
async function loadTags(){ 
  const r = await api('/api/dim/tags'); TAGS_DB = r.tags || {}; }
async function setTag(x, tag){
  await api('/api/dim/tag', json({iid: x.i, h: x.h, tag: tag}));
  TAGS_DB[x.tid] = Object.assign({}, TAGS_DB[x.tid], {t: tag});
  if (!tag && !TAGS_DB[x.tid].n) delete TAGS_DB[x.tid];
  if (window.rerender) window.rerender();
  const p = $('#pop');
  if (p && !p.classList.contains('hidden'))
    $$('.tagbtn[data-tag]', p).forEach(b => b.classList.toggle('on',
      (x.tid in TAGS_DB) && (TAGS_DB[x.tid].t || '') === b.dataset.tag));
}
// 全局动作（快捷键/右键）
function bindGlobal(root){
  root.addEventListener('contextmenu', e => {
    const it = e.target.closest('.item'); if (!it || !it.dataset.i) return;
    e.preventDefault(); cancelHover(); closePop(); openMenu(itemOf(it.dataset.i), e);
  });
  root.addEventListener('mousedown', e => {
    const it = e.target.closest('.item');
    if (it && e.button === 0 && !e.shiftKey){ /* 左键交给 click */ }
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('#menu')) $('#menu').classList.add('hidden');
    if (!e.target.closest('#pop') && !e.target.closest('.item') &&
        !e.target.closest('.tagbtn')) closePop();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape'){ closePop(); $('#menu').classList.add('hidden'); drawSet(false); }
    const it = document.querySelector('.item.hl');
    if (!it) return;
    const x = itemOf(it.dataset.i);
    if (!x) return;
    if (e.shiftKey && TAG_KEYS[e.key.replace('!', '1')]){
      setTag(x, tagOf(x) === TAG_KEYS[e.key.replace('!', '1')] ? '' : TAG_KEYS[e.key.replace('!', '1')]);
      e.preventDefault();
    }
    if (e.key === 'e' && !e.shiftKey && x.eqo && x.w !== 'vault'){
      api('/api/dim/equip', json({iid: x.i, h: x.h, char: x.w, frm: x.w})).then(
        () => { toast('已装备 ' + x.n); reloadSoon(); }, err => toast(err.message, true));
    }
  });
  let last = null;
  root.addEventListener('mouseover', e => {
    const it = e.target.closest('.item');
    if (it === last) return;
    if (last) last.classList.remove('hl');
    last = it;
    if (it) it.classList.add('hl');
  });
}
function itemOf(iid){ return (ST.index || {})[iid]; }
// 写操作后 Bungie 快照有十几秒滞后：先本地挪、再对账（沿用原来的做法）
function reloadSoon(){ if (window.reload) { window.reload(1); window.reload(9000); window.reload(20000); } }

// ---- 设置抽屉 ----
function drawSet(show){
  let d = $('#drawer');
  if (!show){ if (d) d.remove(); const o = $('.ovl'); if (o) o.remove(); return; }
  if (d) return;
  const sorts = [['name','名称'],['type','类型'],['rarity','稀有度'],['power','光等'],
    ['quantity','数量'],['tier','护甲数值']];
  const itemSorts = [['name','名称'],['type','类型'],['rarity','稀有度'],['quantity','数量'],
    ['power','光等'],['tier','护甲数值']];
  const o = document.createElement('div'); o.className = 'ovl'; o.onclick = () => drawSet(false);
  document.body.appendChild(o);
  d = document.createElement('div'); d.id = 'drawer'; d.className = 'drawer';
  d.innerHTML = '<h3>设置 <button class="iconbtn" data-x>✕</button></h3>' +
    '<div class="dset"><label>界面</label>' +
      '<div class="row"><span>图标大小</span><input type="number" min="32" max="96" step="2" value="' +
        SET.size + '" data-k="size"></div>' +
      '<div class="row"><span>默认排序方式</span><select data-k="sort">' + sorts.map(s =>
        '<option value="' + s[0] + '"' + (SET.sort === s[0] ? ' selected' : '') + '>' + s[1] +
        '</option>').join('') + '</select></div>' +
      '<div class="row"><span>物品排序方式</span><select data-k="itemSort">' + itemSorts.map(s =>
        '<option value="' + s[0] + '"' + (SET.itemSort === s[0] ? ' selected' : '') + '>' + s[1] +
        '</option>').join('') + '</select></div>' +
      '<div class="row"><span>排序方向</span><select data-k="sortDir">' +
        '<option value="-1"' + (SET.sortDir < 0 ? ' selected' : '') + '>降序</option>' +
        '<option value="1"' + (SET.sortDir > 0 ? ' selected' : '') + '>升序</option></select></div>' +
      '<div class="row"><label class="chk"><input type="checkbox" data-k="hideJunk"' +
        (SET.hideJunk ? ' checked' : '') + '> 隐藏已标记丢弃</label></div>' +
      '<div class="row"><label class="chk"><input type="checkbox" data-k="equippedFirst"' +
        (SET.equippedFirst ? ' checked' : '') + '> 装备优先置顶</label></div>' +
    '</div>' +
    '<div class="dset"><label>标签</label>' +
      '<div class="mut">鼠标悬停/左键 = 物品弹窗，右键 = 操作菜单。快捷键：</div>' +
      '<div class="mut" style="margin-top:4px">' + TAGS.map(t =>
        '<div><span style="color:' + t.c + '">' + t.sym + '</span> ' + t.n + ' → ' + t.key +
        '</div>').join('') + '<div>∅ 清除标签 → shift+0</div><div>e 装备当前高亮物品</div>' +
      '</div></div>' +
    '<div class="dset"><label>说明</label><div class="mut">标签与备注存在本机（DIM 官方也是本地存，' +
      'Bungie 没有开放标签接口），备份/迁移不需要额外操作。</div></div>';
  document.body.appendChild(d);
  d.onclick = e => { if (e.target.closest('[data-x]')) drawSet(false); };
  d.onchange = e => {
    const k = e.target.dataset.k; if (!k) return;
    if (e.target.type === 'checkbox') SET[k] = e.target.checked;
    else if (e.target.type === 'number') SET[k] = Number(e.target.value) || 48;
    else if (k === 'sortDir') SET[k] = Number(e.target.value);
    else SET[k] = e.target.value;
    saveSet();
    if (window.rerender) window.rerender();
  };
}
function shellInit(){
  bindHover(document); bindDnD(document); bindGlobal(document);
  const sb = $('#q');
  if (sb) sb.addEventListener('input', () => { parseQ(sb.value); if (window.filterNow) window.filterNow(); });
  const st = $('#btn-set'); if (st) st.onclick = () => drawSet(true);
  // 外壳里已经有 #menu / #pop 了（页面模板给的），这里只兜底创建一次
  if (!$('#menu')){
    const mn = document.createElement('div'); mn.id = 'menu'; mn.className = 'menu hidden';
    document.body.appendChild(mn);
  }
  if (!$('#pop')){
    const pp = document.createElement('div'); pp.id = 'pop'; pp.className = 'pop hidden';
    document.body.appendChild(pp);
  }
}

shellInit();
"""

SHELL_JS = SHELL_JS.replace("__TAGS__", json.dumps(TAGS, ensure_ascii=False))

NAV = [("/dim", "背包"), ("/dim/triumphs", "进度"), ("/dim/loadouts", "配装"),
       ("/dim/optimizer", "配装器"), ("/dim/manage", "管理器"), ("/", "主站")]


def shell(active: str, title: str, body: str, page_js: str, page_css: str = "") -> str:
    nav = ""
    for href, txt in NAV:
        nav += f"<a class='{'on' if href == active else ''}' href='{href}'>{txt}</a>"
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,"><title>{title} · DIM</title><style>{SHELL_CSS}{page_css}</style></head><body>
<header class="hdr">
  <span class="logo"><i></i>DIM</span>
  <nav>{nav}</nav>
  <span class="sp"></span>
  <div class="swap"><input id="q" type="text" autocomplete="off"
    placeholder="搜索物品…（tag:favorite / is:equipped / is:weapon / is:armor / notes:xx / not:xx）"></div>
  <button class="iconbtn" id="btn-set" title="设置">⚙</button>
</header>
<div class="wrap">{body}</div>
<div id="menu" class="menu hidden"></div>
<div id="pop" class="pop hidden"></div>
<script>{SHELL_JS}</script>
<script>{page_js}</script>
</body></html>"""


# 页面自定义的补充样式（配装页/配装器用）
EXTRA_CSS = """
.ldrow{display:flex;gap:12px;align-items:flex-start;background:rgba(255,255,255,.04);
  border-radius:6px;padding:9px;margin-bottom:8px}
.ldrow .lm{flex:0 0 190px}
.ldrow .lm .ln{font-weight:600;font-size:14px;display:flex;gap:6px;align-items:center}
.ldrow .lm .ln img{width:22px;height:22px;border-radius:4px}
.ldrow .lm .ls{font-size:11px;color:var(--text-2);margin-top:3px}
.ldrow .lslot{margin-right:8px}
.ldrow .lslot .sn{font-size:10px;color:var(--text-2);text-align:center;width:var(--item-size);
  overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.ldrow .acts{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;flex:0 0 120px}
.inld-hd{display:flex;align-items:center;gap:8px;margin:14px 0 6px;font-size:13px;
  border-bottom:1px solid rgba(255,255,255,.12);padding-bottom:4px}
.lgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px}
.lcard{position:relative;border-radius:6px;overflow:hidden;background:rgba(255,255,255,.04);
  display:flex;gap:8px;padding:7px}
.lcard .bg{position:absolute;inset:0;opacity:.16;background-size:cover;background-position:center}
.lcard .in{position:relative;min-width:0;flex:1;display:flex;flex-direction:column}
.lcard .lt{font-weight:600;font-size:13px;display:flex;gap:5px;align-items:center;
  overflow:hidden;white-space:nowrap}
.lcard .lt img{width:20px;height:20px;border-radius:4px;flex:none}
.lcard .act{display:flex;gap:4px;justify-content:flex-end;margin-top:auto;
  padding-top:6px}
.optcols{display:grid;grid-template-columns:250px minmax(0,1fr);gap:10px;align-items:start}
@media (max-width:1100px){.optcols{grid-template-columns:1fr}}
.optpanel{background:rgba(255,255,255,.04);border-radius:6px;padding:9px;
  max-height:calc(100vh - 118px);overflow:auto}
.optpanel h4{margin:0 0 7px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-2);display:flex;justify-content:space-between}
.optpanel h4+div{margin-bottom:12px}
.prio{display:flex;align-items:center;gap:5px;margin:3px 0}
.prio .pnum{width:16px;height:16px;border-radius:50%;background:var(--accent);color:#000;
  font-size:11px;font-weight:700;display:grid;place-items:center;flex:none}
.prio .pnm{flex:1;font-size:13px}
.prio input[type=number]{width:56px}
.setrow{border-left:3px solid transparent;background:rgba(255,255,255,.04);border-radius:6px;
  padding:8px;margin-bottom:8px}
.setrow.chosen{border-left-color:var(--accent);background:rgba(232,165,52,.1)}
.setrow .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tot{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin:3px 0 6px}
.tot b{font-size:13px}
.tot .hit{color:var(--accent)}
.sgrid{display:flex;gap:5px;flex-wrap:wrap}
"""
