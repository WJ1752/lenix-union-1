"""本地图形化查询界面：http://127.0.0.1:8900"""
import asyncio
import json as _json
import os
import subprocess
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import uvicorn

import destiny_data as d2
import bot_cards
import bot_runtime
import bot_log
import napcat_runtime
import bungie_auth
import weapon_filter

app = FastAPI()


# ---------- 顶部导航：玩家 / 武器 / Perk 同级切换 ----------
# pywebview 原生窗口没有浏览器的后退键，进了 /catalog、/perks 就只能靠 history.back 回去。
# 各页面统一挂同一排导航，任意页面都能一步直达其他页面。
_NAV_ITEMS = (("/", "玩家查询"), ("/catalog", "武器图鉴"),
              ("/perks", "Perk查询"), ("/eververse", "光尘商店"), ("/rotation", "本周轮换"),
              ("/armorsets", "护甲套装"), ("/runtime", "运行状态"),
              ("/panel", "Bot面板"))


def navbar(active: str = "") -> str:
    links = "".join(
        f"<a class='nv{' on' if href == active else ''}' href='{href}'>{txt}</a>"
        for href, txt in _NAV_ITEMS)
    return (
        "<style>.d2nav{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin:0 0 16px}"
        ".d2nav .nv{color:#c5cacd;background:#16181b;border:1px solid #2a2e33;border-radius:8px;"
        "padding:9px 12px;font-size:14px;text-decoration:none;white-space:nowrap}"
        ".d2nav .nv:hover{border-color:#4b8fd4;color:#fff}"
        ".d2nav .nv.on{background:#35c66b;border-color:#35c66b;color:#fff;font-weight:bold}"
        "</style>"
        f"<div class='d2nav'>{links}</div>")


def err_page(title: str, detail: str) -> HTMLResponse:
    return HTMLResponse(
        f"<h1 style='font:600 20px sans-serif;color:#e8e6e3;margin:0 0 8px'>{title}</h1>"
        f"<div style='font:14px/1.7 sans-serif;color:#c5cacd'>{detail}</div>", status_code=200)


@app.exception_handler(Exception)
async def on_error(request, exc: Exception):
    """兜底：上游/网络异常不再吐 500 白页，给一句人话 + 重试提示"""
    return err_page("数据获取失败",
                    "Bungie 接口或网络暂时不可用，请点上方标签重试（已自动重试 2 次）。<br>"
                    f"<span style='color:#9aa0a6'>{d2.esc_err(exc)}</span>")


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- QQ Bot 面板 ----------
PANEL = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>Bot 面板</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;padding:22px}
h1{font-size:20px;text-align:center;margin:0 0 16px}
.wrap{display:flex;gap:18px;align-items:flex-start;justify-content:center;max-width:1180px;margin:0 auto}
.col-main{flex:1 1 620px;max-width:680px;min-width:0}
.col-side{flex:0 0 380px;position:sticky;top:22px}
@media(max-width:1020px){.wrap{flex-direction:column}.col-side{position:static;flex:1 1 auto;width:100%}}
.card{background:#16181b;border:1px solid #2a2e33;border-radius:10px;padding:14px 18px;
      margin:0 0 14px;font-size:14px;line-height:1.8;box-sizing:border-box}
