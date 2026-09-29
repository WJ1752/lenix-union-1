"""DIM 板块：背包/仓库、进度、配装、配装器、管理器 五个页面 + 接口。

界面对齐 DIM（配色/格子/弹窗/标签都照官方样式写，见 dim_ui.py 的说明），功能对齐 DIM：
  * 背包页：鼠标悬停/左键出物品弹窗，右键出操作菜单，**拖拽**物品到角色/仓库/装备槽即搬运装备，
    物品可以打标签（收藏/保留/丢弃/注入/归档）和写备注，搜索支持 DIM 的 is:/tag:/notes:/not: 语法
  * 配装页：游戏内配装 + 自建配装（DIM 配装），能创建/编辑/编辑副本/分享/导入/应用，配装编辑器按槽位挑装备
  * 配装器：DIM Loadout Optimizer 同款——属性优先级和范围 + 假定大师之作 + 指定异域护甲，
    搜索护甲组合并保存/装备
  * 管理器：表格式的批量整理（多选 + 批量打标签/搬运）

写操作只走官方接口：TransferItem / EquipItem / SetItemLockState。标签/备注/自建配装存本地
（dim_user.py），因为 Bungie 没有开放标签接口（实测 SetTag 404）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import dim_data as dm
import dim_opt as do
import dim_ui as ui
import dim_user as du

router = APIRouter()

TAG_URL = "https://www.bungie.net"


def _merge_tags(r: dict) -> dict:
    """背包接口顺带把本地标签带下去，前端铺格子时就能画出标签图标"""
    r["tags"] = du.tags()
    return r


def _err(msg: str, code: int = 200) -> JSONResponse:
    """接口统一回 {ok:false, error}，前端好弹 toast（不能吐 HTML 错误页，fetch 解不了）"""
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


# ============================ 背包 / 仓库页 ============================

INV_JS = r"""
let ST = null;
// 装备槽显示顺序（跟游戏里装备页一致）
const EQ_ORDER = [1498876634, 2465295065, 953998645, 3448274439, 3551918588, 14239492,
                  20886954, 1585787867, 3284755031, 4023194814, 2025709351, 284967655,
                  4274335291, 3683254069, 1506418338];
const BK_ORDER = ['动能武器','能量武器','威能武器','头盔','臂铠','胸部护甲','腿部护甲','职业护甲',
                  '分支职业','机灵','载具','飞船','徽标','终结者','终结技','着色器','表情','表情动作',
                  '模组','改装模组','消耗品','材料','任务','任务步骤','记忆水晶','其他'];
const POST_BUCKET = 215593132;
let CHIP = '';

