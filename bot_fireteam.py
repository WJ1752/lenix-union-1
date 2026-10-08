"""「/队伍」数据采集：当前在打什么 + 队内成员数据

实测结论（2026-10-02，详见 CHANGELOG）：
- **在不在活动 = 看 204 组件的 currentActivityHash 是不是「真活动」**：官方在轨道待机
  照样给一个占位 hash（实测 82913930，manifest 里没有名字），拿它去配历史会配上刚打完
  的那一场，卡片就成了「还在打某某副本」。真活动在 manifest / 本地索引里都有名字。
- **名字不是判据，类型才是**（2026-10-03 事故）：巡逻区/社交空间在 manifest 里也有名字，
  但那是自由漫游不是一局对局 —— 必须按 activityModeTypes 剔除（6=Explore 巡逻、40=社交），
  见 `_activity_kind`；它们走「不在对局里」的生涯总览卡，不进对局分支。
- 轨道/组队态：只给队内每人的 **生涯总时长 + 成就点数**（用户口径：不需要多余数据）；
  名单 = Profile 1000 组件 profileTransitoryData.partyMembers（官方实时队伍，隐私会隐藏）。
- 在活动中：本场名单 = 本场对局 PGCR（历史行里找与当前活动开始时间 ±10 分钟对得上的那场，
  匹配局开局后 PGCR 逐步填充）；突袭/地牢=全队，熔炉/智谋按入口里的 values.team 分阵营。
  **但 PGCR 是「本场参与过的所有人」的流水账，不是当前队伍**：3 人位活动实测一局里躺着 6 个
  账号、6 人位自定义突袭躺着 7 条（中途换角色/退队重进、被替补换下的人都在）。所以名单再按
  官方对局内计时过滤成「此刻仍在场」——entry 的 `startSeconds`（进场秒）+ `timePlayedSeconds`
  （在场秒）：`period + start` = 进场时刻、`+ play` = 离场时刻；进行中比 now、刚打完比结束时刻
  （用户 2026-10-07 口径：「我要的是实时数据」）。中途离场的人在卡上只报个数，不进名单。
  本场还没发布（匹配局打一半等）→ 活动名/模式按 **activity hash** 认（不再拿"上一把"顶替），
  名单退回可见队伍（并报出官方给的本场在场人数）。**不做好名顺序以外的任何推测补齐**——
  宁缺毋滥（实测有过凑出 6 人假名单）。
- 每人的模式数据 = 角色级 /Character/{cid}/Stats/?groups=101,103&modes=N 的 allTime
  （账号级 Account Stats 实测忽略 mode 参数）；角色取 dateLastPlayed 最近的。
- **不显示任何单场结算**（击杀/胜负那一套）——用户口径：只看「当前在打什么 + 队内数据」。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import destiny_data as d2

_TZ8 = timezone(timedelta(hours=8))

try:  # 突袭/地牢「完成数/导师」指标索引（build_raid_metrics.py 生成）
    _MI = json.load(open(d2._idx_file("raid_metrics.json"), encoding="utf-8"))
    _RAID_METRICS = _MI.get("raids") or []
    _DUNGEON_METRICS = _MI.get("dungeons") or []
except Exception:  # noqa: BLE001  旧部署没有索引时退化为不带指标砖
    _RAID_METRICS, _DUNGEON_METRICS = [], []

# 模式桶：modeType → (角色级 Stats 的返回键, 展示名, 卡片主色)
BUCKETS = {
    4: ("raid", "突袭", "#35c66b"),
    82: ("dungeon", "地牢", "#9b6bd4"),
    5: ("allPvP", "熔炉 PvP", "#ff8d85"),
    63: ("pvecomp_gambit", "智谋", "#4b8fd4"),
    7: ("allPvE", "PvE", "#c5cacd"),
}
_COMPETITIVE = (5, 63)  # 有阵营（team）之分、只显示同队的模式

# DestinyActivityDefinition.activityTypeHash → 模式桶（实测采样，2026-10-02）
_TYPE_BUCKET = {2043403989: 4, 608898761: 82, 4088006058: 5, 248695599: 63, 3652020199: 7}
_ACT_CACHE: dict[int, int] = {}
_MAX_MEMBERS = 12
# 单个成员数据的等待上限（秒）：Bungie 从国内时快时慢（实测同一个接口 0.2~25 秒），
# 网络卡时宁可这一行留空让人重发一次，也别让整张卡干等
_MEMBER_BUDGET = 12.0
# 边界窗口：进本瞬间 transitory 还没跟上（真活动 hash + players=0）按「进行中」算；
# 一出本 204 立马换占位 hash，所以「刚打完」只能靠历史行+这个宽限窗口认（2026-10-07 用户实测）
_JUST_IN = timedelta(minutes=5)
_JUST_ENDED = timedelta(minutes=10)

# 轨道占位 hash：官方在轨道待机时 204 组件照样给一个 currentActivityHash，
# 实测 2026-10-02 18:11（Wj 在轨道）= 82913930，manifest 里查得到实体但没有名字
# （activityTypeHash 73015004 本身也没有名字）。不能拿它去配历史，否则会配上
# 刚打完的那一场，卡片就变成"还在打某某副本"。
_ORBIT_HASHES = {82913930}
_KIND_CACHE: dict[int, str] = {}     # activity hash → match/patrol/social/none（查一次缓存）


def _parse_dt(s: str) -> datetime | None:
    """Bungie ISO 时间 → aware datetime（无时区就按 UTC）"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cn(dt: datetime) -> str:
    """UTC → UTC+8 文本"""
    return dt.astimezone(_TZ8).strftime("%m-%d %H:%M")


