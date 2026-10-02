"""给 QQ 官方机器人配「指令面板 + 自定义菜单」（开放平台 → 高级设置 → 菜单与指令）

开放平台网页上只写了「通过 API 配置」，真实接口（官方文档 autogen/api/）：
  POST   /v2/panels                     建面板 {scope, target_type, panel}→{panel_id}
  GET    /v2/panels?scope=group&limit=  列面板（按 remark 前缀认领自己那份）
  PUT    /v2/panels/{id}                改面板 {panel:{items,remark}}
  DELETE /v2/panels/{id}                删面板
  GET/PUT /v2/menu                      单聊自定义菜单
统一域名 https://api.bot.qq.com，鉴权头 Authorization: QQBot {access_token}。
限额（实测 + 文档）：**每个场景只保留一个全局面板**——再建一个会把前一个顶掉；
一个面板最多 20 个元素，元素名 ≤14 字符、描述 ≤30 字符，菜单按钮名 ≤10 字符
（**一个汉字算 2 个字符**，脚本按此校验）。所以面板只放 20 条最常用的，
其余走 /帮助 与单聊菜单。

指令面板 = 用户在聊天框打 "/" 时弹出的清单（小日向那一版就是这个）；
自定义菜单 = 单聊底部按钮（可选，--no-menu 跳过）。

用法：
  .venv/Scripts/python.exe qq_official_panel.py            # 只打印将要写入的内容（dry-run）
  .venv/Scripts/python.exe qq_official_panel.py --list     # 只看线上现在是什么
  .venv/Scripts/python.exe qq_official_panel.py --apply    # 真写（先备份到 qq_official_menu_backup.json）
  .venv/Scripts/python.exe qq_official_panel.py --check    # 只按官方限额自检，不连网

改指令清单就改 PANEL_ITEMS / MENU_ITEMS（都是 (名字, 描述) 或菜单项字典）。
凭证复用它：qq_official_creds.json（AppID/AppSecret，已在 .gitignore 里）。
"""

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE_DIR = pathlib.Path(__file__).resolve().parent
CREDS_FILE = BASE_DIR / "qq_official_creds.json"
BACKUP_FILE = BASE_DIR / "qq_official_menu_backup.json"

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.bot.qq.com"  # 文档「统一请求地址」；旧域名 api.sgroup.qq.com 仍可用
FALLBACK_BASE = "https://api.sgroup.qq.com"

REMARK_PREFIX = "d2query"  # 认领用：线上按此前缀找自己建过的面板（不展示给用户）
REMARK = "d2query-main"
PANEL_SCOPES = ("group", "c2c")  # 群聊 + 单聊各一份，内容相同

# ---------- 指令面板（20 条上限；名字 ≤14 字符、描述 ≤30 字符，汉字算 2）----------
# 顺序就是用户在面板里看到的顺序。名字只写指令词（平台会去掉开头的 "/"，
# 客户端按 type=command 自动补回，点一下填进输入框）。
PANEL_ITEMS = [
    ("/帮助", "全部指令一览"),
    ("/绑定", "绑定玩家名#编号"),
    ("/武器查询", "查武器详情与可刷perk"),
    ("/perk查询", "查perk数值与可出武器"),
    ("/武器筛选", "按条件筛选武器"),
    ("/护甲查询", "查异域护甲与可刷perk"),
    ("/护甲套装", "护甲套装2件/4件效果"),
    ("/掉落", "各副本掉落表图片"),
    ("/每日光尘", "今日光尘商店在售商品"),
    ("/轮换", "本周轮换与扭曲星球"),
    ("/玩家", "生涯总览与三角色"),
    ("/生涯", "生涯/赛季数据总览"),
    ("/raid", "突袭战绩与通关记录"),
    ("/地牢", "地牢战绩与通关记录"),
    ("/pvp", "近期PvP战绩"),
    ("/pve", "近期PvE战绩"),
    ("/历史", "最近对局明细"),
    ("/常用武器", "PvP生涯武器击杀统计"),
    ("/pve生涯武器", "PvE武器击杀统计"),
    ("/宗师", "宗师/征服战绩"),
]
# 放不下的 8 条（面板上限 20）：/智谋 /热力图 /称号 /锻造 /我的 /解绑 /队伍 /老九
# —— 群里都能靠 /帮助 查到，单聊里另有下面的自定义菜单兜底。

