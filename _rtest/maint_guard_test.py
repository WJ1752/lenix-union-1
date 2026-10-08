"""维护应对机制的端到端测试（不联网：把 httpx 客户端换成会吐维护响应的假客户端）

覆盖：
1. 维护判定（错误码表 / HTML 维护页 / OAuth 那条 DestinyThrottledByGameServer）
2. 维护中查询被拦（不打接口、用户看到「维护中」）
3. 脏响应不进响应缓存 + 点亮维护态时清缓存
4. 自动中止在跑的后台任务（状态 aborted，文案是维护口径）
5. 补查复核：空 Response 重取一次，仍旧空就报错（绝不当成 0）
6. 落盘缓存不被维护数据覆盖 / 维护窗口内的缓存被判污染
7. 维护恢复回调（调度器补跑）
8. 未发送图片落盘 + 面板预览/重发路径
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

import bungie_status as bst  # noqa: E402
import destiny_data as d2  # noqa: E402

FAILS = []
OKS = []


def check(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if extra and not cond else ""))


# ---------- 假客户端 ----------
class FakeInner:
    """按 URL/参数给预设响应；记录调用次数，用来验证「维护中根本没打接口」"""

    is_closed = False

    def __init__(self, rules):
        self.rules = rules          # [(匹配子串, [响应…])] 依次取，取完复用最后一个
        self.calls = []

    def _pick(self, url, params, body):
        for pat, resps in self.rules:
            if pat in url:
                n = sum(1 for c in self.calls if pat in c[0])
                self.calls.append((url, params, body))
                return resps[min(n, len(resps) - 1)]
        self.calls.append((url, params, body))
        return {"Response": {}, "ErrorCode": 1, "ErrorStatus": "Success"}

    async def _do(self, url, **kw):
        d = self._pick(str(url), kw.get("params"), kw.get("json"))
        body = d if isinstance(d, (bytes, str)) else json.dumps(d).encode()
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(200, content=body, request=httpx.Request("GET", str(url)))

    async def get(self, url, **kw):
        return await self._do(url, **kw)

    async def post(self, url, **kw):
        return await self._do(url, **kw)


def install(rules):
    """把 destiny_data 的客户端换成假客户端（当前事件循环那份）"""
    loop = asyncio.get_running_loop()
    fake = FakeInner(rules)
    d2._CLIENTS[loop] = d2._CachedClient(fake)
    return fake


def reset_state():
    d2._RESP.clear()
    d2.JOBS.clear()
    d2._JOB_QUEUE[:] = []
    d2._JOB_RUNNING.clear()
    d2._JOB_TASK.clear()
    d2._DEF_CACHE.clear()
    bst.clear("测试复位")
    st = bst._state
    st.update(on=False, kind="", detail="", last_ok=0.0, pass_at=0.0, pass_n=0)
    bst._windows[:] = []
    bst._clear_cbs[:] = []
    d2.bst.on_trip(d2._on_maintenance)      # _windows/_clear_cbs 被清了，回调要装回来


# 测试绝不碰真数据：维护窗口与赛季时长缓存都改到临时文件上
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_maint_test_state.json")
_TMP_TIME = "_maint_test_time_cache.json"
bst._LOG_FILE = _TMP
d2._TIME_CACHE_FILE = _TMP_TIME


def cleanup_tmp():
    for p in (bst._writable_path(_TMP), d2._writable_path(_TMP_TIME)):
        try:
            os.remove(p)
        except OSError:
            pass


# ---------- 1. 判定表 ----------
def test_classify():
    print("\n[1] 维护判定")
    check("ErrorCode 5 SystemDisabled → 维护",
          bst.classify_json({"ErrorCode": 5, "ErrorStatus": "SystemDisabled"}) is not None)
    check("1672 DestinyThrottledByGameServer → 维护（10-06 实测那条）",
          (bst.classify_json({"ErrorCode": 1672,
                              "ErrorStatus": "DestinyThrottledByGameServer"}) or [""])[0] == "disabled")
    check("1651/1652/1688（游戏服掐链）→ 维护",
          all(bst.classify_json({"ErrorCode": c}) for c in (1651, 1652, 1688)))
    check("1601 DestinyAccountNotFound 不算维护（档案不存在是业务语义）",
          bst.classify_json({"ErrorCode": 1601, "ErrorStatus": "DestinyAccountNotFound"}) is None)
    check("36 限流归类为 throttled（不冻结全局）",
          (bst.classify_json({"ErrorCode": 36}) or [""])[0] == "throttled")
    check("正常响应不误判", bst.classify_json({"ErrorCode": 1, "ErrorStatus": "Success"}) is None)
    check("OAuth 那条 HTTP400 体 → 维护",
          bst.classify_throttle_body('{"error":"server_error",'
                                     '"error_description":"DestinyThrottledByGameServer"}') is not None)
    bst.note_http(503, b"<html><body>Destiny 2 is down for maintenance</body></html>")
    check("HTML 维护页 → 维护（HTTP 层）", bst.is_down())
    bst.clear("测试")


# ---------- 2~4. 数据层拦截 / 缓存 / 任务中止 ----------
async def test_client_guard():
    print("\n[2] 维护中拦截 + 脏响应不落缓存 + 中止任务")
    reset_state()
    fake = install([("/Platform/Destiny2/3/Profile/", [
        {"Response": {}, "ErrorCode": 5, "ErrorStatus": "SystemDisabled",
         "Message": "The system is disabled."}])])

    # 先跑一个后台任务，验证维护点亮时会被自动中止
    started = asyncio.Event()

    async def _job():
        started.set()
        for _ in range(200):
            await d2._job_checkpoint("t1")
            await asyncio.sleep(0.02)

    d2.JOBS["t1"] = {"id": "t1", "label": "测试任务", "status": "queued", "ts": time.time()}
    d2._enqueue_job("t1", _job)
    await asyncio.wait_for(started.wait(), 5)
    d2._JOB_RUNNING.add("t1")

    try:
        await d2.client().get("/Platform/Destiny2/3/Profile/123/", params={"components": "100"})
        raised = False
    except bst.BungieMaintenanceError:
        raised = True
    check("维护响应 → 抛 BungieMaintenanceError", raised and bst.is_down())
    check("响应缓存里没有这条脏响应",
          not any("Profile" in k for k in d2._RESP), f"keys={list(d2._RESP)[:3]}")
    check("后台任务被自动中止", (d2.JOBS.get("t1") or {}).get("status") == "aborted",
          str(d2.JOBS.get("t1")))
    check("中止文案说明是维护（不是管理员）",
          "维护" in str((d2.JOBS.get("t1") or {}).get("error")), str(d2.JOBS.get("t1")))
    check("用户看到的是维护文案", "维护" in bst.text())

    n0 = len(fake.calls)
    # 稳态（刚探过官方状态、刚放行过一次）时，维护中的查询应当被直接拦下、不打接口
    bst._state["probe_at"] = time.time()
    bst._state["pass_at"] = time.time()
    try:
        await d2.client().get("/Platform/Destiny2/3/Profile/123/", params={"components": "100"})
        blocked = False
    except bst.BungieMaintenanceError:
        blocked = True
    check("维护中直接拦下（不再打接口）", blocked and len(fake.calls) == n0,
          f"calls +{len(fake.calls) - n0}")

    # 放行窗口到点：允许一个真实请求出去探路（官方恢复就靠它证实）
    bst._state["pass_at"] = time.time() - bst.PASS_EVERY - 1
    try:
        await d2.client().get("/Platform/Destiny2/3/Profile/123/", params={"components": "100"})
    except bst.BungieMaintenanceError:
        pass
    check("到点放行一个探测请求（真实请求失败即继续拦截）",
          len(fake.calls) == n0 + 1 and bst.is_down(), f"calls +{len(fake.calls) - n0}")

    # 官方恢复：真实请求成功 → 自动解除
    install([("/Platform/Destiny2/3/Profile/", [
        {"Response": {"characters": {"data": {}}}, "ErrorCode": 1, "ErrorStatus": "Success"}])])
    bst._state["pass_at"] = time.time() - bst.PASS_EVERY - 1
    await d2.client().get("/Platform/Destiny2/3/Profile/123/", params={"components": "100"})
    check("真实请求成功 → 维护态自动解除（无需重启）", not bst.is_down())
    bst.clear("测试")


# ---------- 5. 补查复核 ----------
async def test_recheck():
    print("\n[5] 补查复核（空数据不落成 0）")
    reset_state()
    install([("/Stats/", [
        {"Response": None, "ErrorCode": 1, "ErrorStatus": "Success"},          # 第一次：可疑空数据
        {"Response": {"allPvP": {"allTime": {"kills": {"basic": {"value": 7}}}}},
         "ErrorCode": 1, "ErrorStatus": "Success"},                            # 复核：正常
    ])])
    st = await d2.char_stats(3, "123", "c1", "101,103,104")
    check("第一次空 → 复核拿到正常数据", st.get("allPvP", {}).get("allTime", {})
          .get("kills", {}).get("basic", {}).get("value") == 7, str(st))

    reset_state()
    install([("/Stats/", [{"Response": {}, "ErrorCode": 1, "ErrorStatus": "Success"}])])
    try:
        await d2.char_stats(3, "123", "c1", "101,103,104")
        raised = False
    except d2.DataSuspiciousError as exc:
        raised = "不完整" in str(exc) or "维护" in str(exc)
    check("两次都空 → 报错而不是返回 0", raised)

    reset_state()
    install([("/Stats/Activities/", [
        {"Response": {}, "ErrorCode": 1620, "ErrorStatus": "DestinyCharacterNotFound"}])])
    acts = await d2.activity_history(3, "123", "c1", 0, count=10)
    check("角色不存在（1620）→ 照旧当空历史（业务语义）", acts == [])

    reset_state()
    install([("/Stats/Activities/", [
        {"Response": None, "ErrorCode": 1672, "ErrorStatus": "DestinyThrottledByGameServer"}])])
    try:
        await d2.activity_history(3, "123", "c1", 0, count=10)
        raised = False
    except bst.BungieMaintenanceError:
        raised = True
    check("对局历史撞维护 → 报错（以前静默当成没有对局）", raised)
    bst.clear("测试")


async def test_pgcr_and_eververse():
    print("\n[6] PGCR / 光尘空货架")
    reset_state()
    install([("/PostGameCarnageReport/", [
        {"Response": None, "ErrorCode": 1, "ErrorStatus": "Success"}])])
    try:
        await d2.get_pgcr("123")
        raised = False
    except d2.DataSuspiciousError:
        raised = True
    check("PGCR 空响应 → 报错（不静默少算）", raised)

    reset_state()
    install([("/Milestones/", [
        {"Response": None, "ErrorCode": 5, "ErrorStatus": "SystemDisabled"}])])
    try:
        await d2.rotation_week(force=True)
        raised = False
    except bst.BungieMaintenanceError:
        raised = True
    check("轮换撞维护 → 报错（不落 key=unknown 的空缓存）", raised)
    bst.clear("测试")


# ---------- 7. 缓存污染判定 ----------
async def test_cache_poison():
    print("\n[7] 维护窗口内的缓存判污染")
    reset_state()
    now = time.time()
    bst._state.update(last_ok=now - 3600)
    bst.trip("disabled", "测试：维护窗口 1 小时前开始")
    bst.clear("测试：维护结束")
    win = bst.windows()[-1]
    check("维护窗口已落盘记录", win and win[0] <= win[1], str(win))
    check("窗口内的时刻判为污染", bst.suspect_at((win[0] + win[1]) / 2))
    check("窗口外的时刻不算污染", not bst.suspect_at(win[0] - 10))
    check("文本时间戳版本同判",
          bst.suspect_stamp(time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime((win[0] + win[1]) / 2))))

    # 生产数据实证：season_time_cache 维护窗口内的封存水位会被回退
    reset_state()
    d2._time_cache.clear()
    d2._time_cache_ready = False
    old_day = time.strftime("%Y-%m-%d", time.localtime(now - 3600))
    d2._time_cache["123|c1"] = {"days": {old_day: 3600}, "done": old_day}
    bst._state.update(last_ok=now - 3600)
    bst.trip("disabled", "测试")
    bst.clear("测试")
    d2._load_time_cache()          # 重新加载触发回退
    ent = d2._time_cache.get("123|c1") or {}
    check("维护前封存的角色水位被回退", (ent.get("done") or "") < old_day,
          f"done={ent.get('done')} 原始={old_day}")
    d2._time_cache.clear()
    d2._time_cache_ready = False


# ---------- 8. 恢复回调（调度器补跑） ----------
async def test_clear_cb():
    print("\n[8] 维护恢复 → 立刻补跑")
    reset_state()
    hits = []
    bst.on_clear(lambda: hits.append(1))
    bst.trip("disabled", "测试")
    bst.clear("测试")
    check("恢复回调被调用（调度器据此立刻补预取/推送）", hits == [1])
    check("恢复后不再拦截查询", not bst.is_down())


# ---------- 9. 未发送图片 ----------
def test_unsent():
    print("\n[9] 未发送图片落盘（面板预览/重发）")
    import bot_log
    fn = bot_log.save_unsent(b"\x89PNG\r\n\x1a\n" + b"x" * 32, "测试卡片/武器", "12345", "")
    check("图片已落盘并返回文件名", bool(fn) and fn.endswith(".png"), str(fn))
    check("文件名里挡掉了非法字符", "/" not in fn, fn)
    p = bot_log.unsent_path(fn)
    check("能按文件名取回绝对路径", bool(p) and os.path.exists(p), p)
    check("路径穿越被挡", bot_log.unsent_path("../../bot_config.json") == "")
    check("列表里能看到", any(x["name"] == fn for x in bot_log.unsent_list()))
    try:
        os.remove(p)
    except OSError:
        pass

# ---------- 10. 限流不整条任务失败 ----------
async def test_throttle_transient():
    print("\n[10] 限流（不是维护）")
    reset_state()
    fake = install([("/PostGameCarnageReport/", [
        {"Response": None, "ErrorCode": 36, "ErrorStatus": "ThrottleLimitExceededMomentarily"},
        {"Response": {"entries": [{"player": {"destinyUserInfo": {"membershipId": "1",
         "bungieGlobalDisplayName": "A"}}, "values": {}}],
         "activityDetails": {"isPrivate": False}},
         "ErrorCode": 1, "ErrorStatus": "Success"},
    ])])
    d = await d2.get_pgcr("999")
    check("限流 → 客户端自动等两秒重试一次就拿到数据", bool(d) and not bst.is_down())
    check("限流没有点亮维护态（不冻结全局）", not bst.is_down())
    # 缓存里只该有重试成功那份（ErrorCode 1），限流那份绝不能进去
    cached = [json.loads(bytes(v[1]).decode("utf-8-sig"))
              for k, v in d2._RESP.items() if "PostGameCarnageReport" in k]
    check("限流响应本身没被写进响应缓存",
          all(c.get("ErrorCode") == 1 for c in cached), str(cached))



# ---------- 11. 响应判定的快慢两条路径 ----------
def test_resp_check_paths():
    print("\n[11] 响应判定（紧凑成功体走快路径，错误体照抓）")
    import httpx as _h

    def resp(obj):
        body = json.dumps(obj, separators=(",", ":")).encode()   # 官方就是紧凑格式（无空格）
        return _h.Response(200, content=body, request=_h.Request("GET", "http://x"))

    reset_state()
    big = {"Response": {"entries": [{"a": i} for i in range(5000)]},
           "ErrorCode": 1, "ThrottleSeconds": 0, "ErrorStatus": "Success",
           "Message": "Ok", "MessageData": {}}
    t0 = time.time()
    check("紧凑成功体判为 ok（快路径）", d2._resp_check(resp(big)) == "ok")
    check("快路径也记了 last_ok（维护窗口起点靠它）",
          abs(bst._state["last_ok"] - t0) < 2, str(bst._state["last_ok"]))
    err = {"Response": {}, "ErrorCode": 5, "ErrorStatus": "SystemDisabled",
           "Message": "disabled"}
    try:
        d2._resp_check(resp(err))
        raised = False
    except bst.BungieMaintenanceError:
        raised = True
    check("紧凑错误体没被快路径放过（仍抛维护）", raised and bst.is_down())
    bst.clear("测试")

    reset_state()
    bst.note_http(200, b"<!DOCTYPE html><title>Destiny 2 maintenance</title>")
    check("HTML 维护页（HTTP 层）判为维护", bst.is_down())
    bst.clear("测试")

    reset_state()
    check("限流体判为 throttle（不冻结全局）",
          d2._resp_check(resp({"Response": {}, "ErrorCode": 36,
                               "ErrorStatus": "ThrottleLimitExceededMomentarily"})) == "throttle"
          and not bst.is_down())


async def main():
    test_classify()
    await test_client_guard()
    await test_recheck()
    await test_pgcr_and_eververse()
    await test_cache_poison()
    await test_clear_cb()
    await test_throttle_transient()
    test_resp_check_paths()
    test_unsent()
    print(f"\n===== 通过 {len(OKS)} / 失败 {len(FAILS)} =====")
    for f in FAILS:
        print("  FAIL:", f)
    cleanup_tmp()
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