async def _entity(kind: str, hash_: int) -> dict:
    """Manifest 实体（小请求，结果由调用方缓存）"""
    r = await d2.client().get(f"/Platform/Destiny2/Manifest/{kind}/{hash_}/")
    return (json.loads(r.content.decode("utf-8-sig")).get("Response") or {})


async def activity_bucket(ref: int) -> int:
    """活动 hash → 模式桶（activityTypeHash 优先，兜底 modeHashes→modeType，再兜底 PvE）"""
    hit = _ACT_CACHE.get(ref)
    if hit is not None:
        return hit
    bucket = 7
    try:
        d = await _entity("DestinyActivityDefinition", ref)
        th = int(d.get("activityTypeHash") or 0)
        if th in _TYPE_BUCKET:
            bucket = _TYPE_BUCKET[th]
        else:
            for mh in (d.get("activityModeHashes") or []):
                md = await _entity("DestinyActivityModeDefinition", int(mh))
                mt = md.get("modeType")
                if mt in BUCKETS:
                    bucket = mt
                    break
                if mt is not None and 5 in d2.mode_ancestors(mt):
                    bucket = 5
                    break
    except Exception:  # noqa: BLE001  实体查不到就当 PvE，别让整卡挂掉
        pass
    _ACT_CACHE[ref] = bucket
    return bucket


def _bucket(modes: list) -> int:
    """PGCR 的 activityDetails.modes → 模式桶（PvP 子模式如试炼 84 经祖先链归到 5）"""
    mt = d2.mode_of(modes)
    if mt in BUCKETS:
        return mt
    if mt is not None and 5 in d2.mode_ancestors(mt):
        return 5
    return 7


async def _profile_ex(mtype: int, mid: str, components: str) -> dict:
    r = await d2.client().get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                              params={"components": components})
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "Bungie API 错误"))
    return resp["Response"]


async def _pgcr(instance: str, fresh: bool = False) -> dict:
    """本场对局的原始 PGCR（含 entries 的 team / characterId / iconPath 全字段）

    fresh=True 强制绕过缓存实时拉：对局还在进行时 PGCR 会随队友进本逐个补全，
    走 6 小时缓存会把开局那一份「只有一个人」的名单冻住（2026-10-04 用户实测）。"""
    r = await d2.client().get(f"/Platform/Destiny2/Stats/PostGameCarnageReport/{instance}/",
                              **({"no_cache": True} if fresh else {}))
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "PGCR 拉取失败"))
    return resp["Response"]


async def _activity_kind(ref: int) -> str:
    """currentActivityHash → 活动类型：'match' | 'patrol' | 'social' | 'none'

    - none：轨道占位 hash，或 manifest 里没有名字的（不是真活动）；
    - patrol：自由漫游区（modeType 6「Explore」= 涅索斯/欧洲无人区…）——不是一局对局；
    - social：社交空间（modeType 40 = 高塔/农庄/蛛王藏身处）；
    - match：raid/地牢/打击/熔炉/智谋等真正的一局对局。

    实测 2026-10-03 事故：玩家在涅索斯巡逻区，204 的 hash 是巡逻区（manifest 有名字，
    所以旧判据放行），再被 ±10 分钟的宽松匹配配上「上一次巡逻」那条 2 分钟的历史行，
    卡片就成了「不稳定半人马座 已结束 / 对局时长 2 分钟」——而他其实正在打「移民号的坠毁」
    打击（已进行 22 分钟）。自由漫游/社交空间不是对局，必须在进对局分支前拦下。
    查询失败按「was 真活动」兜底（宁可不动，也别编出一场没在打的副本）。"""
    if not ref:
        return "none"
    hit = _KIND_CACHE.get(ref)
    if hit:
        return hit
    kind = "match"
    if ref in _ORBIT_HASHES:
        kind = "none"
    else:
        try:
            d = await _entity("DestinyActivityDefinition", ref)
            if not (((d.get("displayProperties") or {}).get("name")) or "").strip():
                kind = "none"
            else:
                mts = {int(x) for x in (d.get("activityModeTypes") or [])}
                if not mts:
                    for mh in (d.get("activityModeHashes") or []):
                        md = await _entity("DestinyActivityModeDefinition", int(mh))
                        if md.get("modeType") is not None:
                            mts.add(int(md["modeType"]))
                if 6 in mts:
                    kind = "patrol"
                elif 40 in mts:
                    kind = "social"
        except Exception:  # noqa: BLE001
            pass
    _KIND_CACHE[ref] = kind
    return kind