# ---------- 单聊自定义菜单（按钮名 ≤10 字符，子菜单 ≤5 个、名 ≤14 字符）----------
MENU_ITEMS = [
    {"type": "send_message", "name": "武器查询", "send_message": "/武器查询"},
    {"type": "send_message", "name": "perk查询", "send_message": "/perk查询"},
    {"type": "send_message", "name": "护甲查询", "send_message": "/护甲查询"},
    {
        "type": "menu",
        "name": "战绩",
        "sub_menu_items": [
            {"type": "send_message", "name": "突袭", "send_message": "/raid"},
            {"type": "send_message", "name": "地牢", "send_message": "/地牢"},
            {"type": "send_message", "name": "PvP", "send_message": "/pvp"},
            {"type": "send_message", "name": "PvE", "send_message": "/pve"},
            {"type": "send_message", "name": "智谋", "send_message": "/智谋"},
        ],
    },
    {
        "type": "menu",
        "name": "资料",
        "sub_menu_items": [
            {"type": "send_message", "name": "掉落表", "send_message": "/掉落"},
            {"type": "send_message", "name": "每日光尘", "send_message": "/每日光尘"},
            {"type": "send_message", "name": "武器筛选", "send_message": "/武器筛选"},
            {"type": "send_message", "name": "护甲套装", "send_message": "/护甲套装"},
            {"type": "send_message", "name": "本周轮换", "send_message": "/轮换"},
        ],
    },
    {
        "type": "menu",
        "name": "记录",
        "sub_menu_items": [
            {"type": "send_message", "name": "常用武器", "send_message": "/常用武器"},
            {"type": "send_message", "name": "PvE武器", "send_message": "/pve生涯武器"},
            {"type": "send_message", "name": "称号收集", "send_message": "/称号"},
            {"type": "send_message", "name": "锻造图案", "send_message": "/锻造"},
            {"type": "send_message", "name": "热力图", "send_message": "/热力图"},
        ],
    },
    {
        "type": "menu",
        "name": "账号",
        "sub_menu_items": [
            {"type": "send_message", "name": "绑定账号", "send_message": "/绑定 "},
            {"type": "send_message", "name": "我的账号", "send_message": "/我的"},
            {"type": "send_message", "name": "解绑账号", "send_message": "/解绑"},
        ],
    },
]


# ---------- 限额校验（官方口径：一个汉字算 2 个字符）----------
def clen(s: str) -> int:
    return sum(2 if ord(ch) > 0x7F else 1 for ch in s)


def check_limits() -> list[str]:
    errs: list[str] = []
    if len(PANEL_ITEMS) > 20:
        errs.append(f"面板：{len(PANEL_ITEMS)} 个元素，超过单面板上限 20")
    for name, desc in PANEL_ITEMS:
        if not name.startswith("/"):
            errs.append(f"面板：{name} 少了 / 前缀")
        if clen(name) > 14:
            errs.append(f"面板：{name} 名字 {clen(name)} 字符 >14")
        if clen(desc) > 30:
            errs.append(f"面板：{name} 描述 {clen(desc)} 字符 >30")
    if len(MENU_ITEMS) > 10:
        errs.append(f"菜单：{len(MENU_ITEMS)} 项 >10")
    for it in MENU_ITEMS:
        if clen(str(it.get("name", ""))) > 10:
            errs.append(f"菜单：{it.get('name')} 名字超 10 字符")
        subs = it.get("sub_menu_items") or []
        if len(subs) > 5:
            errs.append(f"菜单：{it.get('name')} 子菜单 {len(subs)} 个 >5")
        for sub in subs:
            if clen(str(sub.get("name", ""))) > 14:
                errs.append(f"菜单：子项 {sub.get('name')} 名字超 14 字符")
    return errs