function buildIndex(){
  ST.index = {};
  const add = l => { for (const x of l || []) ST.index[x.tid] = x; };
  for (const c of ST.chars){ add(c.equipped); add(c.bag); add(c.post); }
  add(ST.vault);
}
// 索引/拖拽/弹窗统一用 tid 当键：没有实例 id 的堆叠物品退回 "h<物品hash>"，不会互相串
function tile(x){
  const cls = ['item'];
  if (x.mw) cls.push('mw');
  if (x.eq) cls.push('eq');
  if (!matchQ(x)) cls.push('searchHidden');   // 不命中的压暗保留位置（DIM 同款）
  let ic = '';
  if (x.s & 1) ic += '<span class="ic-t" style="color:#fff" title="已锁定">🔒</span>';
  ic += tagHTML(x);
  return '<div class="' + cls.join(' ') + '" data-i="' + esc(x.tid) + '" data-h="' + x.h +
    '" draggable="true" title="' + esc(x.n + (x.ty ? ' · ' + x.ty : '') +
    (x.pw ? ' · 光等 ' + x.pw : '')) + '">' +
    '<div class="item-img r' + (x.tt || 0) + '">' +
      (x.wm ? '<span class="wm" style="background-image:url(' + x.wm + ')"></span>' : '') +
      '<img class="ic" src="' + x.ic + '" loading="lazy" alt="">' +
      (x.en ? '<span class="etop">' + x.en + '</span>' : '') +
    '</div>' +
    (x.pw ? '<span class="vl">' + x.pw + '</span>' : '') +
    (x.q > 1 ? '<span class="qty">' + x.q + '</span>' : '') +
    (ic ? '<span class="icons">' + ic + '</span>' : '') + '</div>';
}
// 桶名 → 显示顺序
function rank(name){ const i = BK_ORDER.indexOf(name); return i < 0 ? 999 : i; }
function groups(list, meta){
  const m = {};
  for (const x of list || []) (m[meta.bucketNames[String(x.db)] || '其他'] =
    m[meta.bucketNames[String(x.db)] || '其他'] || []).push(x);
  return Object.keys(m).map(k => ({n: k, items: m[k].sort(cmpItems)}))
    .sort((a, b) => rank(a.n) - rank(b.n) || b.items.length - a.items.length);
}
function secHTML(name, right, items, ds, full){
  const hit = items.filter(matchQ);
  const style = (hit.length || !QTERMS.length) ? '' : ' style="display:none"';
  return '<div class="sec' + (full ? ' full' : '') + '" data-store="' + esc(ds) + '"' + style + '>' +
    '<div class="sec-hd"><b>' + esc(name) + '</b><span>' + esc(right) + '</span></div>' +
    '<div class="grid">' + items.map(tile).join('') + '</div></div>';
}
function eqHTML(c, meta){
  const slots = [];
  for (const b of EQ_ORDER){
    const x = c.equipped.find(y => y.b === b) || c.equipped.find(y => y.db === b);
    const nm = meta.bucketNames[String(b)] || '';
    if (x) slots.push('<div class="slot" data-store="' + c.id + '" data-slot-bucket="' + b +
      '" title="' + esc(nm) + '">' + tile(x) + '</div>');
    else if (meta.mainSlots.indexOf(b) >= 0)
      slots.push('<div class="slot" data-store="' + c.id + '" data-slot-bucket="' + b +
        '" title="' + esc(nm) + '：空"><div class="empty-slot"></div></div>');
  }
  return '<div class="sec" data-store="' + c.id + '">' +
    '<div class="sec-hd"><b>已装备</b><span>' + esc(c.clsName) + '</span></div>' +
    '<div class="slots">' + slots.join('') + '</div></div>';
}
function storeHTML(c, meta){
  let s = eqHTML(c, meta);
  for (const g of groups(c.bag, meta))
    s += secHTML(g.n, g.items.length + '/' + (meta.bucketCap[String(g.items[0].db)] || '?'),
                 g.items, c.id,
                 (meta.bucketCap[String(g.items[0].db)] || 0) > 0 &&
                 g.items.length >= (meta.bucketCap[String(g.items[0].db)] || 0));
  if (c.post.length) s += secHTML('邮政官', c.post.length + '/' + c.postCap, c.post, c.id);
  return '<div class="store" data-store="' + c.id + '">' +
    '<div class="store-head" style="background-image:url(' + c.emblem + ')">' +
      '<span class="cn">' + esc(c.clsName) + '</span><span class="cl">' + c.light + '</span>' +
      '<span class="sp"></span><span class="mut">' + c.bag.length + ' 件</span></div>' +
    '<div class="store-body">' + s + '</div></div>';
}
function vaultHTML(meta){
  const vc = meta.bucketNames[String(meta.vaultBucket)] || '仓库';
  let s = '';
  for (const g of groups(meta.vault, meta))
    s += secHTML(g.n, g.items.length + ' 件', g.items, 'vault');
  return '<div class="store vault" data-store="vault">' +
    '<div class="store-head"><span class="cn">保险库</span>' +
      '<span class="cl">' + meta.vaultUsed + '</span><span class="sp"></span>' +
      '<span class="mut">/ ' + meta.vaultCap + '</span></div>' +
    '<div class="store-body">' + s + '</div></div>';
}
function chipsHTML(){
  const list = [['', '全部'], ['is:weapon', '武器'], ['is:armor', '护甲'],
    ['is:equipped', '已装备'], ['is:locked', '已锁定'], ['is:masterwork', '大师之作'],
    ['tag:favorite', '♥ 收藏'], ['tag:keep', '⚑ 保留'], ['tag:junk', '✖ 丢弃'],
    ['tag:infuse', '⚡ 注入'], ['tag:archive', '🗄 归档'], ['is:tagged', '有标签'],
    ['is:note', '有备注']];
  return '<div class="chips">' + list.map(c => '<span class="chip' +
    (CHIP === c[0] ? ' on' : '') + '" data-c="' + esc(c[0]) + '">' + esc(c[1]) +
    '</span>').join('') +
    '<span class="mut" style="margin-left:6px">拖动物品：角色 ↔ 仓库 ↔ 装备槽</span></div>';
}
function render(){
  const sy = window.scrollY;
  const meta = ST;
  document.documentElement.style.setProperty('--item-size', SET.size + 'px');
  let h = '';
  for (const c of meta.chars){
    h += storeHTML(c, meta);
  }
  h += vaultHTML(meta);
  $('#stores').innerHTML = h;
  $('#chips').innerHTML = chipsHTML();
  window.scrollTo(0, sy);
}
function rerender(){ if (ST) render(); }
function loadQ(){ parseQ(CHIP ? (CHIP + ' ' + ($('#q').value || '')) : ($('#q').value || '')); }
function filterNow(){ loadQ(); if (ST) render(); }
async function load(force){
  try {
    ST = await api('/api/dim/inventory' + (force ? '?force=1' : ''));
    TAGS_DB = ST.tags || {};
    buildIndex(); render();
  } catch(e){
    $('#stores').innerHTML = '<div class="mut">读取失败：' + esc(e.message) +
      '（需要先在「Bot 面板」授权 Bungie 账号）</div>';
  }
}
window.reload = ms => setTimeout(() => load(1), ms);
window.rerender = rerender;
window.filterNow = filterNow;
$('#chips').addEventListener('click', e => {
  const c = e.target.closest('.chip'); if (!c) return;
  CHIP = (CHIP === c.dataset.c) ? '' : c.dataset.c;
  filterNow();
});
document.addEventListener('click', e => {
  const it = e.target.closest('.item');
  if (it && it.dataset.i) openPop(itemOf(it.dataset.i), it, true);
});
load();
"""


@router.get("/dim", response_class=HTMLResponse)
async def dim_home():
    body = ('<div id="chips"></div><div id="stores" class="stores"></div>')
    return HTMLResponse(ui.shell("/dim", "背包仓库", body, INV_JS))


# ============================ 进度（成就）页 ============================

TRI_JS = r"""
let TR = null, UNDONE = false, SEL = 'all', QF = '';
function recTile(r){
  const done = r.done;
  return '<div class="rc' + (done ? ' done' : '') + '" title="' + esc(r.d || r.n) + '">' +
    '<img src="' + r.ic + '" loading="lazy" alt="">' +
    '<div class="rn">' + esc(r.n) + '</div>' +
    (r.t > 1 ? '<div class="barx' + (done ? ' g' : '') + '"><i style="width:' +
        (r.t ? (r.p / r.t * 100).toFixed(0) : 0) + '%"></i></div>' +
      '<div class="rp">' + r.p + ' / ' + r.t + '</div>' : '') +
    (done ? '<b class="ok">✓</b>' : '') + '</div>';
}
function nodeById(h, node){
  node = node || TR.cats.find(c => c.h === h);
  if (node.h === h) return node;
  for (const k of node.kids){ const f = nodeById(h, k); if (f) return f; }
  return null;
}
function collect(node, out, depth){
  out = out || [];
  for (const r of node.recs) out.push({r: r, node: node.n});
  for (const k of node.kids) collect(k, out, (depth || 0) + 1);
  return out;
}
function matchRec(x){
  const r = x.r;
  if (UNDONE && r.done) return false;
  if (!QF) return true;
  return (r.n + ' ' + r.d + ' ' + x.node).toLowerCase().includes(QF);
}
function renderTree(){
  let h = '<div class="tnode' + (SEL === 'all' ? ' on' : '') + '" data-h="all">全部成就 <span class="mut">' +
    TR.cats.reduce((a, c) => a + c.done, 0) + '/' + TR.cats.reduce((a, c) => a + c.total, 0) + '</span></div>';
  const walk = (n, d) => {
    let s = '<div class="tnode' + (SEL === n.h ? ' on' : '') + '" data-h="' + n.h +
      '" style="padding-left:' + (10 + d * 12) + 'px">' + esc(n.n) +
      ' <span class="mut">' + n.done + '/' + n.total + '</span></div>';
    for (const k of n.kids) s += walk(k, d + 1);
    return s;
  };
  for (const c of TR.cats) h += walk(c, 0);
  $('#tree').innerHTML = h;
}
function renderRecs(){
  let list = [];
  if (SEL === 'all') for (const c of TR.cats) collect(c, list);
  else { const n = nodeById(SEL); if (n) collect(n, list); }
  list = list.filter(matchRec);
  $('#rcount').textContent = list.length + ' 条';
  if (!list.length){ $('#recs').innerHTML = '<div class="mut">没有匹配的记录</div>'; return; }
  let h = '', last = null, open = false;
  for (const x of list){
    if (x.node !== last){
      if (open) h += '</div>';
      h += '<div class="rsec">' + esc(x.node) + '</div><div class="rgrid">'; open = true; last = x.node;
    }
    h += recTile(x.r);
  }
  if (open) h += '</div>';
  $('#recs').innerHTML = h;
}
async function load(){
  $('#recs').innerHTML = loading('读取成就…');
  try { TR = await api('/api/dim/triumphs'); }
  catch(e){ $('#recs').innerHTML = '<div class="mut">读取失败：' + esc(e.message) + '</div>'; return; }
  const done = TR.cats.reduce((a, c) => a + c.done, 0), tot = TR.cats.reduce((a, c) => a + c.total, 0);
  $('#score').innerHTML = '<b>' + TR.score.toLocaleString() + '</b> <span class="mut">凯旋分</span>' +
    '<span class="mut" style="margin-left:14px">成就 ' + done + ' / ' + tot + '</span>' +
    '<div class="barx" style="max-width:420px"><i class="g" style="width:' +
      (tot ? (done / tot * 100).toFixed(1) : 0) + '%"></i></div>';
  renderTree(); renderRecs();
}
window.filterNow = () => { QF = ($('#q').value || '').toLowerCase(); renderRecs(); };
$('#un').addEventListener('change', e => { UNDONE = e.target.checked; renderRecs(); });
$('#tree').addEventListener('click', e => {
  const n = e.target.closest('.tnode'); if (!n) return;
  SEL = n.dataset.h; $('#q').value = ''; QF = '';
  renderTree(); renderRecs();
});
load();
"""

TRI_CSS = """
.tcols{display:grid;grid-template-columns:290px minmax(0,1fr);gap:10px;align-items:start}
@media (max-width:900px){.tcols{grid-template-columns:1fr}}
.tcol{background:rgba(255,255,255,.04);border-radius:6px;padding:9px;
      max-height:calc(100vh - 150px);overflow:auto}
.tnode{padding:5px 9px;border-radius:5px;font-size:13px;cursor:pointer;white-space:nowrap;
       overflow:hidden;text-overflow:ellipsis}