async def _is_real_activity(ref: int) -> bool:
    """currentActivityHash 是不是"人在场"（对局/自由漫游/社交空间算；轨道占位 hash 不算）"""
    return await _activity_kind(ref) != "none"


def _current(prof: dict) -> tuple[str, int, datetime] | None:
    """characterActivities 里取 currentActivityHash≠0 且开始最新的角色 → (cid, hash, 开始时间)"""
    best = None
    for cid, d in (prof.get("characterActivities", {}).get("data") or {}).items():
        h = d.get("currentActivityHash") or 0
        st = _parse_dt(d.get("dateActivityStarted", ""))
        if h and st and (best is None or st > best[2]):
            best = (cid, h, st)
    return best


def _latest_char(chars: dict) -> tuple[str, dict] | None:
    """最近活跃的角色 → (cid, 角色数据)"""
    best, bt, bd = None, None, None
    for cid, c in (chars or {}).items():
        t = _parse_dt(c.get("dateLastPlayed", ""))
        if t and (bt is None or t > bt):
            best, bt, bd = cid, t, c
    return (best, bd) if best else None


def _active_cid(prof: dict) -> str:
    """最近在玩哪个角色：只看 dateActivityStarted 最新的那条（不看 hash）。

    轨道态 204 给的是占位 hash，_current 会把它丢掉——但那条记录照样带着
    「上一场什么时候开的」，拿它定位角色才能去历史里找刚打完的那一场。"""
    best, bt = "", None
    for cid, d in (prof.get("characterActivities", {}).get("data") or {}).items():
        t = _parse_dt(d.get("dateActivityStarted", ""))
        if t and (bt is None or t > bt):
            best, bt = cid, t
    if not best:
        lc = _latest_char((prof.get("characters") or {}).get("data") or {})
        best = lc[0] if lc else ""
    return best


def _entry_info(e: dict) -> dict:
    """PGCR entry → {mid, mtype, name, team, kills, start, play, dur}

    start/play = 官方对局内计时（`startSeconds` 进场秒 / `timePlayedSeconds` 在场秒），
    用来把「本场参与过的人」过滤成「此刻还在场的人」（见 `_pgcr_roster`）；
    老报告或刚开局的 entry 可能没有这两个字段 → None（判定不了就不筛）。"""
    ui = ((e.get("player") or {}).get("destinyUserInfo") or {})
    v = e.get("values") or {}

    def val(k: str):
        return ((v.get(k) or {}).get("basic") or {}).get("value")

    return {
        "mid": str(ui.get("membershipId") or ""),
        "mtype": int(ui.get("membershipType") or 0),
        "name": f"{ui.get('bungieGlobalDisplayName') or ui.get('displayName') or '?'}"
                f"#{d2.fmt_code(ui.get('bungieGlobalDisplayNameCode'))}",
        "team": val("team"),
        "kills": val("kills") or 0,
        "start": val("startSeconds"),
        "play": val("timePlayedSeconds"),
        "dur": val("activityDurationSeconds"),
    }


# 离场判定的宽限：PGCR 是实时记分板，值有分钟级延迟（Bungie 慢时整份报告都可能滞后）。
# 宽限内不动手——宁可多留一个人几分钟，也别把还在场的队友从名单里删掉。
_PRESENT_TOL = timedelta(minutes=3)


def _entry_window(i: dict, period: datetime | None) -> tuple[datetime | None, datetime | None]:
    """PGCR entry → (进场时刻, 离场时刻)；官方没给计时 → (None, None)"""
    if period is None or i.get("start") is None or i.get("play") is None:
        return None, None
    join = period + timedelta(seconds=float(i["start"]))
    return join, join + timedelta(seconds=float(i["play"]))


def _pgcr_roster(infos: list[dict], period: datetime | None,
                 ref: datetime | None) -> tuple[list[dict], int]:
    """PGCR entries → 此刻仍在场的名单（mid 去重）+ 已离场人数。

    ref = 在场判定的参照时刻：进行中=now，刚打完=对局结束时刻（即最后还在队里的人）；
    ref=None（拿不到对局起止）不筛，保持旧行为。
    同一 mid 多条（中途换角色/退队重进）：优先留在场的那条，同样在场按击杀多的。"""
    best: dict[str, tuple] = {}
    for i in infos:
        if not i["mid"]:
            continue
        _join, left = _entry_window(i, period)
        present = ref is None or left is None or (ref - left) <= _PRESENT_TOL
        key = (0 if present else 1, -(i.get("kills") or 0))
        if i["mid"] not in best or key < best[i["mid"]][0]:
            best[i["mid"]] = (key, present, i)
    here = sorted((v[2] for v in best.values() if v[1]),
                  key=lambda i: -(i.get("kills") or 0))
    return here, sum(1 for v in best.values() if not v[1])