def panel_body() -> dict:
    return {
        "remark": REMARK,
        "items": [
            {"name": name, "desc": desc, "type": "command", "only_admin": False}
            for name, desc in PANEL_ITEMS
        ],
    }


# ---------- 开放平台调用 ----------
def api_request(method: str, path: str, token: str, appid: str, body: dict | None = None,
                base: str = API_BASE) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"QQBot {token}")
    req.add_header("X-Union-Appid", appid)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            info = json.loads(raw) or {}
        except json.JSONDecodeError:
            info = {}
        code = info.get("err_code") or info.get("code") or e.code
        msg = info.get("message") or info.get("msg") or raw[:200]
        raise RuntimeError(f"HTTP {e.code} err_code={code} {msg}") from None
    out = json.loads(raw) if raw.strip() else {}
    if isinstance(out, dict) and out.get("err_code"):  # 200 里也可能带业务错误
        raise RuntimeError(f"err_code={out['err_code']} {out.get('message') or ''}")
    return out


def get_token(appid: str, secret: str) -> str:
    body = json.dumps({"appId": appid, "clientSecret": secret}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(f"取 access_token 失败：{data}")
    return token


def load_creds() -> tuple[str, str]:
    if not CREDS_FILE.exists():
        sys.exit(f"缺少 {CREDS_FILE.name}（开放平台 → 你的机器人 → 开发设置）")
    data = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    appid, secret = str(data.get("appid") or ""), str(data.get("secret") or "")
    if not appid or not secret or appid.startswith("填"):
        sys.exit(f"{CREDS_FILE.name} 里的 appid / secret 还是空的占位文本")
    return appid, secret


# ---------- 主流程 ----------
def show_panels(api, menu: bool) -> None:
    for scope in PANEL_SCOPES:
        recs = (api("GET", f"/v2/panels?scope={scope}&limit=50").get("records") or [])
        print(f"[面板 {scope}] 线上 {len(recs)} 个")
        for r in recs:
            panel = r.get("panel") or {}
            items = panel.get("items") or []
            print(f"  · {r.get('panel_id')}  remark={panel.get('remark')!r} "
                  f"target={r.get('target_type')} 元素 {len(items)} 个  v{r.get('version')}")
            for it in items:
                print(f"      {it.get('name')}  —— {it.get('desc')}")
    if menu:
        got = api("GET", "/v2/menu")
        items = ((got.get("menu") or {}).get("items")) or []
        print(f"[单聊菜单] v{got.get('version')}，{len(items)} 个按钮")
        for it in items:
            subs = " / ".join(str(s.get("name")) for s in it.get("sub_menu_items") or [])
            print(f"  · {it.get('name')} → {it.get('send_message') or subs}")


def sync(api, dry: bool, with_menu: bool) -> None:
    backup: dict = {"panels": [], "menu": None}
    for scope in PANEL_SCOPES:
        recs = (api("GET", f"/v2/panels?scope={scope}&limit=50").get("records") or [])
        backup["panels"].append({"scope": scope, "records": recs})
        mine = [r for r in recs
                if str((r.get("panel") or {}).get("remark") or "").startswith(REMARK_PREFIX)]
        body = {"panel": panel_body()}
        # 只复用 target_type=all 的那份：PUT 既改不了生效范围、也改不了关联对象
        # （全局面板报 40030021），所以 specific 的残留只能删掉、靠 POST 重建。
        reuse = next((r for r in mine if r.get("target_type") == "all"), None)
        stale = [r for r in mine if r is not reuse]
        if reuse is None:
            print(f"[新建] {scope} 面板（全局面板只能靠 POST 建）：{len(PANEL_ITEMS)} 个元素")
            if not dry:
                res = api("POST", "/v2/panels",
                          {"scope": scope, "target_type": "all", "panel": panel_body()})
                print(f"       → panel_id={res.get('panel_id')}")
        else:
            print(f"[更新] {scope} 面板 {reuse['panel_id']}（target={reuse.get('target_type')}）："
                  f"{len(PANEL_ITEMS)} 个元素" + (f"；另有 {len(stale)} 个残留待清理" if stale else ""))
            if not dry:
                res = api("PUT", f"/v2/panels/{reuse['panel_id']}", body)
                print(f"       → version={res.get('version')}")
        if not dry:
            for r in stale:
                items_n = len((r.get("panel") or {}).get("items") or [])
                api("DELETE", f"/v2/panels/{r['panel_id']}")
                print(f"       → 已删除 {r.get('target_type')} 面板 {r['panel_id']}（{items_n} 项，不生效）")
        for name, desc in PANEL_ITEMS:
            print(f"       {name}  —— {desc}")

    if with_menu:
        old = api("GET", "/v2/menu")
        backup["menu"] = old
        print(f"[单聊菜单] 线上现有 {len(((old.get('menu') or {}).get('items')) or [])} 个按钮"
              f"（v{old.get('version')}）→ 写入 {len(MENU_ITEMS)} 个"
              + ("（dry-run 不写）" if dry else ""))
        for it in MENU_ITEMS:
            subs = " / ".join(str(s["name"]) for s in it.get("sub_menu_items") or [])
            print(f"       {it['name']} → {it.get('send_message') or subs}")
        if not dry:
            res = api("PUT", "/v2/menu", {"menu": {"items": MENU_ITEMS}})
            print(f"       → version={res.get('version')}")

    if not dry:
        BACKUP_FILE.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[备份] 改动前的线上配置存到 {BACKUP_FILE.name}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="配置 QQ 官方机器人的指令面板与自定义菜单")
    ap.add_argument("--apply", action="store_true", help="真写入（默认只打印计划）")
    ap.add_argument("--list", action="store_true", help="只列出线上现有的面板与菜单")
    ap.add_argument("--check", action="store_true", help="只做限额自检，不连网")
    ap.add_argument("--no-menu", action="store_true", help="不碰单聊自定义菜单")
    ap.add_argument("--base", default=API_BASE, help=f"覆盖接口域名（默认 {API_BASE}）")
    args = ap.parse_args()

    errs = check_limits()
    for e in errs:
        print(f"[超限] {e}")
    if args.check:
        sys.exit(1 if errs else 0)
    if not errs:
        print(f"[自检] 面板 {len(PANEL_ITEMS)} 项、单聊菜单 {len(MENU_ITEMS)} 项，"
              f"名字/描述长度都在限额内")

    appid, secret = load_creds()
    token = get_token(appid, secret)
    base = args.base

    def api(method: str, path: str, body: dict | None = None) -> dict:
        return api_request(method, path, token, appid, body, base)

    if base != FALLBACK_BASE:  # 新域名连不上就回退老域名（两边接口一致）
        try:
            api("GET", "/v2/panels?scope=c2c&limit=1")
            print(f"[接口] {base}")
        except urllib.error.URLError as exc:
            print(f"[回退] {base} 连不上（{exc.reason}），改用 {FALLBACK_BASE}")
            base = FALLBACK_BASE
            api = lambda m, p, b=None: api_request(m, p, token, appid, b, base)  # noqa: E731
    else:
        print(f"[接口] {base}")

    if args.list:
        show_panels(api, menu=not args.no_menu)
        return
    sync(api, dry=not args.apply, with_menu=not args.no_menu)


if __name__ == "__main__":
    main()