.tnode:hover{background:rgba(255,255,255,.12)}
.tnode.on{background:var(--accent);color:#000}
.tnode.on .mut{color:#3a2a00}
.rsec{margin:10px 0 6px;font-size:12px;color:var(--text-2);
      border-bottom:1px solid rgba(255,255,255,.12);padding-bottom:4px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px}
.rc{position:relative;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.18);
    border-radius:6px;padding:8px;text-align:center}
.rc.done{border-color:#2f7c4f;background:rgba(47,124,79,.16)}
.rc img{width:46px;height:46px;border-radius:5px;opacity:.55}
.rc.done img{opacity:1}
.rc .rn{font-size:11px;margin-top:5px;height:30px;overflow:hidden;line-height:1.35}
.rc .rp{font-size:10px;color:var(--text-2)}
.rc .ok{position:absolute;right:5px;top:3px;color:#7dffa0;font-size:13px}
.barx{height:5px;background:rgba(0,0,0,.4);border-radius:3px;overflow:hidden;margin-top:3px}
.barx i{display:block;height:100%;background:#4b7fb5}
.barx.g i{background:#3cba6e}
"""


@router.get("/dim/triumphs", response_class=HTMLResponse)
async def dim_triumphs():
    body = """<div class="bar2"><span id="score"></span>
  <label class="chk"><input type="checkbox" id="un"> 只看未完成</label>
  <span class="mut" id="rcount"></span></div>
<div class="tcols"><div id="tree" class="tcol"></div><div id="recs" class="tcol"></div></div>"""
    return HTMLResponse(ui.shell("/dim/triumphs", "进度", body, TRI_JS, TRI_CSS))


# ============================ 配装页 ============================

LOAD_JS = r"""
let ST = null, MY = [], INGAME = null, EDIT = null, PICK = null, CMP = [];
const EQ_ORDER = [1498876634, 2465295065, 953998645, 3448274439, 3551918588, 14239492,
                  20886954, 1585787867, 3284755031, 4023194814, 2025709351, 284967655,
                  4274335291, 3683254069, 1506418338];
const TAGCOLS = ['#3d3d3d','#2b3a55','#513065','#366f42','#705536'];

function buildIndex(){
  ST.index = {}; ST.byHash = {};
  const add = l => { for (const x of l || []){ ST.index[x.tid] = x;
    if (!ST.byHash[x.h]) ST.byHash[x.h] = x; } };
  for (const c of ST.chars){ add(c.equipped); add(c.bag); add(c.post); }
  add(ST.vault);
}
function slotName(b){ return ST.bucketNames[String(b)] || ''; }
// 一件配装物品的桶：服务端补的 db 优先，其次查自己拥有的同名物品
function itemDb(it){ return it.db || (ST.byHash[it.h] || {}).db || 0; }
// 自建配装里的一件：优先用自己拥有的那件（能显示光等/属性），否则按 hash 显示参考信息
function resolve(it){
  const own = it.i ? ST.index[it.i] : null;
  if (own) return own;
  const any = ST.byHash[it.h];
  return {i: '', h: it.h, n: it.n || ('#' + it.h), ic: it.ic || '', wm: it.wm || '',
          tt: it.tt || 0, ty: it.ty || '', db: it.db || 0, pw: 0, st: {}, tid: it.i || ('h' + it.h),
          missing: 1};
}
function tile(x){
  const cls = ['item'];
  if (x.mw) cls.push('mw');
  if (x.eq) cls.push('eq');
  return '<div class="' + cls.join(' ') + '" data-i="' + esc(x.tid) + '" data-h="' + x.h +
    '" draggable="' + (x.i ? 'true' : 'false') + '" title="' + esc(x.n) + '">' +
    '<div class="item-img r' + (x.tt || 0) + '">' +
      (x.wm ? '<span class="wm" style="background-image:url(' + x.wm + ')"></span>' : '') +
      '<img class="ic" src="' + x.ic + '" loading="lazy" alt="">' +
    '</div>' + (x.pw ? '<span class="vl">' + x.pw + '</span>' : '') +
    (x.missing ? '<span class="icons"><span class="ic-t" style="color:#ff9a4d" title="未拥有">?</span></span>' : '') +
    '</div>';
}
function eqRow(c){
  const out = [];
  for (const b of EQ_ORDER){
    const x = c.equipped.find(y => y.b === b) || c.equipped.find(y => y.db === b);
    out.push('<div class="lslot">' + (x ? tile(x) : '<div class="empty-slot"></div>') +
      '<div class="sn">' + esc(slotName(b)) + '</div></div>');
  }
  return '<div class="sgrid">' + out.join('') + '</div>';
}
function myItemsRow(lo){
  const out = [];
  for (const b of EQ_ORDER){
    const it = (lo.items || []).find(x => itemDb(x) === b);
    if (!it) continue;
    out.push('<div class="lslot">' + tile(resolve(it)) +
      '<div class="sn">' + esc(slotName(b)) + '</div></div>');
  }
  for (const it of lo.items || []) if (!itemDb(it)) out.push('<div class="lslot">' + tile(resolve(it)) + '</div>');
  return '<div class="sgrid">' + out.join('') + '</div>';
}
function ingameHTML(){
  let h = '<div class="inld-hd">游戏内配装 <span class="mut">每角色 20 套，直接读游戏里的配装</span></div>';
  for (const c of INGAME.chars){
    h += '<div class="inld-hd"><b>' + esc(c.clsName) + '</b><span class="mut">光等 ' +
      c.light + ' · ' + c.loadouts.length + ' 套</span></div><div class="lgrid">';
    for (const lo of c.loadouts){
      if (!lo.items.length) continue;
      h += '<div class="lcard" data-char="' + c.id + '" data-idx="' + lo.idx + '">' +
        '<div class="bg" style="background-image:url(' + lo.color + ')"></div>' +
        '<div class="in"><div class="lt"><img src="' + lo.icon + '" alt="">' + esc(lo.name) + '</div>' +
        '<div class="lslot"><div class="sgrid">' +
          lo.items.map(x => tile(x)).join('') + '</div></div>' +
        '<div class="act"><button class="btn sm pri" data-a="apply-ig">应用</button></div>' +
        '</div></div>';
    }
    h += '</div>';
  }
  return h;
}
function curHTML(){
  let h = '<div class="inld-hd">当前装备 <span class="mut">点「保存配装」把当前这套存成 DIM 配装</span></div>';
  for (const c of ST.chars){
    h += '<div class="ldrow"><div class="lm"><div class="ln">' + esc(c.clsName) + '</div>' +
      '<div class="ls">光等 ' + c.light + '</div>' +
      '<div class="act"><button class="btn sm" data-a="save-cur" data-c="' + c.id +
      '">保存配装</button></div></div>' + eqRow(c) + '</div>';
  }
  return h;
}
function myHTML(){
  let h = '<div class="inld-hd">DIM 配装 <span class="mut">' + MY.length +
    ' 套（存在本机，跟 DIM 一样不占用游戏内 20 套配额）</span>' +
    '<span class="sp" style="flex:1"></span>' +
    '<button class="btn sm pri" data-a="new">创建配装</button>' +
    '<button class="btn sm" data-a="import">导入分享码</button></div>';
  if (!MY.length) return h + '<div class="mut">还没有自建配装。可以在上面「当前装备」里保存，' +
    '也可以在「配装器」里搜出一套再保存。</div>';
  for (const lo of MY){
    const c = ST.chars[lo.cls] || null;
    const mark = CMP.includes(lo.id) ? ' chosen' : '';
    h += '<div class="ldrow' + mark + '" data-id="' + lo.id + '">' +
      '<div class="lm"><div class="ln">' +
        (lo.icon ? '<img src="' + lo.icon + '" alt="">' : '') + esc(lo.name) + '</div>' +
        '<div class="ls">' + esc(c ? c.clsName : '通用') + ' · ' +
        (lo.items || []).length + ' 件' + (lo.notes ? ' · ' + esc(lo.notes) : '') + '</div>' +
      '</div>' + myItemsRow(lo) +
      '<div class="acts">' +
        '<button class="btn sm pri" data-a="apply-my" data-c="' + (c ? c.id : '') + '">应用</button>' +
        '<button class="btn sm" data-a="edit">编辑</button>' +
        '<button class="btn sm" data-a="dup">编辑副本</button>' +
        '<button class="btn sm" data-a="share">分享</button>' +
        '<button class="btn sm" data-a="cmp">' + (CMP.includes(lo.id) ? '取消对比' : '对比') + '</button>' +
        '<button class="btn sm" data-a="del">删除</button>' +
      '</div></div>';
  }
  return h;
}
function cmpHTML(){
  if (CMP.length !== 2) return '';
  const a = MY.find(x => x.id === CMP[0]), b = MY.find(x => x.id === CMP[1]);
  if (!a || !b) return '';
  const statSets = [[], []];
  for (const [k, lo] of [[0, a], [1, b]]){
    const tot = {};
    for (const it of lo.items || []){
      const x = resolve(it); if (!x.st) continue;
      for (const h in x.st) tot[h] = (tot[h] || 0) + x.st[h];
    }
    statSets[k] = tot;
  }
  const keys = new Set([...Object.keys(statSets[0]), ...Object.keys(statSets[1])]);
  let rows = '';
  for (const h of keys){
    const v1 = statSets[0][h] || 0, v2 = statSets[1][h] || 0;
    const d = v2 - v1;
    rows += '<div class="tot"><b>' + esc((ST.statNames || {})[h] || h) + '</b>' +
      '<span class="mut">' + a.name + ' ' + v1 + '</span>' +
      '<span class="mut">' + b.name + ' ' + v2 + '</span>' +
      '<b style="color:' + (d > 0 ? '#7dffa0' : d < 0 ? '#ff9a4d' : '#aaa') + '">' +
      (d > 0 ? '+' : '') + d + '</b></div>';
  }
  return '<div class="ldrow"><div class="lm"><div class="ln">对比配装</div>' +
    '<div class="ls">只比属性总和</div><div style="margin-top:5px">' +
    '<button class="btn sm" data-a="cmp-clear">结束对比</button></div></div><div>' + rows + '</div></div>';
}
function render(){
  $('#body').innerHTML = (INGAME ? ingameHTML() : '') + '<div class="inld-hd">' +
    '<b>DIM 配装</b></div>' + cmpHTML() + myHTML() + curHTML();
  $('#editor').innerHTML = EDIT ? editHTML() : '';
}
// ---- 配装编辑器：按槽位挑装备 ----
function editHTML(){
  if (!EDIT) return '';
  const cls = Number(EDIT.cls), c = ST.chars[cls];
  const used = {};
  (EDIT.items || []).forEach(x => used[x.h] = 1);
  let slots = '';
  for (const b of EQ_ORDER){
    const it = (EDIT.items || []).find(x => itemDb(x) === b);
    // 任何槽位都能点开换装：有装备的点槽位背景/名字换，✕ 是移除
    slots += '<div class="slot" data-pick="' + b + '" title="点这里换装">' +
      (it ? '<span class="rm" data-rm="' + it.h + '">✕</span>' + tile(resolve(it))
          : '<div class="empty-slot"></div>') +
      '<div class="sn">' + esc(slotName(b)) + '</div></div>';
  }
  let picks = '';
  if (PICK){
    const cand = candidates(PICK);
    picks = '<div class="pick"><div class="ph">' + esc(slotName(Number(PICK))) + '：选一件</div>' +
      '<div class="plist">' + (cand.length ? cand.map(tile).join('') :
        '<div class="mut">没有可用的（该类角色没拥有这个槽的装备）</div>') + '</div></div>';
  }
  const icons = (ST.loadoutIcons || []).slice(0, 24).map(u =>
    '<img class="pico' + (EDIT.icon === u ? ' on' : '') + '" src="' + u + '" data-icon="' + u + '">').join('');
  const colors = TAGCOLS.map(u =>
    '<span class="pcol' + (EDIT.color === u ? ' on' : '') + '" style="background:' + u +
    '" data-color="' + u + '"></span>').join('');
  return '<div class="edwrap" data-nohover><div class="inld-hd"><b>配装编辑器</b>' +
    '<span class="mut">' + esc(c ? c.clsName : '') + '</span></div>' +
    '<div class="bar2"><input type="text" id="lname" value="' + esc(EDIT.name || '') +
      '" placeholder="配装名称" style="min-width:220px">' +
      '<label class="chk">职业 <select id="lcls">' +
        ST.chars.map((c2, i) => '<option value="' + i + '"' + (Number(EDIT.cls) === i ? ' selected' : '') +
          '>' + esc(c2.clsName) + '</option>').join('') + '</select></label>' +
      '<input type="text" id="lnotes" value="' + esc(EDIT.notes || '') + '" placeholder="备注（可选）" style="min-width:180px">' +
    '</div><div class="slots">' + slots + '</div>' +
    (picks || '<div class="mut" style="margin:6px 0">点任意槽位挑/换装备，✕ 移除。</div>') +
    '<div class="bar2" style="margin-top:8px"><span class="mut">图标</span>' + icons +
      '<span class="mut" style="margin-left:10px">底色</span>' + colors + '</div>' +
    '<div class="bar2"><button class="btn pri" data-a="save">保存</button>' +
      '<button class="btn" data-a="cancel">取消</button>' +
      (EDIT.id ? '<button class="btn" data-a="share">分享</button>' : '') +
      '<span class="mut">空的槽位不会写进配装</span></div></div>';
}
function candidates(b){
  b = Number(b);                 // dataset 出来是字符串，跟 x.db（数字）严格比会永远不相等
  const cls = Number(EDIT.cls), out = [];
  const push = l => { for (const x of l || []) if (x.db === b && (x.cls === cls || x.cls === 3)) out.push(x); };
  const c = ST.chars[cls];
  if (c){ push(c.bag); push(c.equipped); }
  push(ST.vault);
  const seen = {};
  return out.filter(x => !seen[x.i] && (seen[x.i] = 1)).sort(cmpItems).slice(0, 120);
}
function newEdit(cls){
  return {id: '', name: '', cls: cls || 0, icon: '', color: TAGCOLS[1], notes: '', items: []};
}
async function load(){
  $('#body').innerHTML = loading('读取配装…');
  try {
    const [inv, ing, my] = await Promise.all([
      api('/api/dim/inventory'), api('/api/dim/loadouts'), api('/api/dim/my')]);
    ST = inv; TAGS_DB = inv.tags || {}; buildIndex();
    INGAME = ing; MY = my.loadouts || []; ST.loadoutIcons = my.icons || [];
    render();
  } catch(e){ $('#body').innerHTML = '<div class="mut">读取失败：' + esc(e.message) + '</div>'; }
}
window.reload = ms => setTimeout(load, ms);
window.rerender = render;
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-a]');
  const row = e.target.closest('[data-id]');
  const lo = row ? MY.find(x => x.id === row.dataset.id) : null;
  if (!b) return;
  const a = b.dataset.a;
  try {
    if (a === 'new'){ EDIT = newEdit(0); PICK = null; render(); return; }
    if (a === 'edit'){ EDIT = JSON.parse(JSON.stringify(lo)); PICK = null; render(); return; }
    if (a === 'dup'){ const r = await api('/api/dim/my/dup', json({id: lo.id})); toast('已创建副本');
      await load(); return; }
    if (a === 'del'){ if (!confirm('删除配装「' + lo.name + '」？')) return;
      await api('/api/dim/my/del', json({id: lo.id})); toast('已删除'); await load(); return; }
    if (a === 'share'){
      const r = await api('/api/dim/my/share', json({id: lo.id || ''}));
      const url = location.origin + '/dim/loadout?d=' + r.code;
      prompt('分享链接（复制发给别人/自己）', url);
      try { await navigator.clipboard.writeText(url); toast('分享链接已复制'); } catch(err){}
      return;
    }
    if (a === 'cmp'){ const i = CMP.indexOf(lo.id);
      if (i >= 0) CMP.splice(i, 1); else { CMP.push(lo.id); if (CMP.length > 2) CMP.shift(); }
      render(); return; }
    if (a === 'cmp-clear'){ CMP = []; render(); return; }
    if (a === 'apply-my'){
      const r = await api('/api/dim/apply', json({char: b.dataset.c || (ST.chars[Number(lo.cls)] || {}).id,
        items: lo.items}));
      toast('已装备 ' + r.equipped + ' 件' + (r.moved ? '（搬运 ' + r.moved + '）' : '') +
        (r.errors && r.errors.length ? '，' + r.errors.length + ' 件失败：' + r.errors[0] : ''),
        !!(r.errors && r.errors.length));
      window.reload(5000); window.reload(18000); return;
    }
    if (a === 'apply-ig'){
      const card = e.target.closest('.lcard');
      const c = INGAME.chars.find(x => x.id === card.dataset.char);
      const l = c.loadouts.find(x => String(x.idx) === card.dataset.idx);
      if (!confirm('把「' + l.name + '」装到' + c.clsName + '身上？')) return;
      const r = await api('/api/dim/apply', json({char: c.id,
        items: l.items.filter(x => x.h).map(x => ({h: x.h, i: x.i}))}));
      toast('已装备 ' + r.equipped + ' 件' + (r.errors && r.errors.length ?
        '，' + r.errors.length + ' 件失败' : ''), !!(r.errors && r.errors.length));
      window.reload(5000); return;
    }
    if (a === 'save-cur'){
      const c = ST.chars.find(x => x.id === b.dataset.c);
      EDIT = newEdit(c.cls);
      EDIT.name = c.clsName + ' 当前装备';
      EDIT.items = c.equipped.filter(x => EQ_ORDER.indexOf(x.b) >= 0)
        .map(x => ({h: x.h, i: x.i}));
      PICK = null; render(); return;
    }
    if (a === 'cancel'){ EDIT = null; PICK = null; render(); return; }
    if (a === 'save'){
      EDIT.name = ($('#lname') || {}).value || EDIT.name || '未命名配装';
      EDIT.notes = ($('#lnotes') || {}).value || '';
      EDIT.cls = Number(($('#lcls') || {}).value || EDIT.cls);
      const r = await api('/api/dim/my/save', json(EDIT));
      toast('已保存「' + r.loadout.name + '」'); EDIT = null; PICK = null; await load(); return;
    }
    if (a === 'import'){
      const code = prompt('粘贴分享码（/dim/loadout?d= 后面那串）');
      if (!code) return;
      const r = await api('/api/dim/my/import',
        json({code: code.replace(/^.*[?&]d=/, '')}));
      toast('已导入「' + r.loadout.name + '」'); await load(); return;
    }
  } catch(err){ toast(err.message, true); }
});
document.addEventListener('click', e => {
  const rm = e.target.closest('[data-rm]');
  if (rm && EDIT){
    EDIT.items = EDIT.items.filter(x => String(x.h) !== rm.dataset.rm); render(); return;
  }
  // 编辑器里点槽位（连同里面的装备）= 挑/换这件，不再弹物品详情，否则想换装根本没地方点
  const sl = e.target.closest('#editor .slot[data-pick]');
  if (sl && EDIT){
    PICK = (PICK === sl.dataset.pick) ? null : sl.dataset.pick; render(); return;
  }
  const ic = e.target.closest('[data-icon]');
  if (ic && EDIT){ EDIT.icon = ic.dataset.icon; render(); return; }
  const co = e.target.closest('[data-color]');
  if (co && EDIT){ EDIT.color = co.dataset.color; render(); return; }
  const pk = e.target.closest('.pick .item');
  if (pk && EDIT && PICK){
    const x = itemOf(pk.dataset.i) || (ST.vault.find(y => y.i === pk.dataset.i));
    if (!x) return;
    const b = Number(PICK);
    EDIT.items = EDIT.items.filter(it => itemDb(it) !== b);
    EDIT.items.push({h: x.h, i: x.i});
    PICK = null; render(); return;
  }
  const it = e.target.closest('.item');
  if (it && it.dataset.i && !it.closest('.pick') && !it.closest('#editor .slot'))
    openPop(itemOf(it.dataset.i), it, true);
});
document.addEventListener('change', () => {
  if (!EDIT) return;
  const n = $('#lname'), c = $('#lcls');
  if (n) EDIT.name = n.value;
  if (c) EDIT.cls = Number(c.value);
  const nt = $('#lnotes'); if (nt) EDIT.notes = nt.value;
});
load();
"""

LOAD_CSS = ui.EXTRA_CSS + """
.slots{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0}
.slot{position:relative;padding:2px;border-radius:3px}
.slot .sn{font-size:10px;color:var(--text-2);text-align:center;width:var(--item-size);
  overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.slot .rm{position:absolute;right:2px;top:2px;z-index:3;background:rgba(0,0,0,.7);color:#ff9a4d;
  border-radius:3px;font-size:11px;padding:0 3px;cursor:pointer}
.edwrap{background:rgba(255,255,255,.05);border-radius:6px;padding:10px;margin:4px 0 14px}
.pick{margin:6px 0;background:rgba(0,0,0,.3);border-radius:6px;padding:7px}
.pick .ph{font-size:12px;color:var(--text-2);margin-bottom:5px}
.pick .plist{display:flex;gap:3px;flex-wrap:wrap;max-height:190px;overflow:auto}
.pico{width:22px;height:22px;border-radius:3px;cursor:pointer;border:2px solid transparent}
.pico.on{border-color:var(--accent)}
.pcol{width:22px;height:22px;border-radius:3px;cursor:pointer;border:2px solid transparent;display:inline-block}
.pcol.on{border-color:var(--accent)}
"""


@router.get("/dim/loadouts", response_class=HTMLResponse)
async def dim_loadouts():
    body = '<div id="editor"></div><div id="body"></div>'
    return HTMLResponse(ui.shell("/dim/loadouts", "配装", body, LOAD_JS, LOAD_CSS))


@router.get("/dim/loadout", response_class=HTMLResponse)
async def dim_loadout_import():
    """分享链接落地页：?d=<分享码> 自动导入后再跳到配装页"""
    js = r"""
const code = (location.search.match(/[?&]d=([^&]+)/) || [])[1] || '';
(async () => {
  if (!code){ document.body.innerHTML = '<div class="wrap"><div class="mut">缺少分享码</div></div>'; return; }
  try {
    const r = await api('/api/dim/my/import', json({code: decodeURIComponent(code)}));
    toast('已导入「' + r.loadout.name + '」，正在跳转…');
    setTimeout(() => location.href = '/dim/loadouts', 900);
  } catch(e){ document.body.innerHTML = '<div class="wrap"><div class="mut">导入失败：' +
    esc(e.message) + '</div></div>'; }
})();
"""
    return HTMLResponse(ui.shell("/dim/loadouts", "导入配装", "", js))


# ============================ 配装器 ============================

OPT_JS = r"""
let ST = null, OPT = null, STATS = [], PARAMS = null, CMP = [];
const TAGCOLS = ['#3d3d3d','#2b3a55','#513065','#366f42','#705536'];
function buildIndex(){
  ST.index = {}; ST.byHash = {};
  const add = l => { for (const x of l || []){ ST.index[x.tid] = x; if (!ST.byHash[x.h]) ST.byHash[x.h] = x; } };
  for (const c of ST.chars){ add(c.equipped); add(c.bag); add(c.post); }
  add(ST.vault);
}
function panelHTML(){
  const cls = PARAMS.cls;
  const ex = exoticList();
  return '<div class="optpanel">' +
    '<h4>目标职业</h4><div class="chips">' + ST.chars.map((c, i) =>
      '<span class="chip' + (cls === i ? ' on' : '') + '" data-cls="' + i + '">' + esc(c.clsName) +
      '</span>').join('') + '</div>' +
    '<h4>属性优先级和范围</h4><div>' + STATS.map((s, i) => {
      const r = PARAMS.stats[s.h] || {};
      return '<div class="prio"><select data-prio="' + s.h + '" title="优先级">' +
        [1,2,3].map(p => '<option value="' + p + '"' + (Number(r.prio) === p ? ' selected' : '') +
          '>' + p + '</option>').join('') +
        '<option value="0"' + (!r.prio ? ' selected' : '') + '>-</option></select>' +
        '<span class="pnm">' + esc(s.n) + '</span>' +
        '<input type="number" min="0" max="200" value="' + (r.min || 0) + '" data-min="' + s.h + '" title="最小">' +
        '<span class="mut">~</span>' +
        '<input type="number" min="0" max="200" value="' + (r.max || 0) + '" data-max="' + s.h + '" title="最大（0=不限）">' +
        '</div>';
    }).join('') + '</div>' +
    '<h4>假定大师之作</h4><div><select data-mw>' +
      [['none','无'],['legendary','传说'],['exotic','异域']].map(o =>
        '<option value="' + o[0] + '"' + (PARAMS.mw === o[0] ? ' selected' : '') + '>' + o[1] +
        '</option>').join('') + '</select></div>' +
    '<h4>异域护甲</h4><div><select data-ex>' +
      '<option value="0">不限（一套最多 1 件）</option>' +
      ex.map(x => '<option value="' + x.h + '"' + (Number(PARAMS.exoticHash) === x.h ? ' selected' : '') +
        '>' + esc(x.n) + '</option>').join('') + '</select></div>' +
    '<h4>其它</h4><div>' +
      '<label class="chk"><input type="checkbox" data-k="exclEquipped"' +
        (PARAMS.exclEquipped ? ' checked' : '') + '> 排除已装备</label><br>' +
      '<label class="chk"><input type="checkbox" data-k="exclLocked"' +
        (PARAMS.exclLocked ? ' checked' : '') + '> 排除已锁定</label><br>' +
      '<label class="chk"><input type="checkbox" data-k="skipJunk"' +
        (PARAMS.skipJunk ? ' checked' : '') + '> 跳过标记「丢弃」</label><br>' +
      '<label class="chk"><input type="checkbox" data-k="cap180"' +
        (PARAMS.cap180 ? ' checked' : '') + '> 只保留最优 24 套（搜索更快）</label>' +
    '</div>' +
    '<div style="margin-top:10px"><button class="btn pri" data-a="run" style="width:100%">' +
      '开始搜索</button></div>' +
    '<div class="mut" style="margin-top:8px">属性优先级：把想要的属性设成 1 级（最高），' +
    '搜索会先按 1 级属性总和排序，再看 2/3 级。<br>' +
    '「最小」是硬性下限，达不到的组合直接丢弃；「最大」用来限制某项属性别爆表（留空/0=不限）。</div>' +
    '</div>';
}
function exoticList(){
  const cls = PARAMS.cls, out = [], seen = {};
  const push = l => { for (const x of l || []) if (x.tt === 6 && (x.cls === cls || x.cls === 3) &&
    x.st && [3448274439,3551918588,14239492,20886954,1585787867].includes(x.db) &&
    !seen[x.h]){ seen[x.h] = 1; out.push(x); } };
  const c = ST.chars[cls];
  if (c){ push(c.bag); push(c.equipped); }
  push(ST.vault);
  return out.sort((a, b) => a.n.localeCompare(b.n));
}
function statBar(v, order){
  const t = Math.floor(v / 10);
  let b = '';
  for (let i = 0; i < 10; i++) b += '<i class="' + (i < t ? 'on' : '') + '"></i>';
  return '<span class="tierbar" title="' + t + ' 档">' + b + '</span>';
}
function setHTML(s, idx){
  const totals = {};
  for (const h of Object.keys(s.totals)) totals[h] = s.totals[h];
  const cls = ST.chars[OPT.cls] || {};
  const chosen = CMP.includes(idx) ? ' chosen' : '';
  return '<div class="setrow' + chosen + '" data-idx="' + idx + '">' +
    '<div class="top"><b>#' + (idx + 1) + '</b>' +
      STATS.map(x => '<span class="tot"><span class="mut">' + esc(x.n) + '</span>' +
        '<b class="' + (PARAMS.stats[x.h] && PARAMS.stats[x.h].min &&
        totals[x.h] >= PARAMS.stats[x.h].min ? 'hit' : '') + '">' + (totals[x.h] || 0) + '</b>' +
        statBar(totals[x.h] || 0) + '</span>').join('') +
      '<span class="tot"><b>' + s.sum + '</b><span class="mut">总属性</span></span>' +
      '<span class="sp" style="flex:1"></span>' +
      '<button class="btn sm pri" data-a="apply">装备</button>' +
      '<button class="btn sm" data-a="save">保存配装</button>' +
      '<button class="btn sm" data-a="cmp">' + (CMP.includes(idx) ? '取消对比' : '对比') + '</button>' +
    '</div>' +
    '<div class="sgrid" style="margin-top:5px">' + s.items.map(x =>
      '<div class="lslot">' + tile(x) + '<div class="sn">' +
      esc(ST.bucketNames[String(x.db)] || '') + '</div></div>').join('') + '</div>' +
    '</div>';
}
function tile(x){
  return '<div class="item" data-i="' + esc(x.tid) + '" data-h="' + x.h + '" title="' + esc(x.n) + '">' +
    '<div class="item-img r' + (x.tt || 0) + '">' +
      (x.wm ? '<span class="wm" style="background-image:url(' + x.wm + ')"></span>' : '') +
      '<img class="ic" src="' + x.ic + '" loading="lazy" alt="">' +
    '</div>' + (x.pw ? '<span class="vl">' + x.pw + '</span>' : '') + '</div>';
}
function cmpHTML(){
  if (CMP.length !== 2 || !OPT) return '';
  const a = OPT.sets[CMP[0]], b = OPT.sets[CMP[1]];
  if (!a || !b) return '';
  let rows = '';
  for (const s of STATS){
    const v1 = a.totals[s.h] || 0, v2 = b.totals[s.h] || 0, d = v2 - v1;
    rows += '<div class="tot"><b>' + esc(s.n) + '</b><span class="mut">#' + (CMP[0] + 1) + ' ' +
      v1 + '</span><span class="mut">#' + (CMP[1] + 1) + ' ' + v2 + '</span>' +
      '<b style="color:' + (d > 0 ? '#7dffa0' : d < 0 ? '#ff9a4d' : '#aaa') + '">' +
      (d > 0 ? '+' : '') + d + '</b></div>';
  }
  rows += '<div class="tot"><b>总属性</b><span class="mut">' + a.sum + '</span>' +
    '<span class="mut">' + b.sum + '</span><b style="color:' + (b.sum > a.sum ? '#7dffa0' : '#ff9a4d') +
    '">' + (b.sum - a.sum > 0 ? '+' : '') + (b.sum - a.sum) + '</b></div>';
  return '<div class="setrow"><div class="top"><b>对比</b>' +
    '<button class="btn sm" data-a="cmp-clear">结束对比</button></div>' + rows + '</div>';
}
function render(){
  if (!OPT){ $('#res').innerHTML = '<div class="mut">左侧设好条件，点「开始搜索」。</div>'; return; }
  let h = '<div class="bar2"><b>' + OPT.count + ' 套</b>' +
    '<span class="mut">扫描 ' + OPT.nodes.toLocaleString() + ' 个组合 · 耗时 ' + OPT.elapsed +
    ' 秒</span>' +
    (OPT.truncated ? '<span style="color:#ff9a4d">组合太多，搜索被截断（结果未必是最优）</span>' : '') +
    (OPT.note ? '<span style="color:#ff9a4d">' + esc(OPT.note) + '</span>' : '') +
    '</div>' + cmpHTML() + (OPT.sets || []).map(setHTML).join('');
  $('#res').innerHTML = h;
}
async function run(){
  const btn = $('[data-a=run]'); if (btn){ btn.disabled = true; btn.textContent = '搜索中…'; }
  try {
    OPT = await api('/api/dim/optimize', json(PARAMS));
    CMP = []; render();
    toast('搜到 ' + OPT.count + ' 套，耗时 ' + OPT.elapsed + ' 秒');
  } catch(e){ toast(e.message, true); }
  if (btn){ btn.disabled = false; btn.textContent = '开始搜索'; }
}
document.addEventListener('click', async e => {
  const c = e.target.closest('.chip[data-cls]');
  if (c){ PARAMS.cls = Number(c.dataset.cls); $('#side').innerHTML = panelHTML(); return; }
  const b = e.target.closest('[data-a]');
  if (b){
    const a = b.dataset.a;
    if (a === 'run'){ run(); return; }
    if (a === 'cmp-clear'){ CMP = []; render(); return; }
    const row = e.target.closest('[data-idx]');
    const idx = row ? Number(row.dataset.idx) : -1;
    const s = OPT && OPT.sets[idx];
    if (!s) return;
    if (a === 'cmp'){ const i = CMP.indexOf(idx);
      if (i >= 0) CMP.splice(i, 1); else { CMP.push(idx); if (CMP.length > 2) CMP.shift(); }
      render(); return; }
    if (a === 'apply'){
      const cls = OPT.cls;
      const ch = ST.chars[cls];
      if (!confirm('把这套装备装到' + ch.clsName + '身上？')) return;
      const r = await api('/api/dim/apply', json({char: ch.id,
        items: s.items.map(x => ({h: x.h, i: x.i}))}));
      toast('已装备 ' + r.equipped + ' 件' + (r.errors && r.errors.length ?
        '，' + r.errors.length + ' 件失败：' + r.errors[0] : ''), !!(r.errors && r.errors.length));
      window.reload(5000); return;
    }
    if (a === 'save'){
      const name = prompt('配装名称', '配装器 ' + (idx + 1));
      if (!name) return;
      const r = await api('/api/dim/my/save', json({name: name, cls: OPT.cls, notes: '',
        icon: '', color: TAGCOLS[1],
        params: PARAMS, items: s.items.map(x => ({h: x.h, i: x.i}))}));
      toast('已保存「' + r.loadout.name + '」，到「配装」页可以看到');
      return;
    }
  }
  const it = e.target.closest('.item');
  if (it && it.dataset.i) openPop(itemOf(it.dataset.i), it, true);
});
document.addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.prio){ (PARAMS.stats[t.dataset.prio] = PARAMS.stats[t.dataset.prio] || {})
    .prio = Number(t.value); return; }
  if (t.dataset.min){ (PARAMS.stats[t.dataset.min] = PARAMS.stats[t.dataset.min] || {})
    .min = Number(t.value) || 0; return; }
  if (t.dataset.max){ (PARAMS.stats[t.dataset.max] = PARAMS.stats[t.dataset.max] || {})
    .max = Number(t.value) || 0; return; }
  if (t.dataset.mw){ PARAMS.mw = t.value; return; }
  if (t.dataset.ex){ PARAMS.exoticHash = Number(t.value); return; }
  if (t.dataset.k){
    if (t.dataset.k === 'cap180'){ PARAMS.maxResults = t.checked ? 24 : 200; return; }
    PARAMS[t.dataset.k] = t.checked;
  }
});
(async () => {
  try {
    ST = await api('/api/dim/inventory'); TAGS_DB = ST.tags || {}; buildIndex();
    const r = await api('/api/dim/optimize/meta');
    STATS = r.stats;
    PARAMS = {cls: ST.chars.length - 1, stats: {}, mw: 'none', exoticHash: 0,
      exclEquipped: false, exclLocked: false, skipJunk: true, maxResults: 24};
    STATS.forEach(s => PARAMS.stats[s.h] = {prio: 0, min: 0, max: 0});
    $('#side').innerHTML = panelHTML();
    render();
  } catch(e){ $('#res').innerHTML = '<div class="mut">读取失败：' + esc(e.message) + '</div>'; }
})();
"""

OPT_CSS = ui.EXTRA_CSS + """
.sgrid{display:flex;gap:5px;flex-wrap:wrap}
.lslot .sn{font-size:10px;color:var(--text-2);text-align:center;width:var(--item-size);
  overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.tierbar{display:inline-flex;gap:1px;margin-left:3px;vertical-align:middle}
.tierbar i{width:3px;height:9px;background:rgba(255,255,255,.18);border-radius:1px}
.tierbar i.on{background:var(--accent-2)}
"""


@router.get("/dim/optimizer", response_class=HTMLResponse)
async def dim_optimizer():
    body = ('<div class="optcols"><div id="side"></div><div id="res"></div></div>')
    return HTMLResponse(ui.shell("/dim/optimizer", "配装器", body, OPT_JS, OPT_CSS))


# ============================ 管理器（表格式批量整理） ============================

MNG_JS = r"""
let ST = null, SEL = {}, SORT = 'name', DIR = 1, LOC = 'all';
function buildIndex(){
  ST.index = {};
  const add = l => { for (const x of l || []) ST.index[x.tid] = x; };
  for (const c of ST.chars){ add(c.equipped); add(c.bag); add(c.post); }
  add(ST.vault);
}
function rows(){
  const out = [];
  const push = (l, w, wn) => { for (const x of l || []) out.push(Object.assign({}, x, {w: w, wn: wn})); };
  for (const c of ST.chars){ push(c.bag, c.id, c.clsName); push(c.post, c.id, c.clsName + ' 邮政官'); }
  push(ST.vault, 'vault', '保险库');
  return out.filter(matchQ).filter(x => LOC === 'all' || x.w === LOC).sort((a, b) => {
    let r = 0;
    if (SORT === 'name') r = (a.n || '').localeCompare(b.n || '');
    else if (SORT === 'type') r = (a.ty || '').localeCompare(b.ty || '');
    else if (SORT === 'rarity') r = (a.tt || 0) - (b.tt || 0);
    else if (SORT === 'power') r = (a.pw || 0) - (b.pw || 0);
    else if (SORT === 'loc') r = (a.wn || '').localeCompare(b.wn || '');
    else if (SORT === 'tag') r = (tagOf(a) || '').localeCompare(tagOf(b) || '');
    return r * DIR;
  });
}
function th(k, label){
  return '<th data-sort="' + k + '">' + label + (SORT === k ? (DIR < 0 ? ' ▼' : ' ▲') : '') + '</th>';
}
function render(){
  const list = rows();
  let h = '<div class="bar2"><select id="loc">' +
    '<option value="all">全部位置</option>' + ST.chars.map(c =>
      '<option value="' + c.id + '"' + (LOC === c.id ? ' selected' : '') + '>' + esc(c.clsName) +
      '</option>').join('') +
    '<option value="vault"' + (LOC === 'vault' ? ' selected' : '') + '>保险库</option></select>' +
    '<span class="mut">' + list.length + ' 件 · 已选 ' + Object.keys(SEL).length + ' 件</span>' +
    '<span class="sp" style="flex:1"></span>' +
    '<button class="btn sm" data-a="tag-none">清标签</button>' +
    TAGS.map(t => '<button class="btn sm" data-a="tag" data-t="' + t.t + '">' +
      '<span style="color:' + t.c + '">' + t.sym + '</span> ' + t.n + '</button>').join('') +
    '<button class="btn sm" data-a="vault">移到仓库</button>' +
    ST.chars.map(c => '<button class="btn sm" data-a="mv" data-c="' + c.id + '">→' +
      esc(c.clsName) + '</button>').join('') +
    '<button class="btn sm" data-a="sel-none">取消选择</button></div>' +
    '<table class="mtab"><thead><tr><th></th>' + th('name', '名称') + th('type', '类型') +
    th('rarity', '稀有度') + th('power', '光等') + th('tag', '标签') + th('loc', '位置') +
    '</tr></thead><tbody>' + list.slice(0, 600).map(x =>
      '<tr class="' + (SEL[x.tid] ? 'on' : '') + '" data-i="' + esc(x.tid) + '">' +
      '<td><input type="checkbox" data-sel="' + esc(x.tid) + '"' + (SEL[x.tid] ? ' checked' : '') + '></td>' +
      '<td><span class="mi2"><img src="' + x.ic + '" class="r' + (x.tt || 0) + '">' + esc(x.n) +
        '</span></td><td class="mut">' + esc(x.ty || '') + '</td>' +
      '<td class="mut">' + esc(x.tn || '') + '</td><td>' + (x.pw || '') + '</td>' +
      '<td>' + (TAG_MAP[tagOf(x)] ? '<span style="color:' + TAG_MAP[tagOf(x)].c + '">' +
        TAG_MAP[tagOf(x)].sym + ' ' + TAG_MAP[tagOf(x)].n + '</span>' : '') +
        (noteOf(x) ? ' <span class="mut">🗒</span>' : '') + '</td>' +
      '<td class="mut">' + esc(x.wn || '') + '</td></tr>').join('') +
    '</tbody></table>' + (list.length > 600 ? '<div class="mut">只显示前 600 件，用搜索缩小范围</div>' : '');
  $('#mng').innerHTML = h;
}
window.filterNow = render;
window.rerender = render;
document.addEventListener('click', async e => {
  const s = e.target.closest('th[data-sort]');
  if (s){ const k = s.dataset.sort;
    if (SORT === k) DIR = -DIR; else { SORT = k; DIR = 1; } render(); return; }
  const b = e.target.closest('[data-a]'); if (!b) return;
  const ids = Object.keys(SEL).filter(i => SEL[i]);
  if (!ids.length){ toast('先勾选物品', true); return; }
  const a = b.dataset.a;
  const picked = ids.map(k => ST.index[k]).filter(Boolean);
  try {
    if (a === 'tag' || a === 'tag-none'){
      const tag = a === 'tag-none' ? '' : b.dataset.t;
      for (const x of picked){
        await api('/api/dim/tag', json({iid: x.i, h: x.h, tag: tag}));
        TAGS_DB[x.tid] = Object.assign({}, TAGS_DB[x.tid], {t: tag});
      }
      toast('已设置 ' + picked.length + ' 件的标签'); render(); return;
    }
    if (a === 'vault' || a === 'mv'){
      const to = a === 'vault' ? 'vault' : b.dataset.c;
      let ok = 0, err = 0, skip = 0;
      for (const x of picked){
        if (!x.i || x.w === to || (to === 'vault' && !x.vc)){ skip++; continue; }
        try { await api('/api/dim/move', json({iid: x.i, h: x.h, to: to, frm: x.w})); ok++; }
        catch(err2){ err++; }
      }
      toast('已移动 ' + ok + ' 件' + (skip ? '，跳过 ' + skip + ' 件' : '') +
        (err ? '，' + err + ' 件失败' : ''), !!err);
      SEL = {}; window.reload(5000); window.reload(20000); return;
    }
  } catch(err3){ toast(err3.message, true); }
});
document.addEventListener('change', e => {
  const c = e.target.closest('[data-sel]');
  if (c){ SEL[c.dataset.sel] = c.checked; render(); return; }
  if (e.target.id === 'loc'){ LOC = e.target.value; render(); }
});
async function load(){
  try {
    ST = await api('/api/dim/inventory'); TAGS_DB = ST.tags || {}; buildIndex(); render();
  } catch(e){ $('#mng').innerHTML = '<div class="mut">读取失败：' + esc(e.message) + '</div>'; }
}
window.reload = ms => setTimeout(load, ms);
load();
"""

MNG_CSS = """
.mtab{width:100%;border-collapse:collapse;font-size:13px}
.mtab th{text-align:left;padding:5px 7px;border-bottom:1px solid rgba(255,255,255,.18);
  cursor:pointer;color:var(--text-2);font-weight:600;font-size:12px;text-transform:uppercase}
.mtab td{padding:4px 7px;border-bottom:1px solid rgba(255,255,255,.06)}
.mtab tr:hover{background:rgba(255,255,255,.06)}
.mtab tr.on{background:rgba(232,165,52,.12)}
.mi2{display:flex;align-items:center;gap:7px}
.mi2 img{width:24px;height:24px;border-radius:3px;border:1px solid var(--polaroid)}
"""


@router.get("/dim/manage", response_class=HTMLResponse)
async def dim_manage():
    return HTMLResponse(ui.shell("/dim/manage", "管理器", '<div id="mng"></div>',
                                 MNG_JS, MNG_CSS))


# ============================ 接口 ============================


@router.get("/api/dim/inventory")
async def api_inventory(force: int = 0):
    try:
        return _merge_tags(await dm.inventory(force=bool(force)))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "读取背包失败")


@router.get("/api/dim/item/{instance_id}")
async def api_item(instance_id: str, h: str = "0"):
    try:
        return await dm.item_detail(instance_id, h)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "读取详情失败")


@router.post("/api/dim/move")
async def api_move(req: Request):
    b = await req.json()
    try:
        await dm.move(b.get("iid", ""), b.get("h"), b.get("to", ""), b.get("frm", ""))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "移动失败")


@router.post("/api/dim/equip")
async def api_equip(req: Request):
    b = await req.json()
    try:
        await dm.equip_any(b.get("iid", ""), b.get("h"), b.get("char", ""), b.get("frm", ""))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "装备失败")


@router.post("/api/dim/lock")
async def api_lock(req: Request):
    b = await req.json()
    try:
        await dm.lock(b.get("iid", ""), bool(b.get("on")))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "锁定失败")


@router.get("/api/dim/tags")
async def api_tags():
    return {"ok": True, "tags": du.tags()}


@router.post("/api/dim/tag")
async def api_tag(req: Request):
    b = await req.json()
    try:
        rec = du.set_tag(b.get("iid", ""), b.get("h"), b.get("tag"))
        return {"ok": True, "tag": rec}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "设置标签失败")


@router.post("/api/dim/note")
async def api_note(req: Request):
    b = await req.json()
    try:
        rec = du.set_tag(b.get("iid", ""), b.get("h"), None, b.get("note") or "")
        return {"ok": True, "tag": rec}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "保存备注失败")


@router.get("/api/dim/loadouts")
async def api_loadouts():
    """游戏内配装"""
    try:
        return await dm.loadouts()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "读取配装失败")


@router.get("/api/dim/my")
async def api_my():
    """自建配装（DIM 配装）。物品信息在服务端补齐：名称/图标/桶，没拥有的也能显示"""
    try:
        ls = du.loadouts()
        defs = dm.loadout_defs()
        out = []
        for lo in ls:
            items = []
            for it in lo.get("items") or []:
                d = dm.idef(it["h"])
                items.append({
                    "h": it["h"], "i": it.get("i") or "",
                    "n": d[0] if d else f"#{it['h']}",
                    "ic": dm._icon_url(d[5]) if d else "",
                    "wm": dm._icon_url(d[6]) if d else "",
                    "ty": d[2] if d else "", "tt": d[4] if d else 0,
                    "db": d[8] if d else 0,
                    "kind": "weapon" if d and 1 in d[12] else ("armor" if d and 20 in d[12] else ""),
                })
            out.append({k: v for k, v in lo.items() if k != "items"} | {"items": items})
        icons = [dm._icon_url(v) for v in (defs.get("i") or {}).values()]
        return {"ok": True, "loadouts": out, "icons": [u for u in icons if u]}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "读取自建配装失败")


@router.post("/api/dim/my/save")
async def api_my_save(req: Request):
    b = await req.json()
    try:
        return {"ok": True, "loadout": du.put_loadout(b)}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "保存配装失败")


@router.post("/api/dim/my/del")
async def api_my_del(req: Request):
    b = await req.json()
    return {"ok": du.del_loadout(b.get("id", ""))}


@router.post("/api/dim/my/dup")
async def api_my_dup(req: Request):
    b = await req.json()
    lo = du.dup_loadout(b.get("id", ""))
    return {"ok": True, "loadout": lo} if lo else _err("配装不存在")


@router.post("/api/dim/my/share")
async def api_my_share(req: Request):
    b = await req.json()
    lid = b.get("id")
    if not lid:
        return _err("先保存再分享")
    code = du.export_code(lid)
    return {"ok": True, "code": code} if code else _err("配装不存在")


@router.post("/api/dim/my/import")
async def api_my_import(req: Request):
    b = await req.json()
    try:
        return {"ok": True, "loadout": du.import_code(b.get("code", ""))}
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "导入失败")


@router.post("/api/dim/apply")
async def api_apply(req: Request):
    b = await req.json()
    try:
        return await dm.apply_items(b.get("char", ""), b.get("items") or [])
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "应用配装失败")


@router.get("/api/dim/optimize/meta")
async def api_opt_meta():
    return {"ok": True, "stats": [{"h": str(h), "n": n} for h, n in do.STATS]}


@router.post("/api/dim/optimize")
async def api_optimize(req: Request):
    b = await req.json()
    try:
        inv = await dm.inventory()
        tags = du.tags()
        return await asyncio.to_thread(do.search, inv, b, tags)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "搜索失败")


@router.get("/api/dim/triumphs")
async def api_triumphs():
    try:
        return await dm.triumphs()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc) or "读取成就失败")