def _match_entry(hist: list[dict], start: datetime, now: datetime) -> dict | None:
    """历史里找「当前这一局」：开始时间对得上（±10 分钟）**且不是在当前这局开始前就结束的**。

    传进来的 hist 已过滤成真对局（巡逻/社交空间的会话行不算，见 _activity_kind）。
    `end < start` 这一条是关键：上一把哪怕只比当前这局早一分钟，也不能拿来当本场
    （实测事故就是上一场巡逻 18:16:27 结束、当前打击 18:16:29 开始，只差 2 秒）。
    返回 {entry, live, period, end}；超过 6 小时前的不算「当前」"""
    best = None
    for e in hist:
        p = _parse_dt(e.get("period", ""))
        if not p or not e.get("instance"):
            continue
        if abs((p - start).total_seconds()) > 600:
            continue
        end = p + timedelta(seconds=int(e.get("duration") or 0))
        if end < start:
            continue                      # 本局开始前就结束了 → 是"上一把"
        if best is None or p > best[0]:
            best = (p, e)
    if not best:
        return None
    p, e = best
    end = p + timedelta(seconds=int(e.get("duration") or 0))
    if now > end + timedelta(hours=6):
        return None
    return {"entry": e, "period": p, "end": end, "live": now <= end + timedelta(minutes=2)}


def _raid_tiles(metrics: dict, entries: list) -> list[dict]:
    """Profile 1100 的 metrics → 指标砖（每副本「完成数」+ 有「导师」的补上，顺序同索引）"""
    if not entries:
        return []

    def val(h: int) -> str:
        m = metrics.get(str(h)) or {}
        op = m.get("objectiveProgress") or {}
        if m.get("invisible") or not op:
            return "—"
        return f"{float(op.get('progress') or 0):,.0f}"

    def tile(icon: str, d: dict, label: str) -> dict:
        return {"icon": (d2.BASE + icon) if icon else "",
                "value": val(d["hash"]), "label": label, "source": d.get("source") or ""}

    # 图标口径（用户 2026-10-02）：完成数 = metric 自带的通用「突袭/地牢」图标，
    # 导师 = 对应副本的成就徽章（印章 seal_icon，build_raid_metrics.py 生成）
    out = [tile(e["clear"].get("icon") or e.get("icon") or "", e["clear"],
                f"{e['name']}完成数") for e in entries]
    out += [tile(e.get("seal_icon") or e.get("icon") or "", e["sherpa"],
                 f"{e['name']}导师") for e in entries if e.get("sherpa")]
    return out