.on{color:#35c66b}.off{color:#ff8d85}
.grow{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;
      border-radius:6px;margin:3px 0;background:#16181b}
.grow:hover{background:#1b1e22}
button{padding:6px 14px;border-radius:6px;border:1px solid #2a2e33;background:#35c66b;
       color:#e8e6e3;cursor:pointer;font-weight:bold}
button.ghost{background:#2a2e33;font-weight:normal}
button:disabled{opacity:.4;cursor:default}
code{background:#1b1e22;border-radius:4px;padding:1px 6px;font-size:12px;color:#4b8fd4}
.dim{color:#9aa0a6;font-size:12px}
.pbar{height:8px;background:#1b1e22;border-radius:4px;overflow:hidden;margin-top:6px}
.pfill{height:100%;background:linear-gradient(90deg,#35c66b,#4b8fd4);transition:width .4s}
.pfill.err{background:#b04a42}
.jobhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.jobbox{max-height:calc(100vh - 300px);min-height:120px;overflow:auto;padding-right:4px}
.jobbox::-webkit-scrollbar{width:8px}
.jobbox::-webkit-scrollbar-thumb{background:#2a2e33;border-radius:4px}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:8px}
.pager button{padding:4px 10px;font-size:12px}
</style></head><body>
<h1>QQ Bot 面板</h1>
<div class="wrap">
<div class="col-main">
  <div class="card" id="status">正在获取连接状态…</div>
  <div class="card">
    <b>QQ 登录（内置 NapCat）</b>
    <div id="napcat" style="margin-top:8px">正在检查 NapCat…</div>
    <div id="qr" style="margin-top:8px"></div>
    <details class="dim" style="margin-top:6px"><summary>已有外部协议端？手动接入方式</summary>
      1. 启动 NapCat / LLOneBot 并登录 QQ；<br>
      2. 添加<b>反向 WebSocket</b>：<code>ws://127.0.0.1:8901/onebot/v11/ws</code><br>
      3. 连接成功后，上方自动出现 QQ 账号，下方出现群列表。
    </details>
  </div>
  <div class="card">
    <b>Bungie 账号授权</b> <span class="dim">（读光尘商店等需要登录的接口）</span>
    <div id="bungie" style="margin-top:8px">正在检查…</div>
  </div>
  <div class="card">
    <b>消息日志</b> <span class="dim">（群里的指令与私聊消息 + 机器人回复，最新在前，最多 500 条）</span>
    <div style="margin:8px 0;display:flex;gap:8px;align-items:center">
      <select id="logGroup" onchange="refreshLogs()" style="padding:5px 8px;border-radius:6px;
        border:1px solid #2a2e33;background:#1b1e22;color:#e8e6e3"></select>
      <button class="ghost" onclick="clearLogs()">清空日志</button>
      <span class="dim" id="logCount"></span>
    </div>
    <div id="logs" style="max-height:360px;overflow:auto">正在加载…</div>
  </div>
  <div class="card">
    <b>生效群聊</b> <span class="dim">（不勾选任何群 = 所有群都响应）</span>
    <div id="groups" style="margin-top:8px">等待 bot 连接…</div>
    <div style="margin-top:8px;text-align:right"><button onclick="save()">保存群开关</button></div>
  </div>
  <div class="card dim">指令（<b>必须带 <code>/</code> 前缀</b>）：/绑定 玩家名#编号 ｜ /生涯 ｜ /玩家 ｜ /武器查询 武器名 ｜ /perk查询 perk名 ｜ /pvp生涯武器 ｜ /pve生涯武器（绑定账号后玩家类指令可省名字）。<b>群里直接 @机器人 接武器名 / perk名</b> 也会自动出对应卡片。</div>
</div>
<div class="col-side">
  <div class="card">
    <div class="jobhead">
      <b>后台任务日志</b>
      <span class="dim" id="jobCount"></span>
    </div>
    <div class="dim" style="margin-bottom:8px">生涯武器 / 热力图：谁发起的、查的谁、跑到第几场（按发起时间，最新在前）</div>
    <div id="jobs" class="jobbox">暂无后台任务</div>
    <div class="pager">
      <button class="ghost" id="jobPrev" onclick="jobGo(-1)">上一页</button>
      <span class="dim" id="jobPageInfo"></span>
      <button class="ghost" id="jobNext" onclick="jobGo(1)">下一页</button>
    </div>
  </div>
  <div class="card">
    <div class="jobhead">
      <b>账号绑定</b>
      <span class="dim" id="bindCount"></span>
    </div>
    <div class="dim" style="margin-bottom:8px">QQ → 已绑定的命运2账号（编号统一补零到 4 位）</div>
    <div id="binds" style="max-height:280px;overflow:auto">加载中…</div>
  </div>
</div>
</div>
<script>
let groupNames = {};
let jobPage = 0;
const JOB_PAGE = 6;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fmtElapsed(sec){
  if(!sec && sec !== 0) return '';
  const m = Math.floor(sec/60), s = sec%60;
  return m ? `${m}分${s}秒` : `${s}秒`;
}
async function refreshLogs(){
  const sel = document.getElementById('logGroup');
  const cur = sel.value;
  const r = await (await fetch('/api/bot/logs?limit=200&group_id=' + encodeURIComponent(cur))).json();
  const opts = r.groups.map(g =>
    `<option value="${g}" ${g===cur?'selected':''}>${esc(groupNames[g]||('群 '+g))}</option>`).join('');
  sel.innerHTML = `<option value="" ${cur===''?'selected':''}>全部</option>${opts}` +
    (r.has_private ? `<option value="private" ${cur==='private'?'selected':''}>私聊</option>` : '');
  const box = document.getElementById('logs');
  document.getElementById('logCount').textContent = `共 ${r.items.length} 条`;
  if(!r.items.length){ box.innerHTML = '<span class="dim">暂无记录</span>'; return; }
  box.innerHTML = r.items.map(e=>{
    const gname = e.group_id ? (groupNames[e.group_id] || ('群 ' + e.group_id)) : '私聊';
    const tag = e.dir === 'out'
      ? '<span class="on">回</span>'
      : (e.enabled === false ? '<span class="off">未启用群</span>'
         : (e.skip ? `<span class="off">${esc(e.skip)}</span>` : '<span class="dim">收</span>'));
    return `<div style="padding:6px 10px;border-radius:6px;margin:3px 0;background:#16181b">
      <div class="dim">${e.time} · ${esc(gname)} · ${esc(e.dir==='out'?'机器人':(e.nickname||e.user_id))} ${tag}</div>
      <div style="white-space:pre-wrap;word-break:break-all;margin-top:2px">${esc(e.text)}</div></div>`;
  }).join('');
}
async function clearLogs(){
  if(!confirm('清空当前日志？')) return;
  await fetch('/api/bot/logs/clear', {method:'POST'});
  refreshLogs();
}
let qrBusy = false;  // 刷新二维码期间暂停 5 秒轮询覆盖，避免"正在获取"被冲掉
function qrHtml(q){
  return q
    ? `<img src="${q}" width="200" style="border-radius:8px"><div class="dim">用手机 QQ 扫这个码登录（提示过期就点刷新）</div><button onclick="refreshQR()">刷新二维码</button>`
    : '<span class="dim">二维码加载中…</span>';
}
async function refreshNap(){
  const s = await (await fetch('/api/napcat/status')).json();
  const box = document.getElementById('napcat');
  const qr = document.getElementById('qr');
  if(!s.running){
    box.innerHTML = '<span class="off">● NapCat 未启动</span>　<button onclick="startNap()">启动并扫码登录</button>';
    qr.innerHTML = ''; return;
  }
  if(s.isLogin || s.uin || s.qq){
    box.innerHTML = `<span class="on">● NapCat 已登录</span>　QQ：${s.uin || s.qq}　${s.connected===false?'等待接入 Bot…':''}
      <button onclick="resetNap()" style="margin-left:12px">退出并重置（需重新扫码）</button>`;
    qr.innerHTML = ''; return;
  }
  box.innerHTML = '<span class="off">● NapCat 运行中</span>　等待扫码登录…';
  if(qrBusy) return;
  const q = await (await fetch('/api/napcat/qr')).json();
  qr.innerHTML = qrHtml(q.qr);
}
async function refreshQR(){
  if(qrBusy) return;
  qrBusy = true;
  const qr = document.getElementById('qr');
  qr.innerHTML = '<span class="dim">正在获取新二维码…（登录服务重启时最长等 20 秒）</span>';
  try{
    const q = await (await fetch('/api/napcat/qr/refresh', {method:'POST'})).json();
    if(q.qr){ qr.innerHTML = qrHtml(q.qr); }
    else { qr.innerHTML = '<span class="dim">没能取到新二维码，请稍后重试</span><button onclick="refreshQR()">重试</button>'; }
  }catch(e){
    qr.innerHTML = '<span class="dim">刷新失败，请稍后重试</span><button onclick="refreshQR()">重试</button>';
  }finally{ qrBusy = false; }
}
async function startNap(){
  const j = await (await fetch('/api/napcat/start', {method:'POST'})).json();
  if(!j.started && j.error) alert(j.error);
  refreshNap();
}
async function resetNap(){
  if(!confirm('退出当前 QQ 登录并重置？之后需要重新扫码登录（短时间反复重登可能触发 QQ 风控）。')) return;
  await fetch('/api/napcat/reset', {method:'POST'});
  refreshNap();
}
async function refresh(){
  refreshNap();
  refreshLogs();
  const s = await (await fetch('/api/bot/status')).json();
  document.getElementById('status').innerHTML = s.connected
    ? `<span class="on">● 已连接</span>　QQ：${s.nickname}（${s.uin}）`
    : `<span class="off">● 未连接</span>　等待协议端接入（按上方步骤配置 NapCat）`;
  const g = await (await fetch('/api/bot/groups')).json();
  groupNames = Object.fromEntries((g.groups||[]).map(x=>[String(x.group_id), x.group_name]));
  const box = document.getElementById('groups');
  if(!g.connected){ box.textContent = '未连接协议端，无法获取群列表'; return; }
  if(!g.groups.length){ box.textContent = '该 QQ 号还没加入任何群'; return; }
  box.innerHTML = g.groups.map(x =>
    `<div class="grow"><label><input type="checkbox" value="${x.group_id}"
      ${g.enabled.includes(String(x.group_id))?'checked':''}> ${x.group_name}（${x.group_id}）</label></div>`).join('');
}
async function save(){
  const en = [...document.querySelectorAll('#groups input:checked')].map(i=>i.value);
  const r = await fetch('/api/bot/groups', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: en})});
  alert(r.ok ? '已保存' : '保存失败');
}
function jobGo(d){ jobPage += d; refreshJobs(); }
async function refreshJobs(){
  const box = document.getElementById('jobs');
  let all = [];
  try{ all = (await (await fetch('/api/bot/jobs')).json()).jobs || []; }catch(e){ return; }
  document.getElementById('jobCount').textContent = all.length ? `共 ${all.length} 条` : '';
  if(!all.length){
    box.innerHTML = '<span class="dim">暂无后台任务</span>';
    document.getElementById('jobPageInfo').textContent = '';
    document.getElementById('jobPrev').disabled = true;
    document.getElementById('jobNext').disabled = true;
    return;
  }
  const pages = Math.max(1, Math.ceil(all.length / JOB_PAGE));
  if(jobPage >= pages) jobPage = pages - 1;
  if(jobPage < 0) jobPage = 0;
  const slice = all.slice(jobPage * JOB_PAGE, jobPage * JOB_PAGE + JOB_PAGE);
  box.innerHTML = slice.map(j=>{
    const running = j.status === 'running';
    const queued = j.status === 'queued';
    const pct = running && !j.total ? 0 : j.pct;
    const tail = running
      ? (j.total ? `${j.done} / ${j.total} 场（${j.pct}%）` : '准备中…')
      : queued ? `排队中 · 第 ${j.queue_pos || 1} 位`
      : (j.status === 'done' ? '已完成' : j.status === 'error' ? '失败' : j.status);
    const width = running ? (pct || 3) : queued ? 3 : 100;
    const when = (j.date ? j.date + ' ' : '') + (j.time || '');
    const el = (running || queued) && j.elapsed ? ` · 已跑 ${fmtElapsed(j.elapsed)}` : '';
    // 复用的任务不会多出一条记录，这里标一下，免得看着像「指令没反应」
    const reuse = j.reused
      ? `<span class="dim"> · 已复用于 ${esc((j.reused_by || []).join('、') || '同一查询')}</span>` : '';
    return `<div style="padding:8px 10px;border-radius:6px;margin:4px 0;background:#16181b">
      <div style="display:flex;justify-content:space-between;gap:10px">
        <span><b>${esc(j.label)}</b> <span class="dim">${esc(j.name)}</span></span>
        <span class="dim" style="white-space:nowrap">${esc(j.who)}${reuse}</span>
      </div>
      <div class="pbar"><div class="pfill ${j.status==='error'?'err':''}" style="width:${width}%"></div></div>
      <div class="dim" style="margin-top:3px;display:flex;justify-content:space-between;gap:8px">
        <span>${esc(tail)}</span>
        <span style="white-space:nowrap">🕒 ${esc(when)}${esc(el)}</span>
      </div></div>`;
  }).join('');
  document.getElementById('jobPageInfo').textContent = `第 ${jobPage + 1} / ${pages} 页`;
  document.getElementById('jobPrev').disabled = jobPage <= 0;
  document.getElementById('jobNext').disabled = jobPage >= pages - 1;
}
async function refreshBindings(){
  const box = document.getElementById('binds');
  let items = [];
  try{ items = (await (await fetch('/api/bot/bindings')).json()).items || []; }catch(e){ return; }
  document.getElementById('bindCount').textContent = items.length ? `${items.length} 个` : '';
  if(!items.length){ box.innerHTML = '<span class="dim">还没有人绑定账号</span>'; return; }
  box.innerHTML = items.map(b =>
    `<div class="grow"><span class="dim">QQ ${esc(b.qq)}</span><b>${esc(b.name)}</b></div>`).join('');
}
async function refreshBungie(){
  const box = document.getElementById('bungie');
  let s = {};
  try{ s = await (await fetch('/api/bungie/status')).json(); }catch(e){ return; }
  if(!s.configured){
    box.innerHTML = '<span class="off">● 未配置 Bungie 应用</span>'
      + '<div class="dim" style="margin-top:6px">在 .env 里补 <code>BUNGIE_CLIENT_ID</code> / '
      + '<code>BUNGIE_CLIENT_SECRET</code>（Bungie 应用里「开放授权客户端类型」要选<b>机密</b>），'
      + 'Redirect URL 填 <code>https://127.0.0.1:8902/bungie/callback</code>，重启后再授权。</div>';
    return;
  }
  if(s.authorized){
    const exp = s.expires_at ? new Date(s.expires_at*1000).toLocaleString() : '';
    const expired = s.expires_at && (s.expires_at*1000 < Date.now());
    box.innerHTML = `<span class="on">● 已授权</span>　<b>${esc(s.display_name||s.membership_id||'')}</b>`
      + (exp ? `<div class="dim" style="margin-top:4px">token 到期：${esc(exp)}`
             + (expired ? '　<span class="off">已过期，点「刷新 Token」续期</span>' : '') + '</div>' : '')
      + '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">'
      + '<button onclick="bungieRefresh()">刷新 Token</button>'
      + '<a href="/bungie/authorize" target="_blank"><button class="ghost">重新授权</button></a>'
      + '<button class="ghost" onclick="bungieLogout()">取消授权</button></div>'
      + '<div class="dim" id="bmsg2" style="margin-top:6px"></div>';
    return;
  }
  box.innerHTML = '<span class="off">● 未授权</span>'
    + '<div class="dim" style="margin-top:6px">① 点下面按钮去 Bungie 登录并同意；'
    + '② 跳回来时浏览器若提示「不安全」没关系，把<b>地址栏那一整条地址</b>复制下来，'
    + '粘到下面框里点完成。</div>'
    + '<div style="margin-top:8px"><a href="/bungie/authorize" target="_blank">'
    + '<button>打开 Bungie 授权页</button></a></div>'
    + '<div style="margin-top:8px;display:flex;gap:6px">'
    + '<input id="bcode" placeholder="粘贴回调地址或 code" style="flex:1;padding:6px 8px;border-radius:6px;'
    + 'border:1px solid #2a2e33;background:#1b1e22;color:#e8e6e3;font-size:12px">'
    + '<button onclick="bungieManual()">完成授权</button></div>'
    + '<div class="dim" id="bmsg" style="margin-top:6px"></div>'
    + '<div class="dim" style="margin-top:4px;word-break:break-all">程序使用的回调地址：<code>'
    + esc(s.redirect_uri || '') + '</code>（Bungie 应用里登记的 Redirect URL 必须与它完全一致，'
    + '换 token 时会先试你粘的地址、再试这条）</div>'
    + '<div class="dim" style="margin-top:4px;word-break:break-all">授权页打不开就来这里：'
    + '<code>http://127.0.0.1:8900/bungie/authorize</code></div>';
}
async function bungieManual(){
  const el = document.getElementById('bcode');
  const msg = document.getElementById('bmsg');
  const text = (el && el.value || '').trim();
  if(!text){ if(msg) msg.textContent = '先粘贴地址或 code'; return; }
  if(msg) msg.textContent = '正在换取 token…';
  try{
    const r = await (await fetch('/api/bungie/manual', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})})).json();
    if(r.ok){ refreshBungie(); }
    else if(msg){ msg.innerHTML = '<span class="off">' + esc(r.error||'授权失败') + '</span>'; }
  }catch(e){ if(msg) msg.textContent = '请求失败，稍后重试'; }
}
async function bungieRefresh(){
  const msg = document.getElementById('bmsg2');
  if(msg) msg.textContent = '正在用 refresh_token 续期…';
  try{
    const r = await (await fetch('/api/bungie/refresh', {method:'POST'})).json();
    if(r.ok){ if(msg) msg.textContent = '已续期'; refreshBungie(); }
    else if(msg){ msg.innerHTML = '<span class="off">' + esc(r.error||'刷新失败') + '</span>　'
      + '<a href="/bungie/authorize" target="_blank">重新授权</a>'; }
  }catch(e){ if(msg) msg.textContent = '请求失败，稍后重试'; }
}
async function bungieLogout(){
  if(!confirm('取消 Bungie 授权？')) return;
  await fetch('/api/bungie/logout', {method:'POST'});
  refreshBungie();
}
refresh(); setInterval(refresh, 5000); setInterval(refreshJobs, 2000); refreshJobs();
refreshBindings(); setInterval(refreshBindings, 10000);
refreshBungie(); setInterval(refreshBungie, 10000);
</script></body></html>"""


@app.get("/panel", response_class=HTMLResponse)
async def panel():
    # 顶部挂同一排导航：面板本身就是程序内一个页面，可以一步回查询站（此前没有返回按钮）
    return HTMLResponse(PANEL.replace("<h1>QQ Bot 面板</h1>",
                                      navbar("/panel") + "<h1>QQ Bot 面板</h1>", 1))


@app.get("/api/napcat/status")
async def napcat_status():
    # status() 里是同步 httpx（NapCat 不在线时单次可卡十几秒），丢线程池别阻塞事件循环
    st = await asyncio.to_thread(napcat_runtime.status)
    if st.get("isLogin") and not st.get("uin"):
        bots = bot_runtime.get_bots()
        for bot in bots.values():
            try:
                info = await bot.get_login_info()
                st["uin"] = str(info["user_id"])
                st["qq"] = st["uin"]
                break
            except Exception:  # noqa: BLE001
                continue
    return st


@app.post("/api/napcat/start")
def napcat_start():
    # 同步函数（FastAPI 丢线程池跑）：start() 里有 sleep / netstat / tasklist，很慢
    return napcat_runtime.start()


@app.post("/api/napcat/reset")
def napcat_reset():
    # 退出登录并重置：杀掉 NapCat/QQ，回到未启动态，需要重新扫码（同上，必须丢线程池）
    return napcat_runtime.reset()


@app.get("/api/napcat/qr")
def napcat_qr():
    # 同步函数：qr_data_url 在码过期时会顺带续期（内部可能 sleep），不能阻塞事件循环
    return {"qr": napcat_runtime.qr_data_url()}


@app.post("/api/napcat/qr/refresh")
def napcat_qr_refresh():
    """让 NapCat 重新生成登录二维码（面板上的"刷新二维码"按钮）。

    同步函数（FastAPI 会丢到线程池跑）：refresh_qr 内部可能要轮询等新码（最长 ~20s），
    写成 async 会阻塞事件循环、把面板其它请求一起卡死。
    """
    qr = napcat_runtime.refresh_qr(force=True)
    return {"qr": qr or napcat_runtime.qr_data_url()}


@app.get("/api/bot/logs")
async def bot_logs(limit: int = 200, group_id: str = ""):
    items = bot_log.recent(limit=limit, group_id=group_id)
    has_private = bool(bot_log.recent(limit=500, group_id="private"))
    return {"items": items, "groups": bot_log.groups(), "has_private": has_private}


@app.get("/api/bot/jobs")
async def bot_jobs():
    """后台任务进度（面板进度条）：谁发起的 / 查的谁 / 跑到哪了 / 什么时候发的"""
    return {"jobs": d2.job_snapshot()}


@app.get("/api/bot/bindings")
async def bot_bindings():
    """面板用：QQ → 绑定账号 一览（编号统一补零到 4 位）"""
    return {"items": d2.bindings_snapshot()}


@app.post("/api/bot/logs/clear")
async def bot_logs_clear():
    bot_log.clear()
    return {"ok": True}


_ob11_recheck_at = 0.0  # 上次"未连接时补发反向 WS 配置"的时间（限频，WebUI API 有频率限制）


@app.get("/api/bot/status")
async def bot_status():
    global _ob11_recheck_at
    bots = bot_runtime.get_bots()
    if not bots:
        # 已登录但协议端没接入：多半是 NapCat 登录时反向 WS 配置没生效
        # （watcher 不在/下发失败）。面板轮询在这里低频补发，热更即生效。
        st = await asyncio.to_thread(napcat_runtime.status)
        if st.get("isLogin") and time.time() - _ob11_recheck_at > 60:
            _ob11_recheck_at = time.time()
            await asyncio.get_event_loop().run_in_executor(
                None, napcat_runtime.ensure_ob11_via_api)
        return {"connected": False}
    for bot in bots.values():
        try:
            info = await bot.get_login_info()
            return {"connected": True, "uin": info["user_id"], "nickname": info["nickname"]}
        except Exception:  # noqa: BLE001
            continue
    return {"connected": False}


@app.get("/api/bot/groups")
async def bot_groups():
    bots = bot_runtime.get_bots()
    groups = []
    for bot in bots.values():
        try:
            groups = await bot.get_group_list()
            break
        except Exception:  # noqa: BLE001
            continue
    return {"connected": bool(bots), "groups": groups,
            "enabled": bot_runtime.enabled_groups()}


@app.post("/api/bot/groups")
async def bot_groups_save(request: dict):
    bot_runtime.set_enabled_groups(request.get("enabled") or [])
    return {"ok": True}


# ---------- 运行状态页：进程资源占用 + 并发上限设置 ----------
# psutil 打进 exe 后才有进程级数据；没装时页面降级成提示 + 并发设置仍可用。
try:
    import psutil as _psutil
except Exception:  # noqa: BLE001
    _psutil = None

_PROC = _psutil.Process(os.getpid()) if _psutil else None
_NET_LAST = {"t": 0.0, "sent": 0, "recv": 0}


@app.get("/api/runtime/stats")
def runtime_stats():
    """同步函数（丢线程池跑）：psutil 采样有阻塞调用，别卡事件循环。"""
    out = {"ok": False, "concurrency": bot_runtime.load_config().get("max_concurrency") or 0}
    if not _psutil or _PROC is None:
        out["error"] = "psutil 未随 exe 打包，请重新打包后使用"
        return out
    mem = _PROC.memory_info()
    create_time = _PROC.create_time()
    scpu = _psutil.cpu_percent(None)
    svm = _psutil.virtual_memory()
    net = _psutil.net_io_counters()
    now = time.time()
    up = down = 0.0
    if _NET_LAST["t"]:
        dt = now - _NET_LAST["t"]
        if dt > 0:
            up = max(0, net.bytes_sent - _NET_LAST["sent"]) / dt
            down = max(0, net.bytes_recv - _NET_LAST["recv"]) / dt
    _NET_LAST.update(t=now, sent=net.bytes_sent, recv=net.bytes_recv)
    out.update(
        ok=True,
        proc={"cpu": _PROC.cpu_percent(None), "rss": mem.rss, "threads": _PROC.num_threads(),
              "uptime": max(0, now - create_time),
              "read_bytes": _PROC.io_counters().read_bytes, "write_bytes": _PROC.io_counters().write_bytes},
        sys={"cpu": scpu, "mem_total": svm.total, "mem_used": svm.total - svm.available},
        net={"up": up, "down": down, "sent_total": net.bytes_sent, "recv_total": net.bytes_recv})
    return out


@app.get("/api/settings/concurrency")
async def get_concurrency():
    return {"value": bot_runtime.load_config().get("max_concurrency") or 0}


@app.post("/api/settings/concurrency")
async def set_concurrency(request: dict):
    try:
        n = int(request.get("value") or 0)
    except Exception:  # noqa: BLE001
        n = 0
    bot_runtime.set_concurrency(n)
    return {"ok": True, "value": bot_runtime.load_config().get("max_concurrency") or 0}


RUNTIME_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>运行状态</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;padding:22px}
h1{font-size:20px;margin:0 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:18px}
.tile{background:#16181b;border:1px solid #2a2e33;border-radius:10px;padding:12px 14px}
.tile .k{font-size:12px;color:#9aa0a6;margin-bottom:6px}
.tile .v{font-size:22px;font-weight:600;line-height:1.2}
.tile .s{font-size:12px;color:#9aa0a6;margin-top:4px}
.bar{height:5px;background:#24282d;border-radius:3px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;background:#35c66b;border-radius:3px;transition:width .4s}
.sec{background:#16181b;border:1px solid #2a2e33;border-radius:10px;padding:14px 16px;max-width:560px}
.sec h2{font-size:15px;margin:0 0 8px}
.sec p{font-size:12px;color:#9aa0a6;line-height:1.7;margin:0 0 10px}
select,button{font-family:inherit;font-size:14px;background:#0f1113;color:#e8e6e3;
border:1px solid #2a2e33;border-radius:8px;padding:8px 12px}
button{background:#35c66b;border-color:#35c66b;color:#fff;font-weight:bold;cursor:pointer}
#msg{font-size:13px;color:#35c66b;margin-left:10px}
</style></head><body>
<h1>运行状态</h1>
<div class="grid">
 <div class="tile"><div class="k">进程 CPU</div><div class="v" id="pcpu">–</div>
  <div class="bar"><i id="pcpu_b"></i></div></div>
 <div class="tile"><div class="k">进程内存</div><div class="v" id="pmem">–</div><div class="s" id="pmem_s"></div>
  <div class="bar"><i id="pmem_b"></i></div></div>
 <div class="tile"><div class="k">进程线程数</div><div class="v" id="pth">–</div><div class="s" id="puptime"></div></div>
 <div class="tile"><div class="k">系统 CPU</div><div class="v" id="scpu">–</div>
  <div class="bar"><i id="scpu_b"></i></div></div>
 <div class="tile"><div class="k">系统内存</div><div class="v" id="smem">–</div><div class="s" id="smem_s"></div>
  <div class="bar"><i id="smem_b"></i></div></div>
 <div class="tile"><div class="k">网络 ↑ 发送</div><div class="v" id="nup">–</div><div class="s" id="nup_s"></div></div>
 <div class="tile"><div class="k">网络 ↓ 接收</div><div class="v" id="ndown">–</div><div class="s" id="ndown_s"></div></div>
</div>
<div class="sec">
 <h2>并发上限</h2>
 <p>限定查询任务同时发起的请求数（PvP/PvE 逐场对局拉取、卡片图标下载共用）。
 调小可降低对电脑 CPU/带宽的占用，代价是大数据量统计耗时变长；保存即生效，无需重启。
 「默认」= 程序内置值（对局 16 路 / 图标 8 路）。</p>
 <select id="conc">
  <option value="0">默认（对局 16 / 图标 8）</option>
  <option value="2">2（最省资源）</option><option value="4">4</option><option value="6">6</option>
  <option value="8">8</option><option value="12">12</option><option value="16">16</option>
  <option value="24">24</option><option value="32">32</option>
 </select>
 <button onclick="save()">保存</button><span id="msg"></span>
</div>
<script>
function fmtB(n){if(!isFinite(n))return'–';if(n<1024)return n.toFixed(0)+' B';
 const u=['KB','MB','GB','TB'];let i=-1;do{n/=1024;i++}while(n>=1024&&i<3);return n.toFixed(1)+' '+u[i]}
function pct(a,b){return b>0?Math.min(100,a/b*100):0}
function bar(id,p){const e=document.getElementById(id);e.style.width=p+'%';
 e.style.background=p>80?'#e05252':p>50?'#e0b452':'#35c66b'}
async function refresh(){
 try{
  const d=await (await fetch('/api/runtime/stats')).json();
  if(!d.ok){document.getElementById('pcpu').textContent='不可用';
   document.getElementById('pcpu').style.fontSize='14px';return}
  document.getElementById('pcpu').textContent=d.proc.cpu.toFixed(1)+'%';bar('pcpu_b',d.proc.cpu);
  document.getElementById('pmem').textContent=fmtB(d.proc.rss);
  document.getElementById('pmem_s').textContent='占系统内存 '+pct(d.proc.rss,d.sys.mem_total).toFixed(1)+'%';
  bar('pmem_b',pct(d.proc.rss,d.sys.mem_total));
  document.getElementById('pth').textContent=d.proc.threads;
  const h=Math.floor(d.proc.uptime/3600),m=Math.floor(d.proc.uptime%3600/60);
  document.getElementById('puptime').textContent='已运行 '+(h?h+' 小时 ':'')+m+' 分钟';
  document.getElementById('scpu').textContent=d.sys.cpu.toFixed(1)+'%';bar('scpu_b',d.sys.cpu);
  document.getElementById('smem').textContent=fmtB(d.sys.mem_used);
  document.getElementById('smem_s').textContent='共 '+fmtB(d.sys.mem_total);
  bar('smem_b',pct(d.sys.mem_used,d.sys.mem_total));
  document.getElementById('nup').textContent=fmtB(d.net.up)+'/s';
  document.getElementById('nup_s').textContent='累计 '+fmtB(d.net.sent_total);
  document.getElementById('ndown').textContent=fmtB(d.net.down)+'/s';
  document.getElementById('ndown_s').textContent='累计 '+fmtB(d.net.recv_total);
  document.getElementById('conc').value=String(d.concurrency||0);
 }catch(e){}}
async function save(){
 const v=parseInt(document.getElementById('conc').value);
 await fetch('/api/settings/concurrency',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({value:v})});
 const m=document.getElementById('msg');m.textContent='已保存，即刻生效';
 setTimeout(()=>m.textContent='',2500)}
refresh();setInterval(refresh,2000);
</script></body></html>"""


@app.get("/runtime", response_class=HTMLResponse)
async def runtime_page():
    return HTMLResponse(RUNTIME_PAGE.replace(
        "<h1>运行状态</h1>", navbar("/runtime") + "<h1>运行状态</h1>", 1))


# ---------- 首页（输入一次 ID，标签页切换视图） ----------
INDEX = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>D2 查询</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;
     display:flex;flex-direction:column;align-items:center;padding:24px;color:#c5cacd}
.bar{display:flex;gap:10px;margin-bottom:12px}
input,button{padding:10px 14px;border-radius:8px;border:1px solid #2a2e33;
     background:#16181b;color:#e8e6e3;font-size:15px;outline:none}
button{background:#35c66b;border:none;cursor:pointer;font-weight:bold}
button:hover{background:#4b8fd4}
#tabs{display:none;flex-wrap:wrap;gap:8px;margin-bottom:14px;justify-content:center}
#tabs button{background:#16181b;font-weight:normal;font-size:14px}
#tabs button.on{background:#35c66b;font-weight:bold}
#scopebar{display:none;gap:10px;margin-bottom:14px;align-items:center;
     color:#9aa0a6;font-size:13px;flex-wrap:wrap;justify-content:center}
#scopebar select{padding:8px 12px;border-radius:8px;border:1px solid #2a2e33;
     background:#16181b;color:#e8e6e3;font-size:13px;outline:none;max-width:360px}
.tlabel{color:#9aa0a6;font-size:13px}
.sugrow{display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;color:#e8e6e3}
.sugrow:hover{background:#1b1e22}
.sugrow img{width:28px;height:28px;border-radius:4px}
.sugcode{color:#4b8fd4}
iframe{width:760px;height:900px;border:none;border-radius:12px;display:none}
#tip{color:#6d737b;margin-top:10px}
</style></head><body>
__NAV__
<div class="bar" style="position:relative">
  <input id="name" placeholder="玩家名#编号，如 Wj#8984" size="44" autocomplete="off">
  <button onclick="go()">查询</button>
  <div id="sug" style="display:none;position:absolute;top:44px;left:0;width:100%;background:#16181b;border:1px solid #2a2e33;border-radius:8px;z-index:9"></div>
</div>
<div id="tabs">
  <button data-m="all" class="on">总览</button>
  <button data-m="pvp">PVP</button>
  <button data-m="pve">PVE</button>
  <button data-m="gambit">智谋</button>
  <button data-m="history">战绩</button>
  <button data-m="raid">Raid突袭</button>
  <button data-m="dungeon">地牢</button>
  <button data-m="wpvp">PVP生涯武器</button>
  <button data-m="wpve">PVE生涯武器</button>
  <button data-m="gm">宗师</button>
  <button data-m="heat">热力图</button>
  <button data-m="titles">称号</button>
  <button data-m="patterns">锻造</button>
</div>
<div id="scopebar">
  <span>生涯武器统计范围</span>
  <select id="scope" data-for="wpvp">__SCOPES__</select>
  <select id="scope_pve" data-for="wpve">__SCOPES_PVE__</select>
  <span>（要逐场拉对局详情；拉过的场次会缓存，换赛季/换人很快）</span>
</div>
<div id="bar" style="display:none;width:760px;height:26px;background:#16181b;border:1px solid #2a2e33;border-radius:8px;margin-bottom:12px;position:relative;overflow:hidden">
<div id="barfill" style="height:100%;width:0;background:linear-gradient(90deg,#35c66b,#4b8fd4);transition:width .4s"></div>
<span id="bartxt" style="position:absolute;left:0;right:0;top:0;bottom:0;text-align:center;line-height:26px;font-size:13px;color:#e8e6e3">加载中…</span>
</div>
<iframe id="card"></iframe>
<div id="tip">输入 ID 查询后，用上方标签切换该玩家的各项数据（已查看过的标签秒开）；武器/Perk 查询随时可用，无需先查玩家</div>
<script>
let cur='';
const cache=new Map();
function setBar(p,label){const b=document.getElementById('bar');
  b.style.display='block';
  document.getElementById('barfill').style.width=(p>=0?p:'30')+'%';
  document.getElementById('bartxt').textContent=label||(p>=0?('加载中 '+p+'%'):'加载中…');}
function hideBar(){document.getElementById('bar').style.display='none';}
function go(){
  const n=document.getElementById('name').value.trim();
  if(!n)return;
  cur=n;
  const f=document.getElementById('card');
  f.style.display='block';
  document.getElementById('tabs').style.display='flex';
  open('all');
}
const JOB_TABS={wpvp:'/start_wpvp',wpve:'/start_wpve',gm:'/start_gm'};
function scopeKey(m){
  const s=document.getElementById(m==='wpvp'?'scope':'scope_pve');
  return s&&s.value?s.value:'all';
}
function syncScopeBar(m){
  document.getElementById('scopebar').style.display=JOB_TABS[m]?'flex':'none';
  document.querySelectorAll('#scopebar select').forEach(s=>{s.style.display=(s.dataset.for===m||(m==='gm'&&s.dataset.for==='wpve'))?'':'none';});
}
function cacheSet(k,v){
  cache.set(k,v);
  if(cache.size>150)cache.delete(cache.keys().next().value);
}
async function load(url){
  const r=await fetch(url);const t=await r.text();
  document.getElementById('card').srcdoc=t;
}
async function open(m){
  document.querySelectorAll('#tabs button,#tools button').forEach(b=>b.classList.toggle('on',b.dataset.m===m));
  syncScopeBar(m);
  const key=cur+'|'+m;
  if(JOB_TABS[m]){await startJob(JOB_TABS[m],'/'+m+'_result',m,scopeKey(m));return;}
  if(m==='heat'){await startJob('/start_heat','/heat_result',m);return;}
  if(cache.has(key)){document.getElementById('card').srcdoc=cache.get(key);hideBar();return;}
  setBar(-1);
  try{
    const r=await fetch('/card?name='+encodeURIComponent(cur)+'&mode='+m);
    const t=await r.text();
    cacheSet(key,t);
    document.getElementById('card').srcdoc=t;
  }catch(e){alert('加载失败：'+e)}
  hideBar();
}
async function startJob(startUrl,resultUrl,m,extra){
  const key=cur+'|'+m+(extra?'|'+extra:'');
  if(cache.has(key)){document.getElementById('card').srcdoc=cache.get(key);hideBar();return;}
  const r=await fetch(startUrl+'?name='+encodeURIComponent(cur)+(extra?'&scope='+encodeURIComponent(extra):''));
  const j=await r.json();
  if(!j.job){alert('没找到玩家 '+cur);return;}
  setBar(0);
  try{
    while(true){
      await new Promise(s=>setTimeout(s,1000));
      const st=await (await fetch('/job/'+j.job)).json();
      if(st.status==='queued'){setBar(0,'排队中 · 第 '+(st.queue||1)+' 位（正在跑别的生涯任务，跑完就轮到你）');}
      else setBar(st.total?Math.round(st.done/st.total*100):0);
      if(st.status==='done'){
        const h=await (await fetch(resultUrl+'?job='+j.job)).text();
        cacheSet(key,h);document.getElementById('card').srcdoc=h;break;
      }
      if(st.status==='error'){alert('任务失败：'+st.error);break;}
    }
  }catch(e){alert('加载失败：'+e)}
  hideBar();
}
document.querySelectorAll('#tabs button,#tools button').forEach(b=>b.onclick=()=>{if(cur)open(b.dataset.m)});
document.querySelectorAll('#scopebar select').forEach(s=>s.onchange=()=>{
  const t=document.querySelector('#tabs button.on');
  if(cur&&t&&JOB_TABS[t.dataset.m])open(t.dataset.m);
});
document.getElementById('name').addEventListener('keydown',e=>{if(e.key==='Enter'){hideSug();go()}});
let sugTimer=null;
const inp=document.getElementById('name');
inp.addEventListener('input',()=>{clearTimeout(sugTimer);sugTimer=setTimeout(showSug,400)});
inp.addEventListener('blur',()=>setTimeout(hideSug,300));
async function showSug(){
  const q=inp.value.trim();
  if(q.length<3){hideSug();return;}
  const j=await (await fetch('/api/suggest?type=player&q='+encodeURIComponent(q))).json();
  const box=document.getElementById('sug');
  box.innerHTML=j.items.map(it=>`<div class='sugrow' onclick="pick('${it.n.replace(/'/g,"")}#${it.code}')">
    ${it.icon?`<img src='${it.icon}'>`:'<span style="width:28px;display:inline-block"></span>'}
    <b>${it.n}</b><span class='sugcode'>#${it.code}</span></div>`).join('');
  box.style.display=j.items.length?'block':'none';
}
function hideSug(){document.getElementById('sug').style.display='none'}
function pick(v){inp.value=v;hideSug();go()}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return (INDEX.replace("__NAV__", navbar("/"))
            .replace("__SCOPES__", scope_options("pvp"))
            .replace("__SCOPES_PVE__", scope_options("pve")))


@app.get("/card", response_class=HTMLResponse)
async def card(name: str, mode: str = "all", base: str = "", month: str = "",
               amode: int = 4, diff: str = ""):
    try:
        if mode == "pvp":
            rep = await d2.mode_report(name, 5)
            life = await d2.lifetime_stats(name, "allPvP")
            return HTMLResponse(render_match_card(rep, "PVP 熔炉竞技场战绩", "5", life,
                                                  ("activitiesWon", "assists", "precisionKills")))
        if mode == "pve":
            rep = await d2.mode_report(name, 7)
            life = await d2.lifetime_stats(name, "allPvE")
            return HTMLResponse(render_match_card(rep, "PVE 战绩", "7", life, ("precisionKills",)))
        if mode == "gambit":
            rep = await d2.mode_report(name, 63)
            return HTMLResponse(render_match_card(rep, "智谋战绩", "63"))
        if mode == "heat":
            return HTMLResponse("<h2 style='color:#eee;font-family:sans-serif'>请通过首页标签进入热力图（后台任务模式）</h2>")
        if mode == "titles":
            data = await d2.node_report(name, "titles")
            return HTMLResponse(render_nodes(data))
        if mode == "patterns":
            data = await d2.node_report(name, "patterns")
            return HTMLResponse(render_nodes(data))
        if mode == "history":
            data = await d2.history_report(name)
            return HTMLResponse(render_history_card(data))
        if mode == "raid":
            data = await d2.raid_report(name, 4)
            return HTMLResponse(render_raid_card(data, "Raid 突袭战绩", name, 4))
        if mode == "dungeon":
            data = await d2.raid_report(name, 82)
            return HTMLResponse(render_raid_card(data, "地牢战绩", name, 82))
        if mode == "raidg":
            # amode 必须沿用点进来时那一栏（4=突袭 / 82=地牢），否则地牢会拿突袭历史去筛，全是 0
            data = await d2.raid_report(name, amode)
            return HTMLResponse(render_raid_detail(data, base, month, amode, diff))
        data = await d2.full_report(name)
    except LookupError as e:
        return HTMLResponse(f"<h2 style='color:#eee;font-family:sans-serif'>{e}</h2>")
    return HTMLResponse(render_card(data, mode))


@app.get("/start_wpvp")
async def start_wpvp(name: str, scope: str = "all"):
    jid = await d2.start_pvp_weapons(name, scope, who="网页面板")
    return {"job": jid}


@app.get("/start_wpve")
async def start_wpve(name: str, scope: str = "current"):
    jid = await d2.start_pve_weapons(name, scope, who="网页面板")
    return {"job": jid}


@app.get("/wpve_result", response_class=HTMLResponse)
async def wpve_result(job: str):
    j = d2.JOBS.get(job, {})
    if j.get("status") != "done":
        return HTMLResponse("<h2 style='color:#eee;font-family:sans-serif'>任务不存在或未完成</h2>")
    return HTMLResponse(render_wpvp(j["result"]))


@app.get("/start_gm")
async def start_gm(name: str, scope: str = "current"):
    jid = await d2.start_gm_report(name, scope, who="网页面板")
    return {"job": jid}


@app.get("/gm_result", response_class=HTMLResponse)
async def gm_result(job: str):
    j = d2.JOBS.get(job, {})
    if j.get("status") != "done":
        return HTMLResponse("<h2 style='color:#eee;font-family:sans-serif'>任务不存在或未完成</h2>")
    return HTMLResponse(render_gm(j["result"]))


@app.get("/start_heat")
async def start_heat(name: str):
    jid = await d2.start_heatmap(name, who="网页面板")
    return {"job": jid}


@app.get("/heat_result", response_class=HTMLResponse)
async def heat_result(job: str):
    j = d2.JOBS.get(job, {})
    if j.get("status") != "done":
        return HTMLResponse("<h2 style='color:#eee;font-family:sans-serif'>任务不存在或未完成</h2>")
    return HTMLResponse(render_heat(j["result"]))


@app.get("/job/{jid}")
async def job(jid: str):
    j = d2.JOBS.get(jid, {})
    return {"status": j.get("status", "unknown"), "done": j.get("done", 0),
            "total": j.get("total", 0), "error": j.get("error"),
            "queue": d2.queue_position(jid) if j.get("status") == "queued" else 0}


@app.get("/wpvp_result", response_class=HTMLResponse)
async def wpvp_result(job: str):
    j = d2.JOBS.get(job, {})
    if j.get("status") != "done":
        return HTMLResponse("<h2 style='color:#eee;font-family:sans-serif'>任务不存在或未完成</h2>")
    return HTMLResponse(render_wpvp(j["result"]))


@app.get("/api/suggest")
async def api_suggest(type: str = "weapon", q: str = ""):
    q = q.strip().lower()
    if not q:
        return {"items": []}
    if type == "weapon":
        items = [{"n": w["name"], "icon": w["icon"], "wm": w.get("watermark", "")}
                 for w in d2.suggest_weapons(q)]
    elif type == "perk":
        items = [{"n": p["name"], "icon": p["icon"]}
                 for p in d2.search_perks(q, limit=8)]
    elif type == "player":
        items = await d2.player_suggest(q)
    else:
        items = []
    return {"items": items}


@app.get("/catalog", response_class=HTMLResponse)
async def catalog_page():
    """全武器图鉴：一次拉全量索引，筛选/搜索/计数都在浏览器里算"""
    return HTMLResponse(CATALOG_PAGE.replace("__NAV__", navbar("/catalog"))
                        .replace("__ALIAS__", _json.dumps(weapon_filter.SYNONYM, ensure_ascii=False)))


@app.get("/api/catalog/index")
async def catalog_index():
    """图鉴索引（= 武器筛选索引 + 品质），由 build_weapon_catalog.py 生成"""
    path = d2._idx_file("weapon_catalog.json")
    if not os.path.exists(path):
        return JSONResponse({"error": "缺少 manifest_index/weapon_catalog.json，"
                                      "请先跑 build_weapon_catalog.py 再重新打包"}, status_code=503)
    return FileResponse(path, media_type="application/json",
                        headers={"Cache-Control": "max-age=600"})


@app.get("/perks", response_class=HTMLResponse)
async def perks_page(q: str = ""):
    res = d2.search_perks(q) if q else []
    # 同名条目（不同赛季版本 / 普通版与强化版）合并成一张卡，精确数值按名字索引不再重复贴
    groups: dict[str, list[dict]] = {}
    for p in res:
        groups.setdefault(p["name"], []).append(p)
    cards = ""
    for name, items in groups.items():
        base = items[0]
        stats = "".join(
            f"<span class='{('up' if v > 0 else 'dn')}'>{n} {v:+d}</span>"
            for n, v in (base.get("stats") or {}).items())
        descs = [it.get("desc") or "（无官方说明）" for it in items]
        descs = [descs[0]] + [d for d in descs[1:] if d != descs[0]]
        vnotes = "".join(f"<p class='vnote'><span class='dim'>同名变体</span>　{d}</p>" for d in descs[1:])
        prec = bot_cards._zh(name) or base.get("ci")
        ci = ""
        if prec:
            legend = "<div class='legend'>金色 ↑ 为升金（强化特性）后的数值</div>" if "↑" in prec else ""
            ci = (f"<div class='ci'><span class='cil'>社区数据 · 精确数值</span>"
                  f"{bot_cards.hl_enh_text(prec)}{legend}</div>")
        cards += (f"<div class='wcard'><img src='{base['icon']}'><div>"
                  f"<b>{base['name']}</b>"
                  + (f"<div class='stats'>{stats}</div>" if stats else "")
                  + f"<p>{descs[0]}</p>{vnotes}{ci}</div></div>")
    cards = cards or ("<p id='empty'>输入名称开始搜索</p>" if not q else "<p id='empty'>没找到</p>")
    return HTMLResponse(PERKS_PAGE.replace("__NAV__", navbar("/perks")).replace("__RESULTS__", cards).replace("__Q__", q))


def card_page(html: str, active: str, extra: str = "") -> HTMLResponse:
    """把 bot_cards 的卡片 HTML 当网页来用：卡片是按 900px 定宽出图的（body{width:900px}），
    直接打开会贴在左上角、导航挤成两行。这里补一层网页外壳——顶部导航 + 居中定宽内容列，
    与其余页面保持一致。extra 插在导航之后、卡片之前（如套装页的搜索框/分类筛选）。"""
    shell = ("<style>body{width:auto;max-width:940px;margin:0 auto;padding:24px 12px 38px}"
             "</style>")
    html = html.replace("</head>", shell + "</head>", 1)
    return HTMLResponse(html.replace('<div class="card">', navbar(active) + extra + '<div class="card">', 1))


@app.get("/eververse", response_class=HTMLResponse)
async def eververse_page(force: int = 0):
    """光尘商店页：与 QQ 卡片同一份排版（bot_cards.eververse_card），只在顶部补一行导航"""
    try:
        store = await d2.eververse_store(force=bool(force))
    except d2.BungieAuthRequired:
        return HTMLResponse(needs_bungie_page())
    except Exception as exc:  # noqa: BLE001
        return err_page("光尘商店获取失败",
                        "读取 Bungie 商店接口失败，稍后刷新重试。<br>"
                        f"<span style='color:#9aa0a6'>{d2.esc_err(exc)}</span>")
    return card_page(bot_cards.eververse_card(store), "/eververse")


@app.get("/rotation", response_class=HTMLResponse)
async def rotation_page(force: int = 0):
    """本周轮换页：与 QQ 卡片同一份排版（bot_cards.rotation_card），顶部补一行导航"""
    try:
        rot = await d2.rotation_week(force=bool(force))
    except Exception as exc:  # noqa: BLE001
        return err_page("本周轮换获取失败",
                        "读取 Bungie 里程碑接口失败，稍后刷新重试。<br>"
                        f"<span style='color:#9aa0a6'>{d2.esc_err(exc)}</span>")
    return card_page(bot_cards.rotation_card(rot), "/rotation")


_ARMOR_CATS = ("目的地", "先锋行动", "熔炉竞技场行动", "智谋行动", "突袭", "地牢", "活动")


@app.get("/armorsets", response_class=HTMLResponse)
async def armorsets_page(q: str = "", cat: str = ""):
    """护甲套装效果页：与 QQ 卡片同一份排版（bot_cards.armor_*_card），顶部补一行导航。
    默认出全量详情页（56 套 2/4 件效果全文，照搬 Starside）+ 搜索框 + 分类筛选；
    带 q（套装名或别名）出单套全文 / 候选索引；带 cat 只看该类别。"""
    if q.strip():
        res = d2.search_armor_sets(q)
        if not res:
            return card_page(bot_cards.notice(
                "没找到套装",
                [f"没有匹配「{bot_cards.esc(q)}」的套装；可以用别名，如 一愿、vog、kf"],
                kind="warn"), "/armorsets")
        if len(res) == 1:
            html = bot_cards.armor_set_card(res[0])
        else:
            html = bot_cards.armor_sets_card(res, q)
        form = ("<form class='as-search' method='get' action='/armorsets'>"
                "<input name='q' placeholder='搜套装名或别名：炽天使套 / 一愿 / vog / kf …'></form>")
        return card_page(html, "/armorsets", extra=form)
    sets = d2.all_armor_sets()
    if cat.strip() and cat in _ARMOR_CATS:
        sets = [s for s in sets if s["category"] == cat]
    html = bot_cards.armor_sets_full_card(sets)
    # 搜索框 + 分类筛选链（当前类别高亮），插在导航之后、内容之前
    chips = "".join(
        (f"<a class='as-cat{' on' if not cat else ''}' href='/armorsets'>全部</a>" if c == ""
         else f"<a class='as-cat{' on' if cat == c else ''}' href='/armorsets?cat={c}'>{c}</a>")
        for c in ("",) + _ARMOR_CATS)
    form = ("<form class='as-search' method='get' action='/armorsets'>"
            "<input name='q' placeholder='搜套装名或别名：炽天使套 / 一愿 / vog / kf …'></form>"
            f"<div class='as-cats'>{chips}</div>")
    return card_page(html, "/armorsets", extra=form)


# ---------- Bungie 账号授权（读实时商店等需要登录的接口） ----------
def needs_bungie_page() -> str:
    return ("<div style='font:14px/1.8 sans-serif;color:#c5cacd;padding:20px;max-width:640px'>"
            "<h2 style='color:#e8e6e3'>需要先授权 Bungie 账号</h2>"
            "光尘商店是「登录后才能读」的接口，请到 <a style='color:#4b8fd4' href='/panel'>Bot 面板</a> "
            "点「授权 Bungie 账号」。</div>")


@app.get("/api/bungie/status")
def bungie_status():
    return bungie_auth.status()


@app.post("/api/bungie/refresh")
async def bungie_refresh():
    """手动续期：token 过期就用 refresh_token 换新的（refresh_token 一次性，失败报真实原因）"""
    if not bungie_auth.authorized():
        return {"ok": False, "error": "尚未授权"}
    try:
        await bungie_auth.access_token()  # 未过期时直接复用，不白白消耗一次性 refresh_token
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.get("/bungie/authorize")
def bungie_authorize():
    if not bungie_auth.configured():
        return HTMLResponse(
            "<div style='font:14px/1.9 sans-serif;color:#c5cacd;padding:20px;max-width:680px'>"
            "<h2 style='color:#e8e6e3'>还没配置 Bungie 应用</h2>"
            "在项目 <code>.env</code> 里补上：<br><br>"
            "<code>BUNGIE_CLIENT_ID=...</code><br><code>BUNGIE_CLIENT_SECRET=...</code><br>"
            "<code>BUNGIE_REDIRECT_URI=https://127.0.0.1:8902/bungie/callback</code><br><br>"
            "到 <a style='color:#4b8fd4' href='https://www.bungie.net/zh-chs/Application' "
            "target='_blank'>bungie.net/zh-chs/Application</a> 打开你的应用（有 API Key 的那个）："
            "「开放授权客户端类型」选 <b>机密</b>，Redirect URL 填上面那行（Bungie 只收 https，"
            "填 http 会报「必须使用 http 以外的通信架构」），保存后把 Client ID / Client Secret "
            "抄进 .env，重启程序再来授权。</div>")
    return RedirectResponse(bungie_auth.auth_url())


@app.post("/api/bungie/manual")
async def bungie_manual(request: dict):
    """手动完成授权：把浏览器地址栏里那条回调地址（或裸 code）粘进来。

    Bungie 只收 https 重定向，本机没证书 → 浏览器会报"不安全"，但地址栏已经带 code。
    """
    code, state = bungie_auth.parse_code((request or {}).get("text", ""))
    if not code:
        return {"ok": False, "error": "没找到 code，请把浏览器地址栏里整条地址复制过来"}
    try:
        await bungie_auth.exchange(code, bungie_auth.redirect_from_text((request or {}).get("text", "")))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"换取 token 失败：{exc}"}
    st = bungie_auth.status()
    return {"ok": True, "display_name": st.get("display_name", ""), "state_ok": bungie_auth.check_state(state)}


@app.get("/bungie/callback", response_class=HTMLResponse)
async def bungie_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    back = ("<div style='margin-top:14px'><a href='/panel' "
            "style='color:#4b8fd4;font:14px sans-serif'>← 返回面板</a></div>")
    if error or not code:
        return HTMLResponse(f"<h2 style='color:#ff8d85;font-family:sans-serif'>授权失败：{d2.esc_err(error or '没有拿到 code')}</h2>{back}")
    if not bungie_auth.check_state(state):
        return HTMLResponse(f"<h2 style='color:#ff8d85;font-family:sans-serif'>state 校验失败，请重新授权</h2>{back}")
    try:
        # 实际落地 URL 的 scheme://host:port/path 才是发 code 那次授权真正用的 redirect_uri，
        # 拿它去换 token（换 token 的校验只认这个），配置值作为兜底重试
        ru = f"{request.url.scheme}://{request.url.netloc}{request.url.path}"
        await bungie_auth.exchange(code, ru)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(f"<h2 style='color:#ff8d85;font-family:sans-serif'>换取 token 失败：{d2.esc_err(exc)}</h2>{back}")
    return HTMLResponse("<h2 style='color:#35c66b;font-family:sans-serif'>授权成功</h2>"
                        "<div style='font:14px sans-serif;color:#c5cacd'>可以关闭本页，回到面板或直接发 "
                        "<code>/每日光尘</code>。</div>"
                        "<script>setTimeout(function(){location.href='/panel'},2500)</script>" + back)


@app.post("/api/bungie/logout")
def bungie_logout():
    bungie_auth.logout()
    return {"ok": True}


@app.get("/pgcr", response_class=HTMLResponse)
async def pgcr(i: str):
    return HTMLResponse(await pgcr_page(i))


@app.get("/weapon", response_class=HTMLResponse)
async def weapon(hash: str = "", embed: int = 0):
    if hash:
        detail = d2.weapon_detail(hash)
        if detail:
            return HTMLResponse(render_weapon_detail(detail, embed=bool(embed)))
        return HTMLResponse("没找到该武器")
    # 旧的「按名称搜武器」入口已并入武器图鉴，老书签/老链接直接送过去
    return RedirectResponse("/catalog", status_code=307)


def _fmat(sec: int | None) -> str:
    return f"{sec // 60}分{sec % 60}秒" if sec else "-"


def _badge(label: str, n: int, cls: str = "") -> str:
    """标记徽章：为 0 时整块压暗（raid.report 式）"""
    return f"<span class='bd {cls}{'' if n else ' off'}'>{label}<b>{n}</b></span>"


def _raid_badges(g: dict, dungeon: bool = False) -> str:
    """raid.report 式徽章：参与/通关恒显；无暇/低人/首日/首周这类特殊通关只在有数时出现，
    为 0 不再压暗占位（用户 2026-10-02：直接只显示已完成的特殊通关）。

    「参与」= 该副本该难度的总场次（含没打完的），这样中途散的团也看得见；
    大师不再单独出徽章——它已经是卡片里的独立分栏。

    地牢（dungeon.report 口径，2026-10-05 前端代码核对）：徽章全集只有
    Solo / Solo Flawless / Flawless / Day One / Contest Day One / Contest /
    Week One / All Feats——没有 Duo（双人）系，特殊通关只出 无暇/单人无暇。
    2024-06 起官方把首日赛改叫竞赛模式（dungeon.report/raid.report 显示
    Contest）：发售时间在 2024-06-07（救赎的边缘）及之后的副本标签用「竞赛」。
    """
    out = [_badge("参与", g.get("plays", g["clears"]), "pl")]
    out.append(_badge("通关", g["clears"], "cl"))
    pairs = (("无暇", "flawless", "fw"),
             ("单人无暇", "solo_fl", "sf"), ("双人无暇", "duo_fl", "sf"))
    if dungeon:
        # dungeon.report 没有 Duo 系徽章，双人无暇不显示（详见函数头注释）
        pairs = pairs[:2]
    if not dungeon:
        pairs += (("单人", "solo", "lm"), ("双人", "duo", "lm"), ("三人", "trio", "lm"),
                  ("三人无暇", "trio_fl", "sf"))
    for lab, key, cls in pairs:
        if g[key]:
            out.append(_badge(lab, g[key], cls))
    if g.get("day_one"):
        # 首日/竞赛徽章带 raid.report 首日赛名次（api.raidreport.dev；查不到/没进榜就只显示次数）
        # rel_d1 = 发售+24h 的字符串时间戳；≥ 救赎的边缘的 rel_d1（2024-06-08 17:00）
        # 即 2024-06 竞赛时代之后的副本（该格式字典序与时间序一致）
        rel = str(g.get("rel_d1") or "")
        d1_lab = "竞赛" if rel and rel >= _CONTEST_ERA_TS else "首日"
        txt = f"{d1_lab} {g['day_one']}"
        tip = f"{d1_lab}窗口内通关 ×{g['day_one']}"
        r = g.get("d1_rank")
        if r and r.get("rank"):
            txt = f"{d1_lab} #{r['rank']}" + (f"/{r['total']}" if r.get("total") else "")
            tip = f"{d1_lab}通关 ×{g['day_one']} · 首日赛第 {r['rank']} 名（共 {r.get('total') or '?'} 队）"
        out.append(f"<span class='bd d1' title='{tip}'>{txt}</span>")
    if g.get("week_one"):
        out.append(_badge("首周", g["week_one"], "w1"))
    return "".join(out)


def _raid_rows(groups: list[dict], qname: str, amode: int, diff: str = "",
               dungeon: bool = False) -> str:
    """一栏副本行（同一难度口径）；diff 非空时链接带上难度，详情页只看该难度"""
    from urllib.parse import quote
    dq = f"&diff={quote(diff)}" if diff else ""
    rows = ""
    for g in groups:
        if not g.get("plays") and not g.get("clears"):  # 只打过没通关的也要列出来
            continue
        diffs = " / ".join(g["diffs"]) if g["diffs"] else ""
        dtag = f"<span class='rdiff'>{diffs}</span>" if diffs else ""
        rows += (f"<a class='rrow' href='/card?name={qname}&mode=raidg&amode={amode}"
                 f"&base={quote(g['name'])}{dq}'>"
                 f"<img src='{g['pgcr']}'>"
                 f"<div class='rin'><div class='rname'><b>{g['name']}</b>{dtag}</div>"
                 f"<div class='rbads'>{_raid_badges(g, dungeon)}</div></div>"
                 f"<span class='dim'>最快全程 {_fmat(g.get('ffc'))}<br>最近 {(g['last'] or '—')[:10]}</span></a>")
    return rows


# 官方从 2024-06 救赎的边缘起把「首日赛」改叫「竞赛模式」——发售在此之后的
# 副本，首日徽章标签用「竞赛」（dungeon.report/raid.report 同期显示 Contest）
_CONTEST_ERA_TS = "2024-06-08 17:00"   # 救赎的边缘 rel+24h；之后的发售=竞赛时代


def render_raid_card(rep: dict, title: str, name: str = "", amode: int = 4) -> str:
    from urllib.parse import quote
    dungeon = amode == 82
    qname = quote(name)
    std = _raid_rows(rep.get("raids") or [], qname, amode, dungeon=dungeon)
    mst = _raid_rows(rep.get("raids_master") or [], qname, amode, "大师", dungeon=dungeon)
    sections = ""
    if std:
        sections += f"<h2>标准难度</h2>{std}"
    if mst:
        sections += f"<h2>大师难度</h2>{mst}"
    if not sections:
        sections = ("<h2>副本统计</h2>"
                    "<p class='empty'>该玩家没有相关通关记录（对局历史按官方接口深度回溯，每人最多 40 页 × 250 场）</p>")
    # 地牢对齐 dungeon.report：特殊通关只有 无暇/单人无暇（前端没有 Duo 系徽章）
    fl_label = ("无暇 / 单人无暇" if dungeon
                else "无暇 / 单人无暇 / 双人无暇 / 三人无暇")
    fl_vals = (f"{rep['flawless']} / {rep['solo_fl']}" if dungeon
               else f"{rep['flawless']} / {rep['solo_fl']} / {rep['duo_fl']} / {rep['trio_fl']}")
    top = "".join([
        f"<div class='row hl'><span>总通关次数</span><b>{rep['total_clears']}</b></div>",
        f"<div class='row'><span>总参与次数（含未通关）</span><b>{rep.get('total_plays', rep['total_clears'])}</b></div>",
        f"<div class='row'><span>{fl_label}</span><b>{fl_vals}</b></div>",
        f"<div class='row'><span>大师通关</span><b>{rep['master']}</b></div>",
    ])
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>{title} · 数据来自 Bungie.net"
            + (" · 通关数/最快全程已对齐 raid.report（含官方历史已裁剪的老对局）"
               if rep.get("rr_aligned") else "")
            + " · 点副本查看细分统计</div>"
            f"{top}{sections}"
            f"<style>"
            f".rrow{{align-items:flex-start}}"
            f".rin{{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}}"
            f".rname{{display:flex;align-items:center;gap:8px;font-size:15px}}"
            f".rdiff{{color:#9aa0a6;font-size:11px}}"
            f".rbads{{display:flex;flex-wrap:wrap;gap:5px}}"
            f".bd{{display:inline-flex;align-items:center;gap:4px;font-size:11px;border-radius:5px;"
            f"padding:2px 7px;background:rgba(255,255,255,.05);color:#c5cacd}}"
            f".bd b{{font-size:12px}}"
            f".bd.off{{opacity:.32}}"
            f".bd.pl b{{color:#e8e6e3}}.bd.cl b{{color:#35c66b}}.bd.fw b{{color:#d4b26a}}.bd.ms b{{color:#ff8d85}}"
            f".bd.lm b{{color:#4b8fd4}}.bd.sf b{{color:#9b6bd4}}"
            f".bd.d1{{background:rgba(255,200,90,.14);border:1px solid rgba(255,200,90,.35)}}"
            f".bd.d1 b{{color:#ffc95c}}.bd.w1 b{{color:#8fd0ff}}"
            f"</style>")
    return CARD_CSS.replace("__BODY__", body)


def render_raid_detail(rep: dict, base: str, month: str, amode: int = 4, diff: str = "") -> str:
    from urllib.parse import quote
    qname = quote(rep["display"])
    ms = d2.filter_matches(rep["matches"], month=month, base=base, diff=diff)
    done = [m for m in ms if m["completed"]]
    # 与主卡同口径：无暇=0死+从头开始（full_run）+非私局；低人按全程出现过的账号数
    flawless = sum(1 for m in done if m["deaths"] == 0 and m.get("full_run", True)
                   and not m.get("private"))
    solo = sum(1 for m in done if m.get("low_accounts", m["player_count"]) == 1
               and not m.get("private"))
    duo = sum(1 for m in done if m.get("low_accounts", m["player_count"]) <= 2
              and not m.get("private"))
    trio = sum(1 for m in done if 0 < m.get("low_accounts", m["player_count"]) <= 3
               and not m.get("private"))
    solo_fl = sum(1 for m in done if m["deaths"] == 0 and m.get("full_run", True)
                  and not m.get("private") and m.get("low_accounts", m["player_count"]) == 1)
    duo_fl = sum(1 for m in done if m["deaths"] == 0 and m.get("full_run", True)
                 and not m.get("private") and m.get("low_accounts", m["player_count"]) <= 2)
    trio_fl = sum(1 for m in done if m["deaths"] == 0 and m.get("full_run", True)
                  and not m.get("private") and 0 < m.get("low_accounts", m["player_count"]) <= 3)
    master = sum(1 for m in done if m.get("diff") == "大师")
    best = min((m["duration"] for m in done), default=None)
    dungeon = amode == 82
    rname = base or (ms[0]["base"] if ms else "副本")
    months = sorted({(m.get("period_cn") or m["period"])[:7] for m in rep["matches"]
                     if m["base"] == base and (not diff or m.get("diff") == diff)}, reverse=True)
    link = (f"/card?name={qname}&mode=raidg&amode={amode}&base={quote(base)}"
            + (f"&diff={quote(diff)}" if diff else ""))
    mlinks = "".join(
        f"<a class='ml {'on' if mo == month else ''}' href='{link}&month={mo}'>{mo}</a>"
        for mo in months
    )
    mlinks = (f"<div class='months'><span class='dim'>按月：</span>"
              f"<a class='ml {'' if month else 'on'}' href='{link}'>全部</a>{mlinks}</div>")
    diffs = diff or (" / ".join(sorted({m["diff"] for m in ms if m.get("diff")})) or "标准")
    hdr = (
        f"<div class='row hl'><span>通关次数</span><b>{len(done)}</b></div>"
        f"<div class='row'><span>难度</span><b>{diffs}</b></div>"
        f"<div class='row'><span>无暇</span><b>{flawless}</b></div>"
        + (f"<div class='row'><span>单人通关</span><b>{solo}</b></div>"
           f"<div class='row'><span>单人无暇</span><b>{solo_fl}</b></div>" if dungeon else
           f"<div class='row'><span>单人 / 双人 / 三人通关</span><b>{solo} / {duo} / {trio}</b></div>"
           f"<div class='row'><span>单人无暇 / 双人无暇 / 三人无暇</span><b>{solo_fl} / {duo_fl} / {trio_fl}</b></div>")
        + (f"<div class='row'><span>大师通关</span><b>{master}</b></div>" if master else "")
        + f"<div class='row'><span>最快通关</span><b>{_fmat(best)}</b></div>"
        f"<div class='row'><span>总击杀</span><b>{sum(m['kills'] for m in done):,}</b></div>"
    )
    body = (f"<h1>{rname}</h1>"
            f"<div class='sub'>{rep['display']} · 副本细分战绩{(' · ' + month) if month else ''}</div>"
            f"{mlinks}{hdr}{render_matches(ms, limit=40)}")
    return CARD_CSS.replace("__BODY__", body)


def mode_tags(modes: list) -> str:
    """活动的模式标签，取 Manifest 中文名（试炼/铁旗/打击…）"""
    return "".join(f"<span class='mtag'>{d2.MODES[m]['name']}</span>"
                   for m in (modes or []) if m in d2.MODES)


def render_history_card(rep: dict) -> str:
    rows = ""
    for m in rep["matches"][:80]:
        dur = f"{m['duration'] // 60}m{m['duration'] % 60:02d}s"
        tag, tcls = match_result(m)
        mtag = f"<span class='mtag'>{m['mode_name']}</span>" if m.get("mode_name") else ""
        kda = f"{(m['kills'] + m['assists']) / max(1, m['deaths']):.1f}"
        # 小日向式四列：标签在上、数值在下，等宽对齐
        cols = "".join(
            f"<div class='hcol{' hl' if i == 2 else ''}'><span>{lab}</span><b>{val}</b></div>"
            for i, (lab, val) in enumerate(
                [("击杀", m['kills']), ("死亡", m['deaths']),
                 ("KD", f"{m['kd']:.1f}"), ("KDA", kda)]))
        rows += (
            f"<a class='mrow hist' href='/pgcr?i={m['instance']}'>"
            f"<img src='{m['pgcr']}'>"
            f"<div class='mi'><div class='hname'>{m['name']}{tag}{mtag}</div>"
            f"<span class='dim'>{(m.get('period_cn') or m['period'])[5:16]} · 用时 {dur}</span></div>"
            f"<div class='hcols'>{cols}</div></a>"
        )
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>最近战绩 · 共 {len(rep['matches'])} 场 · 点击查看对局详情</div>{rows}"
            "<style>"
            ".hname{font-size:14px;font-weight:600;margin-bottom:2px}"
            ".hcols{display:grid;grid-template-columns:repeat(4,minmax(46px,auto));gap:0 13px;"
            "text-align:right;flex-shrink:0}"
            ".hcol span{display:block;color:#9aa0a6;font-size:11px;line-height:1.5}"
            ".hcol b{font-size:14px;color:#e8e6e3;line-height:1.4}"
            ".hcol.hl b{color:#35c66b}"
            "</style>")
    return CARD_CSS.replace("__BODY__", body)


def render_nodes(rep: dict) -> str:
    return render_patterns(rep)


def render_patterns(rep: dict) -> str:
    from collections import OrderedDict
    groups: OrderedDict[str, list] = OrderedDict()
    for it in rep["items"]:
        groups.setdefault(it["group"], []).append(it)
    sections = ""
    for g, items in groups.items():
        done = sum(1 for i in items if i["completed"])
        color = "#35c66b" if done == len(items) else "#d4b26a"
        # 称号里只有一部分可镀金，组标题单独给出镀金进度
        gildable = [i for i in items if i.get("can_gild")]
        gild_note = ""
        if gildable:
            gd = sum(1 for i in gildable if i.get("gilded"))
            gild_note = (f"<span class='pgildcount'>镀金 {gd} / {len(gildable)}</span>")
        cards = ""
        for it in items:
            cls = "pcard on" if it["completed"] else "pcard off"
            if it.get("gilded"):
                cls += " gold"
            tags = ""
            if it.get("gilded"):
                tags += "<span class='ptag gold'>已镀金</span>"
            elif it.get("can_gild"):
                tags += "<span class='ptag gild'>可镀金</span>"
            tags += ("<span class='ptag got'>已获得</span>" if it["completed"]
                     else "<span class='ptag not'>未获得</span>")
            icon_cls = " class='gild'" if it.get("gilded") else (" class='gildable'" if it.get("can_gild") else "")
            sub = ""
            bar = ""
            bits = []
            if it.get("type"):
                bits.append(esc(it["type"]))
            if it.get("total_n"):
                lab = "" if it.get("type") else "进度 "
                bits.append(f"{lab}<b>{it['done_n']}/{it['total_n']}</b>")
            if bits:
                sub = f"<span class='psub'>{' · '.join(bits)}</span>"
            if it.get("total_n"):
                pct = round(it["done_n"] / it["total_n"] * 100) if it["total_n"] else 0
                bar = f"<span class='pbar'><i style='width:{pct}%'></i></span>"
            cards += (f"<div class='{cls}' title='{it['desc']}'>"
                      + (f"<img src='{it['icon']}'{icon_cls}>" if it["icon"] else "")
                      + f"<div class='pinfo'><span class='pname'>{it['name']}</span>{sub}{bar}</div>"
                      + f"<div class='ptags'>{tags}</div></div>")
        sections += (f"<div class='phead'><span class='paccent'></span>{g}{gild_note}"
                     f"<span class='pcount' style='color:{color}'>{done} / {len(items)}</span></div>"
                     f"<div class='pgrid'>{cards}</div>")
    gild_sum = ""
    if rep.get("gildable"):
        gild_sum = (f"<div class='row'><span>可镀金 / 已镀金</span>"
                    f"<b>{rep['gildable']} / {rep['gilded']}</b></div>")
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>{rep['title']} · 数据来自 Bungie.net · 悬停查看说明</div>"
            f"<div class='row hl'><span>完成数</span><b>{rep['done']} / {rep['total']}</b></div>"
            f"{gild_sum}"
            f"{sections}"
            f"<style>"
            f".phead{{display:flex;align-items:center;gap:8px;margin:16px 0 8px;font-size:15px;"
            f"font-weight:bold;color:#e8e6e3}}"
            f".paccent{{width:4px;height:16px;border-radius:2px;background:#35c66b}}"
            f".pcount{{margin-left:auto;font-size:13px}}"
            f".pgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(214px,1fr));gap:6px}}"
            f".pcard{{display:flex;align-items:center;gap:8px;background:#16181b;"
            f"border:1px solid #2a2e33;border-radius:8px;padding:6px 8px;min-width:0}}"
            f".pcard.on{{border-color:rgba(53,198,107,.35)}}"
            f".pcard.off{{opacity:.45}}"
            f".pcard.gold{{border-color:rgba(212,178,106,.55);"
            f"background:linear-gradient(150deg,rgba(212,178,106,.10),#16181b 60%)}}"
            f".pcard img{{width:36px;height:36px;border-radius:6px;background:#0f1113;flex-shrink:0}}"
            f".pcard img.gild{{box-shadow:0 0 0 2px #d4b26a,0 0 8px rgba(212,178,106,.55)}}"
            f".pcard img.gildable{{box-shadow:0 0 0 2px rgba(212,178,106,.38)}}"
            f".pinfo{{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}}"
            f".pname{{font-size:12px;line-height:1.3;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
            f".psub{{color:#9aa0a6;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
            f".psub b{{color:#e8e6e3}}"
            f".pbar{{display:block;height:3px;border-radius:2px;background:#2a2e33;overflow:hidden}}"
            f".pbar i{{display:block;height:100%;background:linear-gradient(90deg,#35c66b,#35c66b)}}"
            f".ptags{{display:flex;flex-direction:column;align-items:flex-end;gap:3px;flex-shrink:0}}"
            f".ptag{{font-size:10px;line-height:1.5;border-radius:4px;padding:0 5px;flex-shrink:0;white-space:nowrap}}"
            f".ptag.got{{color:#35c66b;background:rgba(53,198,107,.15)}}"
            f".ptag.gold{{color:#16181b;background:#d4b26a;font-weight:bold}}"
            f".ptag.gild{{color:#d4b26a;background:transparent;border:1px solid rgba(212,178,106,.5)}}"
            f".ptag.not{{color:#999;background:rgba(154,160,166,.15)}}"
            f".pgildcount{{color:#d4b26a;font-size:12px;font-weight:normal;margin-left:2px}}"
            f"</style>")
    return CARD_CSS.replace("__BODY__", body)


def scope_options(kind: str = "pvp") -> str:
    """生涯武器统计范围下拉。

    PVP：全生涯排第一（默认），后面各赛季（新的在前）。
    PVE：场次太多，默认当前赛季（排第一），赛季之后才是全生涯，并标注代价。
    """
    if kind == "pve":
        cur = d2.current_season()
        opts = []
        if cur:
            opts.append(f'<option value="s{cur["number"]}" selected>S{cur["number"]} '
                        f'{esc(str(cur.get("name", "")))}（当前赛季）</option>')
        for s in reversed(d2.SEASONS):
            if cur and s["number"] == cur["number"]:
                continue
            opts.append(f'<option value="s{s["number"]}">S{s["number"]} '
                        f'{esc(str(s.get("name", "")))}</option>')
        opts.append('<option value="all">全生涯（全部 PVE 对局，首次要跑很久）</option>')
        return "".join(opts)
    opts = ['<option value="all">全生涯（全部 PVP 对局）</option>']
    for s in reversed(d2.SEASONS):
        opts.append(f'<option value="s{s["number"]}">S{s["number"]} '
                    f'{esc(str(s.get("name", "")))}</option>')
    return "".join(opts)


def render_wpvp(rep: dict) -> str:
    kind = rep.get("kind") or "pvp"
    mode_label = "PVE" if kind == "pve" else "PVP"
    weapons = rep["weapons"][:60]
    top = max(1, weapons[0]["kills"]) if weapons else 1
    # 汇总块用全量（rep 里给的是全量），卡片只列前 60 把
    weapon_kills = rep.get("weapon_kills") or sum(w["kills"] for w in weapons)
    precision = rep.get("weapon_precision") or sum(w["precision"] for w in weapons)
    kills = rep.get("kills") or weapon_kills

    def chip(label: str, val: str, sub: str = "", cls: str = "") -> str:
        sub_html = f"<span class='csub'>{sub}</span>" if sub else ""
        return (f"<div class='chip {cls}'><span class='clab'>{label}</span>"
                f"<b>{val}</b>{sub_html}</div>")

    rows = ""
    medal = ["#d4b26a", "#c5cacd", "#e0965a"]
    for i, w in enumerate(weapons):
        rank = (f"<span class='rk' style='color:{medal[i]};border-color:{medal[i]}'>{i + 1}</span>"
                if i < 3 else f"<span class='rk'>{i + 1}</span>")
        pct = w["kills"] / top * 100
        share = w["kills"] / max(1, weapon_kills) * 100
        hs = w["precision"] / w["kills"] * 100 if w["kills"] else 0.0
        rows += (f"<div class='wrow'>"
                 f"{rank}"
                 f"<img src='{w['icon']}'>"
                 f"<div class='wn'><b>{esc(w['name'])}</b>"
                 f"<span class='wtype'>{esc(w['type'] or '未知')}</span></div>"
                 f"<div class='kbar'><div class='kfill' style='width:{pct:.1f}%'></div>"
                 f"<span class='kn'>{w['kills']:,}</span></div>"
                 f"<div class='wstat'>{w['matches']}</div>"
                 f"<div class='wstat'>{w['kills'] / max(1, w['matches']):.1f}</div>"
                 f"<div class='wstat dim'>{share:.1f}%</div>"
                 f"<div class='wstat dim'>{w['precision']:,}</div>"
                 f"<div class='wstat{' hs' if hs >= 40 else ''}'>{hs:.0f}%</div>"
                 f"</div>")
    if not rows:
        rows = f"<p class='empty'>这个范围里没有 {mode_label} 击杀数据</p>"
    chips = (chip("总击杀", f"{kills:,}")
             + chip("武器击杀", f"{weapon_kills:,}", f"占 {weapon_kills / max(1, kills) * 100:.0f}%")
             + chip("精准击杀", f"{precision:,}", f"爆头率 {precision / max(1, weapon_kills) * 100:.0f}%", "acc")
             + chip("近战", f"{rep.get('melee', 0):,}")
             + chip("手雷", f"{rep.get('grenade', 0):,}")
             + chip("大招", f"{rep.get('super', 0):,}"))
    rng = rep.get("range") or ("", "")
    span = f" · {rng[0]} ~ {rng[1]}" if rng and rng[0] else ""
    note = (f"（已达逐场统计上限 {rep['matches']:,} 场）" if rep.get("capped") else "")
    note += f" · {rep['missed']} 场详情未取到" if rep.get("missed") else ""
    if rep.get("cached"):  # 命中汇总缓存：说明这次只补拉了多少新对局
        note += f" · 缓存复用，本次只补 {rep.get('added', 0)} 场"
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>{mode_label} 生涯武器 · <b class='sl'>{esc(rep.get('scope_label') or '全生涯')}</b> "
            f"{rep['matches']:,} 场对局{span} · {len(rep['weapons'])} 种武器{note}</div>"
            f"<div class='chips'>{chips}</div>"
            f"<div class='whead'><span></span><span></span><span>武器</span>"
            f"<span style='text-align:left'>击杀</span>"
            f"<span>出场</span><span>场均</span><span>占比</span><span>精准</span><span>爆头率</span></div>"
            f"{rows}"
            f"<div class='dim' style='margin-top:10px'>"
            f"击杀条以榜首为基准；占比 = 该武器击杀 / 武器击杀合计；爆头率 = 精准击杀 / 该武器击杀；"
            f"近战 / 手雷 / 大招是击杀方式，不计在武器里。"
            + ("探索 / 巡逻类对局不计入 PVE 统计；PVE 里技能与灼烧 / 电击这类伤害占大头，"
               "Bungie 不逐把归属，所以武器击杀占比通常在 50%~65%。" if kind == "pve" else "") +
            f"</div>"
            f"<style>"
            f".sl{{color:#4b8fd4}}"
            f".chips{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:14px}}"
            f".chip{{background:#16181b;border:1px solid #2a2e33;border-radius:8px;padding:7px 8px;"
            f"text-align:center;display:flex;flex-direction:column;line-height:1.35}}"
            f".chip .clab{{color:#9aa0a6;font-size:11px}}"
            f".chip b{{color:#e8e6e3;font-size:17px}}"
            f".chip .csub{{color:#9aa0a6;font-size:11px}}"
            f".chip.acc{{border-color:rgba(212,178,106,.45)}}"
            f".chip.acc b{{color:#d4b26a}}"
            f".whead{{display:grid;grid-template-columns:34px 40px minmax(0,1fr) 130px 38px 38px 44px 42px 44px;"
            f"gap:5px;align-items:center;color:#9aa0a6;font-size:11px;padding:0 8px 6px;"
            f"border-bottom:1px solid #2a2e33}}"
            f".wrow{{display:grid;grid-template-columns:34px 40px minmax(0,1fr) 130px 38px 38px 44px 42px 44px;"
            f"gap:5px;align-items:center;background:#16181b;border:1px solid #2a2e33;border-radius:8px;"
            f"padding:7px 8px;margin:5px 0}}"
            f".wrow:hover{{border-color:#35c66b}}"
            f".rk{{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;"
            f"border:1px solid #2a2e33;border-radius:6px;font-size:12px;color:#9aa0a6}}"
            f".wrow img{{width:40px;height:40px;border-radius:6px;background:#0f1113}}"
            f".wn{{display:flex;flex-direction:column;line-height:1.4;min-width:0}}"
            f".wn b{{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
            f".wtype{{color:#9aa0a6;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
            f".kbar{{position:relative;height:18px;background:#0f1113;border-radius:5px;overflow:hidden}}"
            f".kfill{{height:100%;background:linear-gradient(90deg,#35c66b,#d4b26a);border-radius:5px}}"
            f".kn{{position:absolute;left:0;right:0;top:0;bottom:0;text-align:center;line-height:18px;"
            f"font-size:12px;color:#e8e6e3}}"
            f".wstat{{font-size:12px;text-align:right}}"
            f".wstat.dim{{color:#9aa0a6}}"
            f".wstat.hs{{color:#d4b26a}}"
            f"</style>")
    return CARD_CSS.replace("__BODY__", body)


def _gm_mmss(sec: int) -> str:
    if not sec:
        return "—"
    return f"{sec // 60}m {sec % 60:02d}s"


def render_gm(rep: dict) -> str:
    """/宗师：宗师征服 + 宗师警戒（日落）战绩，对齐 nightfall.report 的栏目"""
    rec = rep.get("records") or {}

    def chip(label: str, val: str, sub: str = "", cls: str = "") -> str:
        sub_html = f"<span class='csub'>{sub}</span>" if sub else ""
        return (f"<div class='chip {cls}'><span class='clab'>{label}</span>"
                f"<b>{val}</b>{sub_html}</div>")

    cq, ul = rec.get("conquest") or (0, 0), rec.get("ultimate") or (0, 0)
    chips = (chip("宗师征服进度", f"{cq[0]}/{cq[1]}")
             + chip("终极征服进度", f"{ul[0]}/{ul[1]}")
             + chip("历史镀金次数", str(rec.get("gilds", 0)), "伟大征服者")
             + chip("宗师对局", f"{rep.get('matches', 0):,}", f"扫描 {rep.get('scanned', 0):,} 场"))

    def rows(items: list[dict]) -> str:
        out = ""
        for g in items:
            tier_cls = {"终极": "ut", "宗师": "gm", "大师": "ms", "专家": "ex"}.get(g["tier"], "")
            out += (f"<div class='grow'>"
                    f"<span class='tier {tier_cls}'>{esc(g['tier'])}</span>"
                    f"<div class='gn'><b>{esc(g['strike'])}</b>"
                    f"<span class='dim'>最后 {esc(g['last'][:10] or '—')}</span></div>"
                    f"<div class='gs'>{g['clears']} 通关 / {g['attempts']} 次"
                    f"<span class='dim'> 通关率 {g['rate']:.1f}%</span></div>"
                    f"<div class='gt'>最快 {_gm_mmss(g['fastest'])}"
                    f"<span class='dim'> 平均 {_gm_mmss(g.get('avg', 0))}</span></div>"
                    f"</div>")
        return out or "<p class='empty'>这个范围里没有宗师对局</p>"

    rng = rep.get("range") or ("", "")
    span = f" · {rng[0]} ~ {rng[1]}" if rng and rng[0] else ""
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>宗师战绩 · <b class='sl'>{esc(rep.get('scope_label') or '当前赛季')}</b>"
            f"{span}</div>"
            f"<div class='chips'>{chips}</div>"
            f"<h2>征服（赛季中心，每赛季一轮）</h2>{rows(rep.get('conquests') or [])}"
            f"<h2>宗师警戒（日落轮换）</h2>{rows(rep.get('nightfalls') or [])}"
            f"<div class='dim' style='margin-top:10px'>通关率 = 通关 / 参战次数（含失败）；"
            f"最快 / 平均只统计通关场次；进度与镀金次数来自游戏内成就记录。</div>")
    style = (f"<style>"
             f".chips{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px}}"
             f".chip{{display:flex;align-items:center;gap:6px;background:#16181b;"
             f"border:1px solid #2a2e33;border-radius:8px;padding:7px 10px;font-size:13px}}"
             f".chip .clab{{color:#9aa0a6;font-size:11px}}"
             f".chip .csub{{color:#9aa0a6;font-size:11px}}"
             f".chip b{{color:#d4b26a}}"
             f".grow{{display:flex;align-items:center;gap:12px;background:#16181b;"
             f"border:1px solid #2a2e33;border-radius:8px;padding:8px 12px;margin:6px 0}}"
             f".grow:hover{{border-color:#35c66b}}"
             f".tier{{flex:0 0 44px;text-align:center;font-size:12px;border-radius:6px;"
             f"padding:3px 0;border:1px solid #2a2e33;color:#9aa0a6}}"
             f".tier.ut{{color:#ff9de2;border-color:#ff9de2}}"
             f".tier.gm{{color:#d4b26a;border-color:#d4b26a}}"
             f".tier.ms{{color:#9b6bd4;border-color:#9b6bd4}}"
             f".tier.ex{{color:#4b8fd4;border-color:#4b8fd4}}"
             f".gn{{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.5}}"
             f".gs{{flex:0 0 200px;font-size:13px}}"
             f".gt{{flex:0 0 200px;font-size:13px;text-align:right;white-space:nowrap}}"
             f".gt .dim{{margin-left:6px}}"
             f"</style>")
    return CARD_CSS.replace("__BODY__", body + style)


def render_heat(rep: dict) -> str:
    import datetime
    import calendar
    days = rep["days"]
    if not days:
        body = (f"<h1>{rep['display']}</h1>"
                f"<div class='sub'>赛季活跃度日历 · 全历史</div>"
                f"<p class='empty'>没有活动记录</p>")
        return CARD_CSS.replace("__BODY__", body)

    def hue_of(season_idx: int) -> int:
        return int(season_idx * 137.5) % 360  # 黄金角取色，相邻赛季色相差大

    def cell_color(hue: int, minutes: int) -> str:
        lv = min(4, minutes // 60 + 1)
        return f"hsl({hue},62%,{26 + lv * 10}%)"

    # 月份区间；每个月按 15 号归属赛季；相邻同赛季月合为一组
    dmin, dmax = min(days), max(days)
    y, mo = int(dmin[:4]), int(dmin[5:7])
    ey, emo = int(dmax[:4]), int(dmax[5:7])
    sections: list[dict] = []
    cur_season_key = None
    while (y, mo) <= (ey, emo):
        mid = f"{y:04d}-{mo:02d}-15"
        s = d2.season_of(mid)
        key = s["number"] if s else -1
        if key != cur_season_key:
            sections.append({"season": s, "months": []})
            cur_season_key = key
        sections[-1]["months"].append((y, mo))
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1

    parts = ""
    for idx, sec in enumerate(sections):
        s = sec["season"]
        hue = hue_of(idx)
        label = (f"S{s['number']} · {s['name']}（{s['start'][:7].replace('-', '年')}月起）"
                 if s else "早期记录（无赛季数据）")
        mcards = ""
        for (y, mo) in sec["months"]:
            mk = f"{y:04d}-{mo:02d}"
            total_m = sum(days[k]["minutes"] for k in days if k.startswith(mk))
            total_n = sum(days[k]["matches"] for k in days if k.startswith(mk))
            # 日均的天数分母只算落在记录区间内的日子：首月/当月不满整月时不会被拉低
            lo = max(datetime.date(y, mo, 1), datetime.date.fromisoformat(dmin))
            hi = min(datetime.date(y, mo, calendar.monthrange(y, mo)[1]),
                     datetime.date.fromisoformat(dmax))
            span_d = max(1, (hi - lo).days + 1)
            cells = ""
            first_wd = datetime.date(y, mo, 1).weekday()  # 周一=0
            cells += "<span class='mh'>一</span><span class='mh'>二</span><span class='mh'>三</span>" \
                     "<span class='mh'>四</span><span class='mh'>五</span><span class='mh'>六</span><span class='mh'>日</span>"
            cells += "<span class='mo'></span>" * first_wd
            for d in range(1, calendar.monthrange(y, mo)[1] + 1):
                k = f"{y:04d}-{mo:02d}-{d:02d}"
                info = days.get(k)
                if info:
                    style = f"background:{cell_color(hue, info['minutes'])}"
                    tip = f"{k}：{info['matches']} 场 / {info['minutes']} 分钟 / {info['kills']} 击杀"
                else:
                    style, tip = "", k
                cells += f"<span class='md' style='{style}' title='{tip}'></span>"
            hrs = f"{total_m // 60}小时{total_m % 60}分" if total_m >= 60 else f"{total_m}分钟"
            avg_n = total_n / span_d
            avg_m = total_m / span_d
            avg_txt = (f"{int(avg_m) // 60}小时{int(avg_m) % 60}分" if avg_m >= 60
                       else f"{avg_m:.0f}分")
            mcards += (f"<div class='mcal'><div class='mtitle'>{y}年{mo}月</div>"
                       f"<div class='msub'>{total_n} 场 · {hrs}</div>"
                       f"<div class='msub2'>日均 {avg_n:.1f} 场 · {avg_txt}</div>"
                       f"<div class='mgrid'>{cells}</div></div>")
        parts += (f"<div class='shead'><span class='sdot' style='background:hsl({hue},62%,40%)'></span>{label}</div>"
                  f"<div class='mrow'>{mcards}</div>")

    # 全历史日均：跨度按「首次记录 → 最近记录」的自然日算，另给一个只按有活动日子的日均
    span_all = max(1, (datetime.date.fromisoformat(dmax)
                       - datetime.date.fromisoformat(dmin)).days + 1)
    all_n = sum(v["matches"] for v in days.values())
    act_d = len(days)
    top = (f"全历史 <b>{all_n:,}</b> 场 · <b>{d2.fmt_hours(sum(v['minutes'] for v in days.values()))}</b>"
           f" · 活跃 <b>{act_d}</b> 天（跨度 {span_all:,} 天）"
           f" · 日均 <b>{all_n / span_all:.1f}</b> 场（活跃日日均 <b>{all_n / max(1, act_d):.1f}</b> 场）")
    # 数据从哪来的（缓存/补拉/重算），免得看到秒出图以为没跑
    newest = rep.get("newest_full") or ""
    if rep.get("cached") and rep.get("added"):
        src = f"上次统计到 <b>{esc(newest)}</b>，本次补拉新增 <b>{rep['added']}</b> 场"
    elif rep.get("cached"):
        src = f"直接复用缓存（已统计到 <b>{esc(newest)}</b>，没有新数据）"
    else:
        src = f"全历史重新统计，数据截至 <b>{esc(newest)}</b>" if newest else ""
    if src:
        top += f"<div style='margin-top:4px;color:#4b8fd4'>{src}</div>"

    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>赛季活跃度日历 · 全历史按天聚合 · 同一赛季同一颜色，越亮玩得越久</div>"
            f"<div class='hstat'>{top}</div>"
            f"{parts}"
            f"<style>"
            f".shead{{display:flex;align-items:center;gap:8px;margin:16px 0 8px;"
            f"font-size:14px;font-weight:bold;color:#e8e6e3}}"
            f".sdot{{width:10px;height:10px;border-radius:3px}}"
            f".hstat{{background:#16181b;border:1px solid #2a2e33;border-radius:8px;"
            f"padding:8px 12px;margin-bottom:4px;font-size:13px;color:#9aa0a6}}"
            f".hstat b{{color:#d4b26a}}"
            f".mrow{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}"
            f".mcal{{background:#16181b;border:1px solid #2a2e33;border-radius:10px;"
            f"padding:8px;box-sizing:border-box;min-width:0}}"
            f".mtitle{{font-size:13px;font-weight:bold;text-align:center}}"
            f".msub{{color:#9aa0a6;font-size:11px;text-align:center;line-height:1.5}}"
            f".msub2{{color:#4b8fd4;font-size:11px;text-align:center;line-height:1.5;"
            f"margin-bottom:6px}}"
            f".mgrid{{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}}"
            f".mh{{color:#6d737b;font-size:9px;text-align:center;line-height:1.3}}"
            f".mo{{aspect-ratio:1}}"
            f".md{{aspect-ratio:1;border-radius:3px;background:#1b1e22}}"
            f"</style>")
    return CARD_CSS.replace("__BODY__", body)


async def pgcr_page(instance: str) -> str:
    d = await d2.get_pgcr(instance)
    if not d:
        return "<h2 style='color:#eee;font-family:sans-serif'>对局数据获取失败或已超时过期</h2>"
    if not d["entries"]:
        return "<h2 style='color:#eee;font-family:sans-serif'>该对局没有公开数据</h2>"
    tk = sum(e["kills"] for e in d["entries"])
    td = sum(e["deaths"] for e in d["entries"])
    ta = sum(e["assists"] for e in d["entries"])
    mvp = d["entries"][0]["name"]
    rows = ""
    for i, e in enumerate(d["entries"]):
        if e["standing_text"] in ("胜利", "战败"):
            tag = f"<span class='tagw'>{e['standing_text']}</span>" if e["standing_text"] == "胜利" else f"<span class='tagl'>{e['standing_text']}</span>"
        else:
            tag = "<span class='tagw'>通关</span>" if e["completed"] else "<span class='tagd'>未通关</span>"
        mvp_tag = "<span class='mtag mvp'>MVP</span>" if i == 0 else ""
        weps = "".join(
            f"<div class='wrow'><img src='{w['icon']}'><span>{w['name']}</span><b>{w['kills']} 击杀</b></div>"
            for w in e.get("weapons", []) if w["kills"] > 0
        )
        wep_html = f"<details class='weps'><summary>武器明细</summary>{weps}</details>" if weps else ""
        rows += (f"<div class='pcard'><div class='pban'><img src='{e['emblem']}'>"
                 f"<div class='pbanname'><b>{e['name']}</b> {mvp_tag} {tag}"
                 f"<span class='pbandim'>{e['class']} · {e['light']} 光能 · 击杀占比 {e['kill_share']:.1f}%</span></div></div>"
                 f"<div class='pstats'>"
                 f"<div><i>击杀</i><b>{e['kills']}</b></div><div><i>死亡</i><b>{e['deaths']}</b></div>"
                 f"<div><i>协助</i><b>{e['assists']}</b></div><div><i>K/D</i><b>{e['kd']:.2f}</b></div>"
                 f"<div><i>得分</i><b>{e['score']}</b></div></div>{wep_html}</div>")
    summary = (f"<div class='grid'><section><h2>团队汇总</h2>"
               f"<div class='row'><span>总击杀</span><b>{tk:,}</b></div>"
               f"<div class='row'><span>总死亡</span><b>{td:,}</b></div>"
               f"<div class='row'><span>总协助</span><b>{ta:,}</b></div>"
               f"<div class='row hl'><span>团队 K/D</span><b>{tk / max(1, td):.2f}</b></div></section>"
               f"<section><h2>本场 MVP</h2>"
               f"<p class='mvpname'>{mvp}</p><p class='dim'>按得分排行第一名</p></section></div>")
    body = (f"<h1>{d['name']}</h1>"
            f"<div class='sub'>对局详情 · {d.get('period_cn') or d['period']}（北京时间） · 共 {len(d['entries'])} 名玩家</div>"
            f"{summary}<h2>玩家排行（点开武器明细）</h2>{rows}")
    return CARD_CSS.replace("__BODY__", body)




CATALOG_PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>武器图鉴</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;
     display:flex;flex-direction:column;align-items:center;padding:20px}
.wrap{width:100%;max-width:1440px}
.hd{display:flex;align-items:flex-end;gap:10px;margin:0 0 12px}
h1{font-size:22px;margin:0}
.hd .sub{color:#9aa0a6;font-size:11px;letter-spacing:2px;padding-bottom:3px}
.bar{display:flex;gap:10px;margin:10px 0 12px}
input{flex:1;padding:9px 14px;border-radius:8px;border:1px solid #2a2e33;background:#16181b;
      color:#e8e6e3;font-size:14px;outline:none}
input:focus{border-color:#4b8fd4}
button,a.btn{padding:9px 14px;border-radius:8px;border:1px solid #2a2e33;background:#16181b;
      color:#c5cacd;font-size:13.5px;cursor:pointer;text-decoration:none;white-space:nowrap;font-family:inherit}
button:hover,a.btn:hover{border-color:#4b8fd4;color:#fff}
.facets{background:#0f1113;border:1px solid #1b1e22;border-radius:12px;padding:8px 12px;margin-bottom:10px}
.fhdr{display:flex;justify-content:space-between;align-items:center;padding:0 0 5px}
.fhdr b{color:#7fb2e8;font-size:12px;letter-spacing:1px}
.facets.min .frow{display:none}
.frow{display:flex;gap:8px;align-items:flex-start;padding:3px 0;border-bottom:1px dashed #1b1e22}
.frow:last-child{border-bottom:none}
.flab{width:40px;flex:0 0 auto;color:#9aa0a6;font-size:11px;padding-top:5px;letter-spacing:1px}
.fchips{display:flex;flex-wrap:wrap;gap:5px;flex:1}
.fc{background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:3px 9px;font-size:12px;
    color:#c5cacd;cursor:pointer;user-select:none;white-space:nowrap}
.fc:hover{border-color:#4b8fd4}
.fc.on{background:#2b2417;border-color:#d4b26a;color:#d4b26a;font-weight:700}
.fc.z{opacity:.3}
.fc .n{color:#9aa0a6;font-size:10.5px;margin-left:4px;font-style:normal}
.fc.on .n{color:#d4b26a}
.fdot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:1px}
.fmore{color:#4b8fd4;font-size:11.5px;cursor:pointer;padding:4px 4px}
.fmore:hover{text-decoration:underline}
.rsbar{display:flex;align-items:center;gap:12px;margin:0 0 10px;color:#9aa0a6;font-size:13px}
.rsbar b{color:#d4b26a}
select{padding:6px 10px;border-radius:7px;border:1px solid #2a2e33;background:#16181b;color:#c5cacd;
       font-size:13px;font-family:inherit}
.wggrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(254px,1fr));gap:10px}
.wgcard{box-sizing:border-box;background:#16181b;border:1px solid #2a2e33;border-radius:10px;padding:8px 9px;
        text-decoration:none;color:#e8e6e3;display:flex;align-items:center;gap:9px;
        cursor:pointer;position:relative;overflow:hidden}
.wgcard:hover{border-color:#35c66b;background:#16181b}
/* 赛季水印只取左边缘那条竖带（Manifest 水印的有效像素就在左侧），单独裁成一条 spine；
   整张盖在图标上会把武器压住 → 现在武器 100% 可见 */
.wmslice{width:20px;height:56px;flex:0 0 auto;overflow:hidden;position:relative;background:#1b1e22;
         border-radius:4px}
.wmslice img{position:absolute;left:0;top:0;width:56px;height:56px}
.wgicon{position:relative;width:56px;height:56px;flex:0 0 auto;background:#1b1e22;border-radius:4px}
.wgicon>img{width:56px;height:56px;object-fit:contain}
.wgtxt{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.wgtxt b{font-size:13.5px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m2{display:block;color:#9aa0a6;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.el{font-size:12px;font-weight:700}
.craft{flex:0 0 auto;background:rgba(47,110,219,.22);border:1px solid #35c66b;
       color:#7fb2e8;border-radius:5px;font-size:10px;padding:1px 5px}
#more{display:none;margin:16px auto;padding:10px 22px}
.empty{color:#9aa0a6;padding:20px 0}
.ov{position:fixed;inset:0;background:rgba(4,7,13,.84);display:none;z-index:99;
    align-items:center;justify-content:center}
.ov.show{display:flex}
.ovb{width:860px;max-width:96vw;height:92vh;background:#0f1113;border:1px solid #2a2e33;border-radius:12px;
     display:flex;flex-direction:column;overflow:hidden;box-shadow:0 18px 60px rgba(0,0,0,.7)}
.ovh{display:flex;align-items:center;gap:12px;padding:9px 14px;background:#0f1113;border-bottom:1px solid #2a2e33}
.ovh b{flex:1;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ovh .hint{color:#9aa0a6;font-size:12px}
#ovf{flex:1;border:0;background:#0f1113}
</style></head><body>
<div class="wrap">
__NAV__
<div class="hd"><h1>武器图鉴</h1><span class="sub">DESTINY 2 · WEAPONS</span></div>
<div class="bar">
  <input id="q" placeholder="搜索武器名 / 类型 / 元素 / 框架 / Perk / 来源，例如：灾变 电弧 速射 喷子" autocomplete="off">
</div>
<div class="facets" id="facets"></div>
<div class="rsbar"><span id="cnt">加载中…</span>
  <select id="sort">
    <option value="def">默认排序</option>
    <option value="name">按名称</option>
    <option value="type">按类型</option>
    <option value="tier">按品质</option>
    <option value="rpm">按射速</option>
  </select>
  <button onclick="resetAll()" style="font-size:13px;padding:6px 12px">重置筛选</button>
</div><div class="wggrid" id="grid"></div>
<button id="more" onclick="PAGE+=STEP;draw()">加载更多</button>
</div>
<div class="ov" id="ov">
  <div class="ovb">
    <div class="ovh"><b id="ovn"></b><span class="hint">Esc 关闭</span>
      <button onclick="closeOv()" style="font-size:13px;padding:6px 12px">✕ 关闭</button></div>
    <iframe id="ovf" src="about:blank"></iframe>
  </div>
</div>
<script>
const ALIAS=__ALIAS__;
const TIER={6:'异域',5:'传说',4:'稀有',3:'罕见',2:'普通'};
const ELCLR={电弧:'#7fe3ff',烈日:'#ff9d6e',虚空:'#9b6bd4',冰影:'#8fd8ff',缚丝:'#35c66b',动能:'#c5cacd'};
const DIMS=[{k:'t',lab:'类型'},{k:'f',lab:'框架'},{k:'e',lab:'元素'},
            {k:'a',lab:'弹药'},{k:'c',lab:'槽位'},{k:'q',lab:'品质'}];
const SHOW=12, STEP=160;
const OPEN={};              // 维度 → 是否展开了全部取值
const F={t:new Set(),f:new Set(),e:new Set(),a:new Set(),c:new Set(),q:new Set()};
let CRAFT=false, MERGE=true, COLLAPSED=false, PAGE=STEP, ITEMS=[], POOL=[], TOKS=[];
const AL={};

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function lab(k,v){return k==='q'?(TIER[v]||('品质'+v)):v;}

function syncToks(){
  const v=document.getElementById('q').value.trim().toLowerCase();
  TOKS=v?v.split(/\s+/).filter(Boolean).map(x=>[x,AL[x]||'']):[];
}
// 一个词命中：原文或它的社区叫法（喷子→霰弹枪）出现在名称/类型/框架/元素/来源/Perk 里
function tokOk(it,c){return c.some(x=>x&&it.hay.indexOf(x)>=0);}
// matchOne 判单个版本；ok 判一张卡（合并后一张卡可能挂着同名多版本）
function matchOne(v,skip){
  for(const c of TOKS){if(!tokOk(v,c))return false;}
  for(const k in F){
    if(k===skip||!F[k].size)continue;
    if(!F[k].has(String(v.d[k])))return false;
  }
  return !(CRAFT&&skip!=='cr'&&!v.d.cr);
}
function ok(g,skip){return g.vs.some(v=>matchOne(v,skip));}
// 展示用哪一版：优先当前筛选真正命中的那版（同名版本元素/Perk 池可能不同），否则第一版
function pick(g){return g.vs.find(v=>matchOne(v,null))||g.vs[0];}
function counts(k){
  const m={};
  for(const g of POOL){
    const seen=new Set();
    for(const v of g.vs){
      if(!matchOne(v,k))continue;
      const val=String(v.d[k]);
      if(val&&val!=='0'&&!seen.has(val)){seen.add(val);m[val]=(m[val]||0)+1;}
    }
  }
  return m;
}
// 同一把武器在 Manifest 里按 perk 池分多个 hash（不同赛季版本），默认按名字合并成一张卡。
// 合并不是丢掉其它版本：筛选/计数仍在每条版本上算，命中哪版就显示哪版。
function buildPool(){
  const groups=new Map();
  ITEMS.forEach(it=>{
    if(!MERGE){groups.set(it.h,{vs:[it]});return;}
    let g=groups.get(it.d.n);
    if(!g)groups.set(it.d.n,g={vs:[]});
    g.vs.push(it);
  });
  POOL=[...groups.values()];
}
function drawFacets(){
  const box=document.getElementById('facets');
  const qn=POOL.filter(it=>ok(it,null)).length;
  let html=`<div class="fhdr"><b>筛选</b><span class="fmore" data-fold="1">${COLLAPSED?'展开筛选 ▼':'收起筛选 ▲'}</span></div>`;
  DIMS.forEach(dim=>{
    const c=counts(dim.k);
    let vals=Object.keys(c);
    if(dim.k==='q')vals.sort((a,b)=>b-a);
    else vals.sort((a,b)=>c[b]-c[a]||a.localeCompare(b,'zh'));
    // 已勾选但当前 0 命中的也要留着，否则取消不掉
    F[dim.k].forEach(v=>{if(vals.indexOf(v)<0)vals.push(v);});
    const open=OPEN[dim.k];
    const shown=open?vals:vals.slice(0,SHOW);
    let cs=`<span class="fc" data-all="${dim.k}">全部<i class="n">${qn}</i></span>`;
    shown.forEach(v=>{
      const n=c[v]||0, on=F[dim.k].has(v);
      const dot=dim.k==='e'?`<i class="fdot" style="background:${ELCLR[v]||'#9aa0a6'}"></i>`:'';
      cs+=`<span class="fc${on?' on':''}${n?'':' z'}" data-d="${dim.k}" data-v="${esc(v)}">${dot}${esc(lab(dim.k,v))}<i class="n">${n}</i></span>`;
    });
    if(vals.length>SHOW)cs+=`<span class="fmore" data-more="${dim.k}">${open?'收起':('展开全部 '+vals.length)}</span>`;
    html+=`<div class="frow"><div class="flab">${dim.lab}</div><div class="fchips">${cs}</div></div>`;
  });
  const crn=POOL.filter(g=>ok(g,'cr')&&g.vs.some(v=>v.d.cr)).length;
  html+=`<div class="frow"><div class="flab">其它</div><div class="fchips">
    <span class="fc${CRAFT?' on':''}" data-craft="1">可锻造（有图案）<i class="n">${crn}</i></span>
    <span class="fc${MERGE?' on':''}" data-merge="1">合并同名版本<i class="n">${POOL.length}/${ITEMS.length}</i></span></div></div>`;
  box.innerHTML=html;
  box.classList.toggle('min',COLLAPSED);
}
function sortList(l){
  const s=document.getElementById('sort').value;
  const byn=(a,b)=>a.d.n.localeCompare(b.d.n,'zh');
  if(s==='name')l.sort(byn);
  else if(s==='type')l.sort((a,b)=>a.d.t.localeCompare(b.d.t,'zh')||byn(a,b));
  else if(s==='tier')l.sort((a,b)=>b.tier-a.tier||byn(a,b));
  else if(s==='rpm')l.sort((a,b)=>(b.d.r||0)-(a.d.r||0)||byn(a,b));
  else l.sort((a,b)=>b.tier-a.tier||byn(a,b));
}
function card(it){
  const d=it.d, wm=d.w?`<span class="wmslice"><img loading="lazy" src="${esc(d.w)}"></span>`:'';
  const ec=ELCLR[d.e];
  const el=d.e?`<span class="el" style="color:${ec||'#9aa0a6'}">◈ ${esc(d.e)}</span> · `:'';
  const f2=[d.f,d.r?(d.r+' 射速'):''].filter(Boolean).join(' · ');
  return `<a class="wgcard" data-h="${esc(it.h)}" data-n="${esc(d.n)}">
    ${wm}<span class="wgicon"><img loading="lazy" src="${esc(d.i)}"></span>
    <span class="wgtxt"><b>${esc(d.n)}</b>
      <span class="m2">${el}${esc(TIER[d.q]||'')} · ${esc(d.t)}</span>
      <span class="m2">${esc(f2)}</span></span>
    ${d.cr?'<span class="craft">图案</span>':''}</a>`;
}
function draw(){
  const list=POOL.filter(g=>ok(g,null)).map(g=>{const v=pick(g);return {h:v.h,d:v.d,tier:v.tier};});
  sortList(list);
  const shown=list.slice(0,PAGE);
  const grid=document.getElementById('grid');
  grid.innerHTML=shown.length?shown.map(card).join(''):"<p class='empty'>没有符合筛选的武器</p>";
  const filtering=(TOKS.length||Object.keys(F).some(k=>F[k].size)||CRAFT);
  document.getElementById('cnt').innerHTML=(filtering?`筛选出 <b>${list.length}</b> 把 · `:`全图鉴 <b>${list.length}</b> 把武器 · `)
    +`显示 ${shown.length}${MERGE?'':' / 未合并同名'}`;
  const more=document.getElementById('more');
  more.style.display=list.length>shown.length?'block':'none';
  more.textContent=`加载更多（还有 ${list.length-shown.length}）`;
}
function resetAll(){
  Object.keys(F).forEach(k=>F[k].clear());
  CRAFT=false; PAGE=STEP;
  Object.keys(OPEN).forEach(k=>delete OPEN[k]);
  document.getElementById('q').value='';
  document.getElementById('sort').value='def';
  buildPool();syncToks();drawFacets();draw();
}
function openOv(h,n){
  document.getElementById('ovn').textContent=n;
  document.getElementById('ovf').src='/weapon?hash='+encodeURIComponent(h)+'&embed=1';
  document.getElementById('ov').classList.add('show');
}
function closeOv(){
  document.getElementById('ov').classList.remove('show');
  document.getElementById('ovf').src='about:blank';
}
document.getElementById('facets').onclick=e=>{
  const el=e.target.closest('.fc'), mo=e.target.closest('.fmore');
  if(el){
    if(el.dataset.all){F[el.dataset.all].clear();}
    else if(el.dataset.craft){CRAFT=!CRAFT;}
    else if(el.dataset.merge){MERGE=!MERGE;buildPool();}
    else{
      const k=el.dataset.d, v=el.dataset.v;
      if(F[k].has(v))F[k].delete(v);else F[k].add(v);
    }
    PAGE=STEP;drawFacets();draw();return;
  }
  if(mo){
    if(mo.dataset.fold)COLLAPSED=!COLLAPSED;
    else OPEN[mo.dataset.more]=!OPEN[mo.dataset.more];
    drawFacets();
  }
};
document.getElementById('grid').onclick=e=>{
  const c=e.target.closest('.wgcard');
  if(c)openOv(c.dataset.h,c.dataset.n);
};
document.getElementById('sort').onchange=()=>{PAGE=STEP;draw();};
let _t=null;
document.getElementById('q').addEventListener('input',()=>{
  clearTimeout(_t);_t=setTimeout(()=>{PAGE=STEP;syncToks();drawFacets();draw();},140);
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeOv();});
(async function(){
  Object.keys(ALIAS).forEach(k=>{AL[k]=String(k).toLowerCase();AL[String(k).toLowerCase()]=ALIAS[k];});
  AL['']='';
  let j={};
  try{j=await (await fetch('/api/catalog/index')).json();}catch(err){j={error:'读取图鉴索引失败：'+err};}
  if(j.error){
    document.getElementById('cnt').textContent='';
    document.getElementById('grid').innerHTML=`<p class='empty'>${esc(j.error)}</p>`;
    return;
  }
  const order=Object.entries(j);
  ITEMS=order.map(([h,d])=>({h,d,tier:d.q||0,
    hay:(d.n+' '+d.t+' '+d.f+' '+d.e+' '+d.g+' '+(d.p||[]).join(' ')).toLowerCase()}));
  buildPool();
  syncToks();
  drawFacets();draw();
  document.getElementById('q').focus();
})();
</script></body></html>"""


PERKS_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>Perk查询</title>
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;
     display:flex;flex-direction:column;align-items:center;padding:24px}
h1{font-size:22px}
.bar{display:flex;gap:10px;margin:14px 0;position:relative}
input,button{padding:10px 14px;border-radius:8px;border:1px solid #2a2e33;
     background:#16181b;color:#e8e6e3;font-size:15px;outline:none}
button{background:#35c66b;border:none;cursor:pointer;font-weight:bold}
#sug{position:absolute;top:46px;left:0;width:100%;background:#16181b;border:1px solid #2a2e33;border-radius:8px;z-index:9}
.sugrow{display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;color:#e8e6e3}
.sugrow:hover{background:#1b1e22}
.sugrow img{width:32px;height:32px;object-fit:contain;border-radius:4px}
.wcard{display:flex;gap:14px;background:#16181b;border:1px solid #2a2e33;border-radius:10px;
       padding:12px 16px;margin:10px 0;width:680px}
.wcard img{width:48px;height:48px;object-fit:contain;flex-shrink:0}
.wcard p{margin:4px 0 0;color:#9aa0a6;font-size:13px}
.wcard .stats{margin-top:4px}
.wcard .stats span{display:inline-block;margin-right:8px;padding:1px 6px;border-radius:4px;font-size:12px}
.wcard .stats .up{background:rgba(53,198,107,.12);color:#35c66b}
.wcard .stats .dn{background:rgba(217,72,63,.12);color:#ff8d85}
.wcard .ci{margin-top:6px;padding:6px 8px;background:#1b1e22;border-left:2px solid #35c66b;
       border-radius:4px;color:#9aa0a6;font-size:12px;line-height:1.6;white-space:pre-wrap}
.wcard .ci .cil{display:block;color:#4b8fd4;font-size:10px;letter-spacing:1px;margin-bottom:2px}
.wcard .ci .enh{color:#d4b26a;font-weight:700;background:rgba(212,178,106,.14);border-radius:4px;padding:0 3px}
.wcard .legend{color:#9aa0a6;font-size:11px;margin-top:3px}
.wcard .vnote{margin:3px 0 0;color:#9aa0a6;font-size:13px}
.wcard .vnote .dim{color:#9aa0a6}
#empty{color:#9aa0a6}
</style></head><body>
__NAV__
<h1>Perk 查询</h1>
<div class="bar">
  <input id="q" placeholder="输入 Perk 名称" size="30" value="__Q__" autocomplete="off">
  <button onclick="go()">搜索</button>
  <div id="sug" style="display:none"></div>
</div>
<div id="results">__RESULTS__</div>
<script>
const inp=document.getElementById('q');
let t=null;
inp.addEventListener('input',()=>{clearTimeout(t);t=setTimeout(showSug,350)});
inp.addEventListener('keydown',e=>{if(e.key==='Enter'){hideSug();go()}});
async function showSug(){
  const q=inp.value.trim();
  if(q.length<2){hideSug();return;}
  const j=await (await fetch('/api/suggest?type=perk&q='+encodeURIComponent(q))).json();
  const box=document.getElementById('sug');
  box.innerHTML=j.items.map(it=>`<div class='sugrow' onclick="fill('${it.n.replace(/'/g,"")}')">
    <img src='${it.icon}'><b>${it.n}</b></div>`).join('');
  box.style.display=j.items.length?'block':'none';
}
function fill(n){inp.value=n;hideSug();go()}
function hideSug(){document.getElementById('sug').style.display='none'}
function go(){
  const q=inp.value.trim();
  if(!q)return;
  location.href='/perks?q='+encodeURIComponent(q);
}
</script></body></html>"""


def render_weapon_detail(w: dict, embed: bool = False) -> str:
    """embed=1：图鉴页用 iframe 内嵌时用，去掉顶部导航和"返回搜索结果"（外层有自己的关闭键）"""
    html = DETAIL_PAGE.replace("__DATA__", _json.dumps(w, ensure_ascii=False))
    if embed:
        # 详情页 body 是固定 820px 宽的卡片排版，居中 + 去掉导航/返回键（弹层自带关闭）
        return (html.replace("__NAV__", "")
                .replace("</style></head>",
                         ".d2nav,.backlnk{display:none!important}body{margin:0 auto}</style></head>", 1))
    return html.replace("__NAV__", navbar("/catalog"))


DETAIL_PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<style>
body{margin:0 auto;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;
     width:820px;box-sizing:border-box;padding:20px}
a{color:#4b8fd4;text-decoration:none}
.head{display:flex;align-items:center;gap:14px;margin:10px 0}
.wi{position:relative;width:64px;height:64px;flex-shrink:0}
.wi img{width:64px;height:64px;object-fit:contain}
.wi .wm{position:absolute;left:0;top:0;width:64px;height:64px}
h1{margin:0;font-size:26px}
.meta{color:#4b8fd4;font-size:13px}
.shot{width:100%;max-height:190px;object-fit:cover;object-position:center;border-radius:10px;
      border:1px solid #2a2e33;margin:12px 0 4px;display:block}
.flavor{color:#9b6bd4;font-style:italic;font-size:13px;margin:6px 0 14px}
.sec{background:#0f1113;border:1px solid #1b1e22;border-radius:12px;padding:14px 16px;margin:12px 0}
.sec > h2{font-size:14px;color:#7fb2e8;margin:0 0 12px;font-weight:700;letter-spacing:.5px;
     display:flex;align-items:center;gap:8px}
.sec > h2::before{content:'';width:3px;height:14px;border-radius:2px;background:#35c66b}
.statwrap{display:grid;grid-template-columns:1fr 1fr;gap:2px 22px}
.srow{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px}
.sname{width:86px;color:#9aa0a6;text-align:right;flex-shrink:0}
.sbar{flex:1;height:10px;background:#1b1e22;border-radius:5px;overflow:hidden;display:flex}
.sbar i{display:block;height:100%}
.sb{background:linear-gradient(90deg,#35c66b,#4b8fd4)}
.sg{background:#35c66b}
.sr{background:#d9483f}
.sval{width:62px;text-align:left;font-size:12px}
.sval b{color:#d4b26a}
/* 绝对数量（射速/弹匣/充能时间）不画条，跨两列单独写一行 */
.plainstat{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:2px 20px;font-size:13px;
  color:#9aa0a6;margin:0 0 7px}
.plainstat b{color:#d4b26a;font-weight:700;margin-left:4px}
.dplus{color:#35c66b;font-size:11px}.dminus{color:#ff8d85;font-size:11px}
.cols{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.mgrid{display:flex;flex-wrap:wrap;gap:8px}
.mgrid .perk{width:100px;flex:0 0 auto}
.catcard{display:flex;gap:10px;background:#1b1e22;border:1px solid #2a2e33;border-radius:8px;
         padding:10px 12px;margin-bottom:8px}
.catcard img{width:44px;height:44px;object-fit:contain;flex:0 0 auto}
.catbody{flex:1;min-width:0}
.cattop b{color:#4b8fd4;font-size:14px;margin-right:8px}
.cattop .st{color:#d4b26a;font-weight:700;font-size:12.5px;margin-right:8px}
.catfx{color:#c5cacd;font-size:12.5px;line-height:1.6;margin-top:3px}
.catfx b{color:#9b6bd4}
.catci{color:#9aa0a6;font-size:12px;line-height:1.6;margin-top:4px;padding-top:4px;
       border-top:1px dashed #2a2e33}
.catunlock{color:#9aa0a6;font-size:12px;margin-top:3px}
.tcol{min-width:104px;flex:1;display:flex;flex-direction:column;gap:8px}
.perk{background:#16181b;border:1px solid #2a2e33;border-radius:8px;padding:8px 4px;
      display:flex;flex-direction:column;align-items:center;gap:4px;cursor:pointer}
.perk img{width:34px;height:34px;object-fit:contain}
.perk span{font-size:12px;text-align:center;line-height:1.3}
.perk.sel{border-color:#d4b26a;background:#2b2417;box-shadow:0 0 6px rgba(212,178,106,.35)}
.perk:hover{border-color:#4b8fd4}
.pst{display:flex;flex-wrap:wrap;gap:2px 6px;justify-content:center}
.pst em{font-style:normal;font-size:11px;color:#35c66b}
.pst em.dn{color:#ff8d85}
.clabel{color:#9aa0a6;font-size:11px;text-align:center;letter-spacing:.5px;padding-bottom:2px;
      border-bottom:1px solid #1b1e22;margin-bottom:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:flex;align-items:center;gap:6px;background:#1b1e22;border:1px solid #2a2e33;
      border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;color:#c5cacd}
.chip img{width:22px;height:22px;object-fit:contain}
.chip:hover{border-color:#4b8fd4}
.chip.on{border-color:#d4b26a;background:#2b2417}
.chip .st{color:#35c66b;font-size:11px}
.chip .st.dn{color:#ff8d85}
.hidesec{border-color:#1b1e22;background:#0f1113}
.hidesec > h2{cursor:pointer;color:#9aa0a6}
.empty{color:#9aa0a6;font-size:12px}
#tip{position:fixed;display:none;z-index:99;background:#1b1e22;border:1px solid #35c66b;
     border-radius:8px;padding:10px 12px;max-width:320px;font-size:12px;line-height:1.6;
     color:#9aa0a6;pointer-events:none;box-shadow:0 6px 20px rgba(0,0,0,.6)}
#tip b{color:#e8e6e3;font-size:13px;display:block;margin-bottom:4px}
#tip .tstats{margin-bottom:4px}
#tip .tstats span{display:inline-block;margin:2px 8px 2px 0;padding:1px 6px;border-radius:4px;
     background:rgba(53,198,107,.12);color:#35c66b;font-size:11px}
#tip .tstats span.dn{background:rgba(217,72,63,.12);color:#ff8d85}
#tip .tci{margin-top:6px;padding-top:6px;border-top:1px dashed #2a2e33;
     color:#9aa0a6;white-space:pre-wrap;font-size:11px}
#tip .tcilabel{color:#4b8fd4;font-size:10px;letter-spacing:1px;display:block;margin-bottom:2px}
</style></head><body>
__NAV__
<a href="javascript:history.back()" class="backlnk" style="display:inline-block;margin-bottom:10px">← 返回搜索结果</a>
<div class="head">
  <span class="wi"><img id="wicon"><img class="wm" id="wwm" style="display:none"></span>
  <div><h1 id="wname"></h1><div class="meta" id="wmeta"></div></div>
</div>
<img class="shot" id="wshot">
<p class="flavor" id="wflavor"></p>
<div class="sec"><h2>武器素体</h2><div id="stats" class="statwrap"></div></div>
<div class="sec"><h2>Perk 池</h2><div id="traits" class="cols"></div>
  <div class="dim" style="font-size:11px;margin-top:8px">点击选择（每列单选，再点取消），属性条实时联动；悬停看 Perk 说明与社区精确数值。</div></div>
<div class="sec"><h2>大师杰作</h2><div id="master" class="mgrid"></div></div>
<div class="sec" id="catsec" style="display:none"><h2>异域催化</h2><div id="catbox"></div></div>
<div class="sec"><h2>武器模组</h2><div id="mods" class="chips"></div></div>
<script>
const W=__DATA__;
const STAT_MAX={"冲击":100,"射程":100,"稳定性":100,"操控性":100,"填装速度":100,"弹匣":100,
  "后坐方向":100,"精准度":100,"变焦":100,"空中效率":100,"挥砍速度":100,"护盾穿透":100,
  "爆炸范围":100,"充能速度":100,"射击速度":450,"每分钟发射数":450,"弹药生成":100,"辅助瞄准":100};
document.getElementById('wicon').src=W.icon;
if(W.watermark){document.getElementById('wwm').src=W.watermark;}
else{document.getElementById('wwm').style.display='none';}
document.getElementById('wname').textContent=W.name;
const _frame=(W.plugs.intrinsic[0]||{}).n||'';
document.getElementById('wmeta').textContent=[_frame,W.cat,W.type,W.ammo].filter(Boolean).join(' · ');
if(W.screenshot)document.getElementById('wshot').src=W.screenshot;
else document.getElementById('wshot').style.display='none';
document.getElementById('wflavor').textContent=W.flavor||W.desc||'';

const sel={};           // key -> plug 对象
const STAT_ORDER=['每分钟发射数','伤害','射程','稳定性','操控性','填装速度','弹匣','辅助瞄准','变焦','后坐方向','空中效率','弹药生成','冲击','精度','充能时间','蓄力时间','充能速度','爆炸范围','挥舞速度','护盾穿透','举盾速度'];
// 绝对数量，不在 0–100 量程上（射速 257、弹匣 18、充能 800ms），不画条，单独写一行
const PLAIN_STATS=new Set(['每分钟发射数','射击速度','弹匣','弹药容量','弹头速度','充能时间','蓄力时间','蓄能时间']);
const base=[...W.stats].sort((a,b)=>{const ia=STAT_ORDER.indexOf(a.n),ib=STAT_ORDER.indexOf(b.n);return (ia<0?99:ia)-(ib<0?99:ib);});
const statNames=base.map(s=>s.n);

function plugKey(cat,i){return cat+'#'+i;}
// 默认选择：每列的第一个插件（列顺序来自索引 plugs.cols，老数据走下面的兜底）
if(W.plugs.cols&&W.plugs.cols.length){
  W.plugs.cols.forEach((c,ci)=>{
    if(c.items.length)sel[c.t.indexOf('特性')===0?('trait#'+ci):('col#'+ci)]=c.items[0];
  });
}else{
  W.plugs.trait_cols.forEach((c,i)=>{if(c.length)sel['trait#'+i]=c[0];});
  if(W.plugs.barrels.length)sel['barrels']=W.plugs.barrels[0];
  if(W.plugs.magazines.length)sel['magazines']=W.plugs.magazines[0];
  if(W.plugs.stocks&&W.plugs.stocks.length)sel['stocks']=W.plugs.stocks[0];
}

function compute(){
  const st={};base.forEach(s=>st[s.n]=s.v);
  Object.values(sel).forEach(p=>{
    for(const [n,v] of Object.entries(p.stats||{})) st[n]=(st[n]||0)+v;
  });
  return st;
}
function renderStats(){
  const st=compute();
  const box=document.getElementById('stats');box.innerHTML='';
  const dtxt=d=>d>0?`<span class='dplus'>+${d}</span>`:(d<0?`<span class='dminus'>${d}</span>`:'');
  const plain=base.filter(s=>PLAIN_STATS.has(s.n));
  if(plain.length)box.innerHTML+='<div class="plainstat">'+plain.map(s=>{
    const f=st[s.n]||0;
    return `<span>${s.n} <b>${f}</b>${dtxt(f-s.v)}</span>`;}).join('')+'</div>';
  base.filter(s=>!PLAIN_STATS.has(s.n)).forEach(s=>{
    const f=st[s.n]||0, d=f-s.v, max=STAT_MAX[s.n]||100;
    let inner='';
    const w1=Math.max(0,Math.min(f,s.v))/max*100;
    if(d>0){inner=`<i class='sb' style='width:${w1}%'></i><i class='sg' style='width:${Math.min(f,max)/max*100-w1}%'></i>`;}
    else if(d<0){inner=`<i class='sb' style='width:${Math.max(0,f)/max*100}%'></i><i class='sr' style='width:${Math.min(s.v,max)/max*100-Math.max(0,f)/max*100}%'></i>`;}
    else{inner=`<i class='sb' style='width:${Math.max(0,Math.min(f,max))/max*100}%'></i>`;}
    box.innerHTML+=`<div class='srow'><span class='sname'>${s.n}</span>
      <div class='sbar'>${inner}</div><span class='sval'><b>${f}</b> ${dtxt(d)}</span></div>`;
  });
}
// 悬停浮层：显示 perk 名称、参数、说明
const tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);
function bindTip(el,p){
  el.addEventListener('mouseenter',e=>showTip(e,p));
  el.addEventListener('mousemove',moveTip);
  el.addEventListener('mouseleave',()=>{tip.style.display='none';});
}
function showTip(e,p){
  let s=`<b>${p.n}</b>`;
  const st=Object.entries(p.stats||{});
  if(st.length)s+=`<div class='tstats'>${st.map(([n,v])=>
    `<span class='${v>0?'up':'dn'}'>${n} ${v>0?'+':''}${v}</span>`).join('')}</div>`;
  if(p.d)s+=`<div class='tdesc'>${p.d}</div>`;
  if(p.ci)s+=`<div class='tci'><span class='tcilabel'>社区数据 · 精确数值</span>${p.ci}</div>`;
  tip.innerHTML=s;tip.style.display='block';moveTip(e);
}
function moveTip(e){
  const r=tip.getBoundingClientRect();
  let x=e.clientX+14,y=e.clientY+14;
  if(x+r.width>window.innerWidth-8)x=e.clientX-r.width-10;
  if(y+r.height>window.innerHeight-8)y=e.clientY-r.height-10;
  tip.style.left=x+'px';tip.style.top=y+'px';
}
function makePerk(p,selected,onclick){
  const d=document.createElement('div');d.className='perk'+(selected?' sel':'');
  d.innerHTML=`<img src='${p.i}'><span>${p.n}</span>`;
  d.onclick=onclick;bindTip(d,p);
  return d;
}
function pick(cat,p,redraw){
  return ()=>{ if(sel[cat]&&sel[cat].hash===p.hash)delete sel[cat];else sel[cat]=p;  // 每列单选
    redraw();renderStats(); };
}
function column(label,list,cat,redraw){
  if(!list||!list.length)return null;
  const col=document.createElement('div');col.className='tcol';
  col.innerHTML=`<div class='clabel'>${label}</div>`;
  list.forEach(p=>col.appendChild(makePerk(
    p, cat && sel[cat] && sel[cat].hash===p.hash, cat?pick(cat,p,redraw):null)));
  return col;
}
function renderTraits(){
  const box=document.getElementById('traits');box.innerHTML='';
  // 列顺序由索引给出（plugs.cols 按游戏内 socket 顺序排好），老数据才走下面的兜底
  const cols=W.plugs.cols;
  if(cols&&cols.length){
    cols.forEach((c,ci)=>{
      const key=c.t.indexOf('特性')===0?('trait#'+ci):null;
      const el=document.createElement('div');el.className='tcol';
      el.innerHTML=`<div class='clabel'>${c.t}</div>`;
      c.items.forEach(p=>{
        const selKey=key||('col#'+ci);
        el.appendChild(makePerk(p, sel[selKey]&&sel[selKey].hash===p.hash, ()=>{
          if(sel[selKey]&&sel[selKey].hash===p.hash)delete sel[selKey];else sel[selKey]=p;
          renderTraits();renderStats();}));
      });
      box.appendChild(el);
    });
    return;
  }
  [['barrels','枪管 / 发射','barrels'],['magazines','弹匣 / 电池','magazines']]
    .forEach(([cat,label,key])=>{const c=column(label,W.plugs[cat]||[],key,renderTraits);
      if(c)box.appendChild(c);});
  W.plugs.trait_cols.forEach((list,ci)=>{
    if(!list.length)return;
    const key='trait#'+ci;
    const col=document.createElement('div');col.className='tcol';
    col.innerHTML=`<div class='clabel'>特性 ${ci+1}</div>`;
    list.forEach(p=>col.appendChild(makePerk(p, sel[key]&&sel[key].hash===p.hash, ()=>{
      if(sel[key]&&sel[key].hash===p.hash)delete sel[key];else sel[key]=p;
      renderTraits();renderStats();})));
    box.appendChild(col);
  });
  [['origins','起源特性','origins'],['stocks','枪托','stocks'],['fixed','固定配件',null],
   ['catalysts','催化',null]]
    .forEach(([cat,label,key])=>{const c=column(label,W.plugs[cat]||[],key,renderTraits);
      if(c)box.appendChild(c);});
}
function renderMaster(){
  const box=document.getElementById('master');box.innerHTML='';
  const list=W.plugs.masterworks||[];
  if(!list.length){box.innerHTML="<span class='empty'>该武器没有大师杰作词条</span>";return;}
  list.forEach(p=>box.appendChild(makePerk(
    p, sel['masterworks']&&sel['masterworks'].hash===p.hash, pick('masterworks',p,renderMaster))));
}
function renderMods(){
  const box=document.getElementById('mods');box.innerHTML='';
  const list=W.plugs.mods||[];
  if(!list.length){box.innerHTML="<span class='empty'>该武器没有可选模组</span>";return;}
  list.forEach(p=>{
    const c=document.createElement('div');
    const on=sel['mods']&&sel['mods'].hash===p.hash;
    c.className='chip'+(on?' on':'');
    const st=Object.entries(p.stats||{}).map(([n,v])=>
      `<span class='st ${v>0?'':'dn'}'>${n}${v>0?'+':''}${v}</span>`).join(' ');
    c.innerHTML=`<img src='${p.i}'>${p.n} ${st}`;
    c.onclick=pick('mods',p,renderMods);
    bindTip(c,p);
    box.appendChild(c);
  });
}
// 异域催化：数值加成 + 真实效果
//   stats：催化插件自带的属性增减（Manifest，很多金枪这里是空的）
//   zh：Starside 中文催化说明（含「+20 操控性、+1 弹匣容量（5 → 6）」这类精确写法）
//   fx：构建时从 perks.json 解析出的 sandbox perk 中文说明
//   ci：Clarity 社区英文原文（Manifest 没数值的催化只有它有，如「Grants 30 Reload」）
function renderCata(){
  const list=W.plugs.catalysts||[];
  const sec=document.getElementById('catsec');
  if(!list.length){sec.style.display='none';return;}
  sec.style.display='';
  const box=document.getElementById('catbox');box.innerHTML='';
  const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
  list.forEach(p=>{
    const st=Object.entries(p.stats||{}).map(([n,v])=>`<span class='st'>${n} ${v>0?'+':''}${v}</span>`).join(' ');
    const zh=(p.zh||'').split('\n').filter(Boolean).map(x=>`<div class='catfx'>${esc(x)}</div>`).join('');
    const fx=(p.fx||[]).map(f=>`<div class='catfx'><b>${esc(f.n)}</b>　${esc(f.d)}</div>`).join('');
    const ci=(p.ci||'').split('\n').filter(Boolean).map(x=>`<div class='catci'>${esc(x)}</div>`).join('');
    const d=(p.d||'').split('。').map(x=>x.trim()).filter(Boolean);
    const unlock=(!zh&&!fx&&d.length)?d[d.length-1]:'';
    const el=document.createElement('div');el.className='catcard';
    el.innerHTML=`<img src='${p.i||''}'><div class='catbody'><div class='cattop'>
      <b>${esc(p.n)}</b> ${st}</div>${zh}${fx}${ci}${unlock?`<div class='catunlock'>解锁：${unlock}。</div>`:''}</div>`;
    box.appendChild(el);
  });
}
renderTraits();renderStats();renderMaster();renderCata();renderMods();
</script></body></html>"""


def stat_rows(stats: dict, extra: list[str]) -> str:
    def row(label, value, cls=""):
        return f"<div class='row {cls}'><span>{label}</span><b>{value}</b></div>"
    html = row("击杀", f"{stats['kills']:,.0f}")
    html += row("死亡", f"{stats['deaths']:,.0f}")
    html += row("K/D", f"{stats['kd']:.2f}", "hl")
    html += row("场次", f"{stats['activitiesEntered']:,.0f}")
    if "activitiesWon" in extra and stats.get("activitiesEntered"):
        html += row("胜率", f"{stats['activitiesWon'] / stats['activitiesEntered'] * 100:.1f}%", "hl")
    if "precisionKills" in extra:
        html += row("精准击杀", f"{stats.get('precisionKills', 0):,.0f}")
    if "assists" in extra:
        html += row("协助", f"{stats.get('assists', 0):,.0f}")
    return html


def match_result(m: dict) -> tuple[str, str]:
    """对局结果 → (标签HTML, 格子样式类)。PvE 用通关/未完成，竞技模式才用胜/负"""
    if m.get("competitive"):
        if not m["completed"]:
            return "<span class='tagd'>未完成</span>", "d"
        return ("<span class='tagw'>胜利</span>", "w") if m["win"] else ("<span class='tagl'>失败</span>", "l")
    return ("<span class='tagw'>通关</span>", "w") if m["completed"] else ("<span class='tagd'>未通关</span>", "d")


def esc(s: str) -> str:
    """属性值转义（活动名里偶有引号会截断 HTML 属性）"""
    return str(s).replace("&", "&amp;").replace("'", "&#39;").replace('"', "&quot;").replace("<", "&lt;")


def render_matches(matches: list[dict], limit: int = 15) -> str:
    order = list(reversed(matches))
    grid = "".join(
        f"<span class='cell {match_result(m)[1]}' title='{esc(m['name'])} {esc(m.get('period_cn') or m['period'])}'></span>"
        for m in order
    )
    rows = ""
    for m in matches[:limit]:
        dur = f"{m['duration'] // 60}分{m['duration'] % 60}秒"
        tag, _ = match_result(m)
        mtag = f"<span class='mtag'>{m['mode_name']}</span>" if m.get("mode_name") else ""
        rows += (
            f"<a class='mrow' href='/pgcr?i={m['instance']}'><img src='{m['pgcr']}'><div class='mi'>"
            f"<b>{m['name']}</b> {tag}{mtag} <span class='dim'>{m.get('period_cn') or m['period']} · {dur} · 点击查看对局详情</span></div>"
            f"<div class='ms'><span>击杀 <b>{m['kills']}</b></span><span>死亡 <b>{m['deaths']}</b></span>"
            f"<span>K/D <b>{m['kd']:.2f}</b></span><span>协助 <b>{m['assists']}</b></span>"
            + (f"<span>效率 <b>{m['eff']:.2f}</b></span>" if m.get("eff") else "")
            + (f"<span>队伍分 <b>{m['team_score']}</b></span>" if m.get("team_score") else "")
            + "</div></a>"
        )
    return f"<h2>最近对局（点击查看全场数据）</h2><div class='gridwrap'><div class='gridline'>{grid}</div></div>{rows}"


def render_breakdown(rep: dict) -> str:
    """按具体玩法（试炼/铁旗/打击/地牢…）细分战绩"""
    if len(rep.get("breakdown") or []) < 2:
        return ""
    comp = rep.get("competitive")
    head = ("<div class='bhead'><span>模式</span><span>场次</span>"
            + ("<span>胜率</span>" if comp else "")
            + "<span>K/D</span><span>KDA</span><span>场均击杀</span></div>")
    rows = ""
    for b in rep["breakdown"][:12]:
        if b["n"] < 2:
            continue
        wr = f"<span class='{'wrg' if b['win_rate'] >= 50 else 'wrb'}'>{b['win_rate']:.0f}%</span>" if comp else ""
        rows += (f"<div class='brow'><span class='bname'>{b['name']}</span><span>{b['n']}</span>{wr}"
                 f"<span>{b['kd']:.2f}</span><span>{b['kda']:.2f}</span>"
                 f"<span>{b['avg_kills']:.1f}</span></div>")
    if not rows:
        return ""
    style = ("<style>"
             f".bhead,.brow{{display:grid;grid-template-columns:1fr 56px {'64px ' if comp else ''}64px 64px 76px;"
             f"gap:6px;align-items:center;font-size:13px}}"
             f".bhead{{color:#9aa0a6;font-size:12px;padding:0 8px 6px;border-bottom:1px solid #2a2e33}}"
             f".brow{{background:#16181b;border:1px solid #2a2e33;border-radius:7px;padding:7px 8px;margin:5px 0}}"
             f".brow span{{text-align:right}}"
             f".bname{{text-align:left!important;font-weight:bold}}"
             f".wrg{{color:#35c66b}}.wrb{{color:#ff8d85}}"
             f"</style>")
    return f"<h2>模式细分</h2>{head}{rows}{style}"


def render_match_card(rep: dict, title: str, mode: str, lifetime: dict | None = None,
                      lifetime_extra: tuple = ()) -> str:
    comp = rep.get("competitive")
    rows = [f"<div class='row'><span>对局场次</span><b>{rep['total']}</b></div>"]
    if comp:
        rows.append(f"<div class='row hl'><span>胜率</span><b>{rep['win_rate']:.1f}%</b></div>")
        rows.append(f"<div class='row'><span>胜 / 负</span><b>{rep['wins']} / {rep['losses']}</b></div>")
        if rep.get("streak"):
            lab = "当前连胜" if rep["streak_win"] else "当前连败"
            rows.append(f"<div class='row'><span>{lab}</span><b>{rep['streak']} 场</b></div>")
    else:
        rows.append(f"<div class='row hl'><span>通关率</span><b>{rep['clear_rate']:.1f}%</b></div>")
        rows.append(f"<div class='row'><span>通关场次</span><b>{rep['completed']} / {rep['rate_base']}</b></div>")
    rows += [
        f"<div class='row hl'><span>K/D</span><b>{rep['kd']:.2f}</b></div>",
        f"<div class='row'><span>KDA</span><b>{rep['kda']:.2f}</b></div>",
        f"<div class='row'><span>总击杀 / 死亡 / 协助</span><b>{rep['kills']:,} / {rep['deaths']:,} / {rep['assists']:,}</b></div>",
        f"<div class='row'><span>场均击杀</span><b>{rep['avg_kills']:.1f}</b></div>",
    ]
    if rep.get("eff"):
        rows.append(f"<div class='row'><span>平均效率</span><b>{rep['eff']:.2f}</b></div>")
    rows.append(f"<div class='row'><span>对局时长合计</span><b>{rep['hours']:.1f} 小时</b></div>")

    life = ""
    if lifetime:
        life = ("<h2>生涯统计（Bungie 官方）</h2>" + stat_rows(lifetime, list(lifetime_extra)))
    body = (f"<h1>{rep['display']}</h1>"
            f"<div class='sub'>{title} · 数据来自 Bungie.net</div>"
            f"{life}"
            f"<h2>近期战绩（跨角色，每角色最近 100 场）</h2>{''.join(rows)}"
            f"{render_breakdown(rep)}{render_matches(rep['matches'])}")
    return CARD_CSS.replace("__BODY__", body)


CARD_CSS = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><base href="http://127.0.0.1:8900/">
<style>
body{margin:0;font-family:"Microsoft YaHei",sans-serif;background:#0f1113;color:#e8e6e3;
     width:900px;box-sizing:border-box;padding:12px}
.card{background:none;border:none;border-radius:0;box-shadow:none;padding:0}
h1{margin:0;font-size:34px;font-weight:700;color:#fff;line-height:1.22;padding:20px 24px;border:1px solid #2a2e33;border-radius:12px;background:linear-gradient(115deg,#1b1e22 0%,#141619 52%,#0f1113 100%);text-shadow:0 2px 12px rgba(0,0,0,.6)}
.title{color:#e8e6e3;font-size:20px}
.sub{color:#9aa0a6;font-size:13.5px;margin:8px 0 14px;padding:0 2px}
h2{font-size:13.5px;color:#c5cacd;font-weight:700;letter-spacing:1.5px;border-left:3px solid #35c66b;padding-left:9px;margin:16px 0 8px}
.row{display:flex;justify-content:space-between;padding:8px 12px;border-radius:8px;font-size:14px;margin:3px 0;background:#1b1e22}

.row b{color:#d4b26a}
.row.hl b{color:#35c66b}
.char{display:flex;align-items:center;gap:12px;background-size:cover;border-radius:12px;border:1px solid #2a2e33;padding:10px 14px;margin:8px 0;background-color:#1b1e22}
.char img{height:44px;border-radius:4px}
.ci{display:flex;flex-direction:column;line-height:1.5;font-size:14px;background:rgba(15,17,19,.72);
    padding:4px 10px;border-radius:6px}
.ci .dim{color:#9aa0a6;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
section{background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:10px 12px 12px}
.empty{color:#9aa0a6}
.gridwrap{background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:10px;margin-bottom:10px}
.gridline{display:grid;grid-template-columns:repeat(auto-fill,19px);gap:3px}
.cell{width:19px;height:19px;border-radius:3px;background:#2b2e33;transition:transform .08s}
.cell:hover{transform:scale(1.18)}
.cell.w{background:#35c66b}.cell.l{background:#d9483f}.cell.d{background:#3a3e44}
.mrow{display:flex;align-items:center;gap:12px;background:#16181b;border:1px solid #2a2e33;
      border-radius:8px;padding:8px 12px;margin:6px 0;text-decoration:none;color:#e8e6e3}
.mrow:hover{border-color:#35c66b}
.rrow{display:flex;align-items:center;gap:12px;background:#16181b;border:1px solid #2a2e33;
      border-radius:8px;padding:8px 12px;margin:6px 0;font-size:14px;text-decoration:none;color:#e8e6e3}
.rrow:hover{border-color:#35c66b}
.rrow img{width:86px;height:44px;object-fit:cover;border-radius:4px}
.rrow .clears{color:#35c66b}
.rrow .fw{color:#d4b26a}
.rrow .lm{color:#4b8fd4}
.rrow .dim{color:#9aa0a6;font-size:12px;margin-left:auto}
.months{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.ml{color:#9aa0a6;font-size:13px;text-decoration:none;background:#16181b;border:1px solid #2a2e33;
    border-radius:6px;padding:3px 10px}
.ml.on{color:#0b1a10;background:#35c66b;border-color:#35c66b;font-weight:700}
.mrow>img{width:86px;height:48px;object-fit:cover;border-radius:4px;flex-shrink:0}
.mi{flex:1;font-size:13px;line-height:1.6}
.ms{display:flex;gap:14px;font-size:13px;color:#9aa0a6;flex-shrink:0}
.ms b{color:#e8e6e3}
.tagw,.tagl,.tagd{font-size:12px;border-radius:4px;padding:1px 6px}
.tagw{color:#35c66b;background:rgba(53,198,107,.15)}
.tagl{color:#ff8d85;background:rgba(217,72,63,.15)}
.tagd{color:#999;background:rgba(154,160,166,.15)}
.mtag{display:inline-block;background:#22262b;color:#9aa0a6;border-radius:6px;font-size:11px;padding:1px 7px;margin-left:4px;vertical-align:1px}
.mtag.mvp{background:rgba(212,178,106,.16);color:#d4b26a}
.mrow.hist{padding:10px 12px}
.hist .mi{flex:1.4}
.weps{margin-top:6px}
.weps summary{cursor:pointer;color:#4b8fd4;font-size:12px}
.wrow{display:flex;align-items:center;gap:8px;background:#1b1e22;border-radius:8px;
      padding:4px 8px;margin:4px 0;font-size:12px}
.wrow img{width:26px;height:26px;object-fit:contain}
.wrow b{margin-left:auto;color:#d4b26a}
.mvpname{color:#d4b26a;font-size:18px;font-weight:bold}
.pcard{background:#16181b;border:1px solid #2a2e33;border-radius:10px;padding:10px;margin:8px 0}
.pban{display:flex;align-items:center;gap:12px;background:linear-gradient(90deg,#1b1e22,#16181b);
      border:1px solid #2a2e33;border-radius:8px;padding:8px 14px}
.pban img{width:72px;height:38px;object-fit:contain;border-radius:4px;flex-shrink:0}
.pbanname{line-height:1.6}
.pbanname b{font-size:15px}
.pbandim{display:block;color:#9aa0a6;font-size:12px}
.pstats{display:flex;gap:8px;margin-top:8px}
.pstats div{flex:1;background:#1b1e22;border-radius:8px;text-align:center;padding:6px 2px}
.pstats i{display:block;font-style:normal;color:#9aa0a6;font-size:11px}
.pstats b{color:#d4b26a;font-size:14px}
.hgrid{display:flex;gap:3px;background:#16181b;border:1px solid #2a2e33;border-radius:12px;padding:10px}
.hcol{display:flex;flex-direction:column;gap:3px}
.hcell{width:14px;height:14px;border-radius:3px;display:block}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips .on{border-color:#d4b26a;background:#2b2417}
.chips .dim2{opacity:.45}
.chips img{width:26px;height:26px;object-fit:contain;border-radius:4px}
.chips span{font-size:13px}
.wcard img{width:52px;height:52px;object-fit:contain;flex-shrink:0}
.wcard b{font-size:16px}
.wcard p{margin:4px 0 0;color:#9aa0a6;font-size:13px}
.tag{color:#4b8fd4;font-size:12px}
/* 角落水印：bot 名 + 作者（与 bot_cards.CSS 同一条，改一处要两处一起改） */
body,body.pgw{position:relative;padding-bottom:30px}
body::after{content:"雷尼克斯联合-1 · by Wj";position:absolute;right:14px;bottom:8px;
            font-size:11.5px;letter-spacing:1.2px;color:rgba(212,178,106,.5)}
</style></head><body><div class="card">
__BODY__
</div></body></html>"""


def render_card(data: dict, mode: str = "all") -> str:
    """总览页：生涯概况 + 三角色 + 三模式速览（PVP/PVE/智谋各有独立标签页看细分）"""
    mode_title = {"all": "生涯总览", "pvp": "PVP 战绩", "pve": "PVE 战绩", "gambit": "智谋战绩"}.get(mode, mode)
    sections = []
    if mode == "all":
        sections.append(("<h2>生涯概况</h2>" +
                         f"<div class='row'><span>总游戏时长</span><b>{d2.fmt_hours(data['total_playtime'])}</b></div>" +
                         f"<div class='row hl'><span>最高光能</span><b>{data['max_light']}</b></div>"))
    elif mode == "pvp":
        sections.append("<h2>PVP 生涯</h2>" + stat_rows(data["pvp"], ["activitiesWon", "assists", "precisionKills"]))
    elif mode == "pve":
        sections.append("<h2>PVE 生涯</h2>" + stat_rows(data["pve"], ["precisionKills"]))
    chars_html = "".join(
        f"<div class='char' style=\"background-image:url('{c['emblem_bg']}')\">"
        f"<img src='{c['emblem']}'><div class='ci'>"
        f"<b>{c['class']} · {c['race']}</b>"
        f"<span>光能 {c['light']} · {d2.fmt_hours(c['playtime_min'])}</span>"
        f"<span class='dim'>上线 {c['last_played']}</span></div></div>"
        for c in data["chars"]
    )
    extra_all = ""
    if mode == "all":
        extra_all = (f"<div class='grid'><section><h2>PVP 生涯</h2>{stat_rows(data['pvp'], ['activitiesWon'])}</section>"
                     f"<section><h2>PVE 生涯</h2>{stat_rows(data['pve'], [])}</section>"
                     f"<section><h2>智谋 生涯</h2>{stat_rows(data['gambit'], ['activitiesWon'])}</section></div>"
                     f"<p class='dim' style='margin-top:8px'>上方为 Bungie 官方生涯统计；"
                     f"PVP / PVE / 智谋 标签页是按对局历史聚合的近期战绩（含模式细分与胜率）</p>")
    body = (f"<h1>{data['display']}</h1>"
            f"<div class='sub'>{mode_title} · 数据来自 Bungie.net</div>"
            f"{''.join(sections)}"
            f"<h2>角色</h2>{chars_html}{extra_all}")
    return CARD_CSS.replace("__BODY__", body)


if __name__ == "__main__":
    import threading

    def _tls():
        cert, key = bungie_auth.cert_files()
        if cert and key:
            uvicorn.run(app, host="127.0.0.1", port=bungie_auth.TLS_PORT,
                        ssl_certfile=cert, ssl_keyfile=key, log_level="warning")

    threading.Thread(target=_tls, daemon=True).start()
    bot_runtime.start()
    uvicorn.run(app, host="127.0.0.1", port=8900, log_level="warning")