async def _activity_label(ref: int) -> str:
    """真活动的名字：本地索引（中文）→ 线上 manifest（英文）→ 空。
    占位 hash（轨道）两条都查不到名字，本来也不会走到这里。"""
    nm = (d2.activity_name(ref) or {}).get("name") or ""
    if nm and not nm.startswith("未知活动"):
        return nm
    try:
        d = await _entity("DestinyActivityDefinition", ref)
        return ((d.get("displayProperties") or {}).get("name") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _empty_row(mid: str, is_self: bool, name_hint: str = "") -> dict:
    """拿不到数据时的空行（只有 mid 尾号）"""
    return {"mid": mid, "name": name_hint or f"…{mid[-6:]}", "class": "", "light": 0,
            "emblem": "", "is_self": is_self, "rows": [], "tiles": [], "team": None}


async def _row_budget(coro, mid: str, is_self: bool, name_hint: str = "") -> dict:
    """给单个成员的抓取套一个等待上限，超时就返回空行"""
    try:
        return await asyncio.wait_for(coro, timeout=_MEMBER_BUDGET)
    except Exception:  # noqa: BLE001  超时/取消
        return _empty_row(mid, is_self, name_hint)


async def _stats_rows(mtype: int, mid: str, cid: str, bucket: int) -> list[tuple[str, str]]:
    """该模式的角色级生涯数据行（只保留官方实际返回的字段，缺的跳过）"""
    stat_key = BUCKETS[bucket][0]
    r = await d2.client().get(
        f"/Platform/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/",
        params={"groups": "101,103", "modes": bucket})
    resp = json.loads(r.content.decode("utf-8-sig"))
    if resp.get("ErrorCode") != 1:
        raise RuntimeError(resp.get("Message", "生涯统计失败"))
    at = ((resp["Response"] or {}).get(stat_key) or {}).get("allTime") or {}

    def num(k: str) -> float:
        return float((at.get(k) or {}).get("basic", {}).get("value", 0) or 0)

    def has(k: str) -> bool:
        return bool(at.get(k))

    ent = num("activitiesEntered")
    rows: list[tuple[str, str]] = []
    if bucket in (4, 82):
        rows += [("通关", f"{num('activitiesCleared'):,.0f}"), ("场次", f"{ent:,.0f}")]
        if ent:
            rows.append(("通关率", f"{num('activitiesCleared') / ent * 100:.0f}%"))
        rows += [("击杀", f"{num('kills'):,.0f}"), ("死亡", f"{num('deaths'):,.0f}"),
                 ("K/D", f"{num('killsDeathsRatio'):.2f}")]
        if has("secondsPlayed"):
            rows.append(("时长", d2.fmt_hours(num("secondsPlayed") / 60)))
            if ent:
                rows.append(("场均", f"{num('secondsPlayed') / ent / 60:.0f} 分"))
    elif bucket == 5:
        rows.append(("场次", f"{ent:,.0f}"))
        if ent:
            rows.append(("胜率", f"{num('activitiesWon') / ent * 100:.0f}%"))
        rows += [("K/D", f"{num('killsDeathsRatio'):.2f}"), ("击杀", f"{num('kills'):,.0f}"),
                 ("死亡", f"{num('deaths'):,.0f}")]
        if has("bestSingleGameKills"):
            rows.append(("最佳单场", f"{num('bestSingleGameKills'):,.0f}"))
        if has("longestWinStreak"):
            rows.append(("最长连胜", f"{num('longestWinStreak'):,.0f}"))
        if has("secondsPlayed"):
            rows.append(("时长", d2.fmt_hours(num("secondsPlayed") / 60)))
    elif bucket == 63:
        rows.append(("场次", f"{ent:,.0f}"))
        if ent:
            rows.append(("胜率", f"{num('activitiesWon') / ent * 100:.0f}%"))
        rows += [("K/D", f"{num('killsDeathsRatio'):.2f}"), ("击杀", f"{num('kills'):,.0f}")]
        if has("motesDeposited"):
            rows.append(("存光尘", f"{num('motesDeposited'):,.0f}"))
        if has("secondsPlayed"):
            rows.append(("时长", d2.fmt_hours(num("secondsPlayed") / 60)))
    else:
        rows += [("场次", f"{ent:,.0f}"), ("通关", f"{num('activitiesCleared'):,.0f}"),
                 ("击杀", f"{num('kills'):,.0f}"), ("K/D", f"{num('killsDeathsRatio'):.2f}")]
        if has("secondsPlayed"):
            rows.append(("时长", d2.fmt_hours(num("secondsPlayed") / 60)))
    return rows


async def _member_row(mtype: int, mid: str, bucket: int | None, is_self: bool,
                      name_hint: str = "") -> dict:
    """单个成员的卡片行：名字/职业/光能/徽标 + 该模式生涯数据（单人失败只丢这一行）"""
    row = {"mid": mid, "name": name_hint or f"…{mid[-6:]}", "class": "", "light": 0,
           "emblem": "", "is_self": is_self, "rows": [], "tiles": [], "team": None}
    try:
        tile_entries = (_RAID_METRICS if bucket == 4 else
                        _DUNGEON_METRICS if bucket == 82 else None)
        prof = await _profile_ex(mtype, mid, "100,200,1100" if tile_entries else "100,200")
        ui = ((prof.get("profile") or {}).get("data") or {}).get("userInfo") or {}
        nm = ui.get("bungieGlobalDisplayName") or ui.get("displayName")
        if nm:
            row["name"] = f"{nm}#{d2.fmt_code(ui.get('bungieGlobalDisplayNameCode'))}"
        lc = _latest_char((prof.get("characters") or {}).get("data") or {})
        if lc:
            cid, c = lc
            row["class"] = d2.CLASS_NAMES.get(c.get("classType"), "")
            row["light"] = int(c.get("light") or 0)
            if c.get("emblemPath"):
                row["emblem"] = d2.BASE + c["emblemPath"]
            if tile_entries:
                row["tiles"] = _raid_tiles(
                    ((prof.get("metrics") or {}).get("data") or {}).get("metrics") or {},
                    tile_entries)
            elif bucket:
                row["rows"] = await _stats_rows(mtype, mid, cid, bucket)
    except Exception:  # noqa: BLE001  隐私/接口失败：名字都拿不到就留个短 mid
        pass
    return row


async def _career_row(mtype: int, mid: str, is_self: bool, name_hint: str = "") -> dict:
    """轨道态成员行：只有 生涯总时长 + 成就点数。

    时长 = 各角色 200 组件 minutesPlayedTotal 求和（分钟）；
    成就点数 = 900 组件 profileRecords.score（游戏内「凯旋分数」口径）。
    没公开这两项的人照常出名字，数值留「—」。"""
    row = {"mid": mid, "name": name_hint or f"…{mid[-6:]}", "class": "", "light": 0,
           "emblem": "", "is_self": is_self, "rows": [], "tiles": [], "team": None}
    try:
        prof = await _profile_ex(mtype, mid, "100,200,900")
        ui = ((prof.get("profile") or {}).get("data") or {}).get("userInfo") or {}
        nm = ui.get("bungieGlobalDisplayName") or ui.get("displayName")
        if nm:
            row["name"] = f"{nm}#{d2.fmt_code(ui.get('bungieGlobalDisplayNameCode'))}"
        chars = (prof.get("characters") or {}).get("data") or {}
        lc = _latest_char(chars)
        if lc:
            _cid, c = lc
            row["class"] = d2.CLASS_NAMES.get(c.get("classType"), "")
            row["light"] = int(c.get("light") or 0)
            if c.get("emblemPath"):
                row["emblem"] = d2.BASE + c["emblemPath"]
        mins = sum(int(c.get("minutesPlayedTotal") or 0) for c in chars.values())
        score = (((prof.get("profileRecords") or {}).get("data") or {})).get("score")
        row["rows"] = [("生涯总时长", d2.fmt_hours(mins) if mins else "—"),
                       ("成就点数", f"{int(score):,}" if score is not None else "—")]
    except Exception:  # noqa: BLE001  隐私/接口失败：名字都拿不到就留个短 mid
        row["rows"] = [("生涯总时长", "—"), ("成就点数", "—")]
    return row


async def _retry_twice(call, *args):
    """整条命令的地基（玩家解析 / 主档案）多试一次：这类失败=整卡出不来，
    自动重试好过让用户手动重发；成员行那些可选数据才"失败就放弃"。"""
    for attempt in (1, 2):
        try:
            return await call(*args)
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            await asyncio.sleep(1)


def _end_of(e: dict) -> datetime | None:
    """历史行 → 结束时间（period 是开局时间，实测 2026-10-07）"""
    p = _parse_dt(e.get("period", ""))
    if not p or not e.get("instance"):
        return None
    return p + timedelta(seconds=int(e.get("duration") or 0))


async def _hist_rows(mtype: int, mid: str, cid: str, count: int = 12) -> list[dict]:
    """该角色的对局历史，只留真对局，按时间倒序（官方顺序）。

    巡逻/社交空间的会话行必须剔除：它们会冒充「当前这一场」（见 _activity_kind）。"""
    if not cid:
        return []
    raw = await d2.activity_history(mtype, mid, cid, 0, count=count)
    kinds = await asyncio.gather(*(_activity_kind(e.get("ref") or 0) for e in raw))
    return [e for e, k in zip(raw, kinds) if k == "match"]


def _newest_finished(hist: list[dict], now: datetime) -> dict | None:
    """刚打完的那一场：历史第一条真对局，且结束在 _JUST_ENDED 内，否则 None。

    轨道态专用。**官方一回到轨道就把 204 的 currentActivityHash 换成占位 hash
    （实测 2026-10-07：人在突袭结束 2 秒后查询，204 已经是 82913930、transitory
    的 players 归 0，但 startTime 还留着上一场的时间）——单看 204 分不出
    「刚打完」和「一直在轨道」，只能由历史行说了算。** 历史倒序，第一条不够新
    就不必往下看。"""
    for e in hist:
        end = _end_of(e)
        if end is None:
            continue
        return e if (end <= now and now - end <= _JUST_ENDED) else None
    return None


def _last_of(e: dict | None) -> dict | None:
    """「上一场」一行用的字段（活动名 / 时长 / 起止时刻，UTC+8）"""
    if not e:
        return None
    p, end = _parse_dt(e.get("period", "")), _end_of(e)
    a, b = _cn(p) if p else "", _cn(end) if end else ""
    span = f"{a[6:]}–{b[6:]}" if (a and b and a[:5] == b[:5]) else (f"{a} – {b}" if b else a)
    return {"name": e.get("name") or "未知活动",
            "minutes": int((e.get("duration") or 0) // 60),
            "span": span, "ref": e.get("ref") or 0}


async def _recent_finished(mtype: int, mid: str, cid: str, now: datetime) -> dict | None:
    """刚打完的那一场（宽限 _JUST_ENDED 内），拿不到就 None。

    轨道/自由漫游卡上的可选信息，所以照样套单次等待上限：Bungie 慢或报错时
    宁可这一行不显示，也别让整张卡干等。"""
    try:
        hist = await asyncio.wait_for(_hist_rows(mtype, mid, cid, count=3),
                                      timeout=_MEMBER_BUDGET)
    except Exception:  # noqa: BLE001  超时/接口失败都只是少一行
        return None
    return _last_of(_newest_finished(hist, now))


async def _rows_for(roster: list[dict], mid: str, mtype: int, party: list[str],
                    bucket: int | None) -> tuple[list[dict], list[dict]]:
    """名单补齐（自己 + 可见队伍成员）+ 每人该模式生涯数据行"""
    known = {r["mid"] for r in roster}
    if mid not in known:
        roster.insert(0, {"mid": mid, "mtype": mtype, "name": ""})
        known.add(mid)
    for pm in party:
        if pm and pm not in known and len(roster) < _MAX_MEMBERS:
            roster.append({"mid": pm, "mtype": mtype, "name": ""})
            known.add(pm)
    roster = roster[:_MAX_MEMBERS]
    rows = list(await asyncio.gather(*(
        _row_budget(_member_row(r["mtype"], r["mid"], bucket, r["mid"] == mid, r["name"]),
                    r["mid"], r["mid"] == mid, r["name"]) for r in roster)))
    for r, row in zip(roster, rows):
        row["team"] = r.get("team")      # 供卡片按阵营分组（熔炉/智谋）
    return roster, rows


async def _match_brief(member: dict, mtype: int, mid: str, party: list[str],
                       e: dict, *, state: str, now: datetime | None = None) -> dict:
    """本场对局的卡（进行中 / 刚结束）：活动名 + **此刻仍在场的**名单 + 每人该模式生涯。

    名单来自本场 PGCR，但 PGCR 记的是「本场参与过的所有人」（中途退队/被替补换下的都在，
    实测 3 人位活动里躺 6 个账号），所以按 `startSeconds`/`timePlayedSeconds` 过滤：
    state="live" 比现在（离场的人不进名单），"ended" 比对局结束时刻（最后还在队里的人）；
    被过滤掉的人在返回里只留个 `left_out` 计数（卡片注一行，不占名单）。

    对局进行中始终 fresh：PGCR 随队友进本逐个补全，吃 6 小时缓存会把开局那份单人名单冻住
    （2026-10-04 实测）；刚结束也要 fresh——那条缓存可能正是"打一半时"抓的残本。"""
    now = now or datetime.now(timezone.utc)
    started = _parse_dt(e.get("period", ""))
    try:
        pgcr = await _pgcr(e["instance"], fresh=True)
    except Exception:  # noqa: BLE001  拿不到 PGCR 就退回可见队伍
        pgcr = {}
    modes = ((pgcr.get("activityDetails") or {}).get("modes") or e.get("modes") or [])
    bucket = _bucket(modes) if modes else 7
    infos = [i for i in (_entry_info(x) for x in (pgcr.get("entries") or [])) if i["mid"]]
    period = _parse_dt(pgcr.get("period") or "") or started
    dur = max([float(e.get("duration") or 0)] + [float(i.get("dur") or 0) for i in infos])
    ref = (period + timedelta(seconds=dur)) if (state == "ended" and period and dur) else now
    uniq, left_out = _pgcr_roster(infos, period, ref)
    roster = [{"mid": i["mid"], "mtype": i["mtype"] or mtype, "name": i["name"],
               "team": i.get("team")} for i in uniq] or \
             [{"mid": mid, "mtype": mtype, "name": ""}]
    # 可见队伍（实时）只在「进行中」并进来补齐 PGCR 还没写上的人；刚打完不并——
    # 那是出本后的当前队伍，拿它往上一场名单里塞人正是"凑出假名单"的老坑
    roster, rows = await _rows_for(roster, mid, mtype,
                                   party if state == "live" else [], bucket)
    return {
        "name": f"{member['display']}#{d2.fmt_code(member['code'])}",
        "state": state,
        "in_activity": True,
        "live": state == "live",
        "activity": e.get("name") or "未知活动",
        "bucket": bucket,
        "mode_name": BUCKETS[bucket][1] if bucket else "组队中",
        "started_text": _cn(started) if started else "",
        "duration_min": int((e.get("duration") or 0) // 60),
        "left_out": left_out,
        "members": rows,
        "self_mid": mid,   # 名单按击杀排序，自己不一定在第一位：/队伍配装 靠这个标「查询」
        "roster": [{"mid": r["mid"], "mtype": r["mtype"], "name": r.get("name") or ""}
                   for r in roster],   # /队伍配装 复用名单发现
    }


async def _career_brief(member: dict, mtype: int, mid: str, prof: dict, party: list[str], *,
                        state: str, mode_name: str, activity: str = "",
                        last: dict | None = None) -> dict:
    """「没在对局里」的卡：轨道 / 自由漫游 / 社交空间 / 不在线。
    名单 = 官方实时队伍，每人只给 生涯总时长 + 成就点数（用户口径，见 CHANGELOG）；
    last = 刚打完的那一场（宽限窗口内），卡片在抬头下面补一行「上一场」。"""
    last_text = ""
    if state == "offline":
        lc = _latest_char((prof.get("characters") or {}).get("data") or {})
        lp = _parse_dt((lc[1] or {}).get("dateLastPlayed") or "") if lc else None
        if lp:
            last_text = f"最后游玩 {_cn(lp)}（UTC+8）"
    roster = [{"mid": mid, "mtype": mtype, "name": ""}]
    known = {mid}
    for pm in party:
        if pm and pm not in known and len(roster) < _MAX_MEMBERS:
            roster.append({"mid": pm, "mtype": mtype, "name": ""})
            known.add(pm)
    rows = list(await asyncio.gather(*(
        _row_budget(_career_row(r["mtype"], r["mid"], r["mid"] == mid, r["name"]),
                    r["mid"], r["mid"] == mid, r["name"]) for r in roster)))
    return {
        "name": f"{member['display']}#{d2.fmt_code(member['code'])}",
        "state": state,
        "in_activity": False,
        "live": False,
        "activity": activity,
        "bucket": 7,
        "mode_name": mode_name,
        "started_text": "",
        "duration_min": 0,
        "members": rows,
        "last_text": last_text,
        "last": last,
        "self_mid": mid,    # 名单首位是自己，但显式给出更稳（/队伍配装 标「查询」）
        "roster": roster,   # [{mid, mtype, name}]，/队伍配装 复用名单发现
    }


async def collect(name: str) -> dict:
    """采集「当前在打什么 + 队内成员数据」

    在打本/刚打完 → 本场活动 + 同队成员在该模式的生涯数据；
    轨道/自由漫游/社交空间/不在线 → 队内每人 生涯总时长 + 成就点数，
    宽限窗口内再补一行「上一场 X（多久）」——一出本 204 就换成占位 hash，
    没有这一行的话「刚打完」与「一直在轨道」在数据上长得一模一样。

    玩家不存在抛 LookupError。"""
    member = await _retry_twice(d2.resolve_member, name)
    if not member:
        raise LookupError(f"没找到玩家 {name}")
    mtype, mid = member["mtype"], member["mid"]
    prof = await _retry_twice(_profile_ex, mtype, mid, "100,200,204,1000")
    now = datetime.now(timezone.utc)

    cur = _current(prof)
    kind = await _activity_kind(cur[1]) if cur else "none"
    if kind == "none":
        cur = None                       # 轨道占位 hash ≠ 在打活动

    # 官方实时队伍（隐私设置可能隐藏部分成员）
    tr = (prof.get("profileTransitoryData") or {}).get("data") or {}
    party = [str(p.get("membershipId") or "") for p in (tr.get("partyMembers") or [])]
    # 实时对局信号：transitory.currentActivity 的 numberOfPlayers（打本时 ≥1，轨道/待机 = 0）。
    # 退出到选人界面后 204 的 currentActivityHash 会把上一场挂一阵子（看着像"进行中"），
    # 必须拿这个实时信号把关：没有它就不算在打。
    tr_act = (tr.get("currentActivity") or {}) if tr else {}
    live_players = int(tr_act.get("numberOfPlayers") or 0) + int(tr_act.get("numberOfOpponents") or 0)

    if cur and kind in ("patrol", "social") and tr:
        # ---- 自由漫游 / 社交空间：在游戏里但没在对局，给队内生涯总览 ----
        # （巡逻区在 manifest 里有名字，旧判据会把它当对局，再配上"上一次漫游"的历史行）
        return await _career_brief(
            member, mtype, mid, prof, party, state="world",
            mode_name="自由漫游" if kind == "patrol" else "社交空间",
            activity=await _activity_label(cur[1]),
            last=await _recent_finished(mtype, mid, cur[0], now))

    if cur and kind == "match":
        # ---- 真活动 hash：在打 / 刚进本 / 刚打完，三种都由这里分出去 ----
        hist = await _hist_rows(mtype, mid, cur[0])
        match = _match_entry(hist, cur[2], now)
        state = "live" if (live_players >= 1 and match and match["live"]) else "ended"
        if match and state == "ended":
            end = _end_of(match["entry"]) or now
            if now - end > _JUST_ENDED:
                match = None             # 早就结束的活动（204 残留）→ 当轨道处理
        if match:
            return await _match_brief(member, mtype, mid, party, match["entry"],
                                      state=state, now=now)

        if live_players >= 1 or now - cur[2] <= _JUST_IN:
            # 在活动中、但本场名单还没发布（匹配局开局 / 突袭进行中 / 刚进本
            # transitory 还没跟上）：活动名与模式直接按官方给的 activity hash 认
            # （真活动 manifest 里一定有名字，本地索引里是中文名）
            activity = await _activity_label(cur[1])
            bucket = await activity_bucket(cur[1])
            if not activity and hist:
                # 名字查不到（网络抖动等）才退回「上一把」；正常情况本场名字来自 hash
                newest = hist[0]
                activity = newest.get("name") or ""
                modes = newest.get("modes") or []
                bucket = _bucket(modes) if modes else bucket
            roster, rows = await _rows_for([{"mid": mid, "mtype": mtype, "name": ""}],
                                           mid, mtype, party, bucket)
            return {
                "name": f"{member['display']}#{d2.fmt_code(member['code'])}",
                "state": "pending",
                "in_activity": True,
                "live": False,
                "activity": activity,
                "bucket": bucket or 7,
                "mode_name": BUCKETS[bucket][1] if bucket else "组队中",
                "started_text": _cn(cur[2]),
                "duration_min": max(0, int((now - cur[2]).total_seconds() // 60)),
                "players_now": live_players,   # 官方实时人数（名单还没发布时告诉用户本场几个人）
                "members": rows,
                "self_mid": mid,
                "roster": [{"mid": r["mid"], "mtype": r["mtype"], "name": r.get("name") or ""}
                           for r in roster],
            }
        # 204 残留（离开很久的活动）→ 落到下面按轨道/不在线处理

    # ---- 没在打：在世界里（轨道/塔/组队待机）或不在线（选人界面/已退出，transitory 都没了）----
    # 退出游戏后 204 会把上一场活动挂一阵子，但 transitory 会先消失/清零——以它为准，
    # 宁可报「不在线」也别把上一场当成"进行中"（2026-10-02 用户实测打回过）。
    if not tr:
        return await _career_brief(member, mtype, mid, prof, party, state="offline",
                                   mode_name="不在线")
    return await _career_brief(
        member, mtype, mid, prof, party, state="orbit", mode_name="轨道待机",
        last=await _recent_finished(mtype, mid, _active_cid(prof), now))
