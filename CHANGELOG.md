# 开发记录与实现笔记

> 本文件保留项目全部功能演进记录与实现笔记（原 README 正文），最新功能说明见 [README.md](README.md)。

## 2026-10-08（收尾）完整性复核 + 修的 5 处 + 上一批改动清理入库

**这次做的是「把 10-07/10-08 攒下的改动盘一遍再入库」**，不是新功能。复核口径：全部模块能编译、
离线测试全绿（ev_time 80 / maint_guard 42 / job_engine / job_dedup / heat_cache / resend 16 /
help_list 6 / i18n 三件套 56+11+7 / raid_badges / bot_hook 4）、仓库里没有任何凭据（只有
`certs/localhost*.pem` 这对只对 127.0.0.1 生效的回跳证书，`.gitignore` 里写明为何留）、
`dist_new` 的 exe 是 08:54 打的包、比最后一次源码改动（08:52）新——运行态与源码一致。

**修的 5 处**（都是「说得对但没做到」的缺口，不是新功能）：

| # | 缺口 | 改法 |
|---|---|---|
| 1 | `_char_history_deep._one` 把维护异常当「这一页坏了」吞掉 → 维护期拿半截历史出卡，用户看到「这模式没打过 / 已达上限」（正是维护期最该避免的假结论） | `destiny_data.py`：`except BungieMaintenanceError: raise`，维护一路抛到上层换成「维护中」提示 |
| 2 | 发送超时自查「已核实进群」的那条照样落进 `unsent_images/` → 面板多出一个「重发」按钮，一点就是同一条消息两张 | `nonebot_plugins/destiny2.py`：`_log_send_error` 收 `png`，只在**没送达**（含查不到）时才存盘 |
| 3 | 维护闸门里发提示成功时 `matcher.finish()` 抛的 `FinishedException` 被 `except Exception` 记成「提示发送失败」→ 每次拦查询都在日志里写一条假失败 | 同文件：`except FinishedException: pass` 放行（与 `_finish_send` 里同一套写法） |
| 4 | `bot_platform` 拉群成员仍用 `"onebot" in type(bot).__module__` 判通道 → 被 `nonebot` 子串误命中，官方通道的 bot 也进来、每群白调一次必失败的 `get_group_member_list`（10-08 已在调度器修过同一处，这是漏网的第三处） | `bot_platform.py`：改 `"adapters.onebot"` |
| 5 | `_sub_agg_segments` 用 `datetime.date.today()` 判「段结束了没」（全盘时钟口径里的漏网） | `destiny_data.py`：改 `_cn_now().date()` |

顺带修好 `_rtest/test_i18n_plugin.py` 里那条一直红着的断言：它只认 `xx掉落`、不认
`raid_loot.command_names()` 同样会注册的 `掉落xx`（`掉落ce` / `掉落克洛塔`），断言本身写错了，
改成「别名要么含『掉落』要么就是 loot」——判断的是「不冒出会误吞普通消息的裸词」，那才是它想守的东西。

**复核时看到、但这轮没动的 4 处口径问题**（都涉及产品取舍，留给人拍板，别当没看见）：
① `season_time_cache` 封存水位：某个月取失败（`got is None`）时只 `continue`，后面月份成功仍会把
`done` 推过去 → 那个月的水位被推过 = 永久少算（CHANGELOG 声称修过的那类 bug 在「部分失败」这条路上还没堵）；
② `bungie_status.note_ok()` 对「HTTP 200 + 空数据」也当恢复信号 → 最阴的那种维护形态会让维护态提前熄掉、
污染窗口记短；③ `mode_report` 里 `except Exception: rep["gambit"] = {}` 把 `gambit_career` 特意抛出的
`DataSuspiciousError` 吞掉（它抛就是为了别让用户看不出少了一截）；④ `bungie_status.suspect_stamp` 用
`time.mktime` 按本机时区解析 `time.strftime` 写的戳（写入/读出两边都是本机时间才自洽，跨时区读会判错窗口）。

**清理**（工作区里 10-07/10-08 各轮探针留下的产物，全部可重跑生成，不入库）：
`_rtest/edge_cf_profile/`（临时起的 Edge 配置档，30M，含 cookie）、`_rtest/drep_bundles/`（抓来的
dungeon.report 前端 bundle）、`_rtest/openapi.json`（1.8M 官方 OpenAPI 快照）、`_rtest/ev_vendor_raw.json`
（2.3M GetVendors 原始返回）、`_rtest/cata_rep.json`、`_rtest/drep_*.json`、`_rtest/_emb_*.{jpg,img}`、
`_rtest/weapon_src_preview/`、以及 `_rtest/` 下 81 张探针截图 + 27 个页面转储 + 各轮构建日志；
`_rtest/` 从 105M 降到 2.1M。另有 6 个纯脚手架脚本删除（`_dummy_cb.py` 假回调桩、`maint_probe.py`
只打印 key 长度、`gc_debug.py`、`_ft_check.py`、`statdef2/3.py` 被 `gambit_statdef_probe.py` 覆盖）。
`.gitignore` 补上这些产物与 `unsent_images/`、`maintenance_log.json` 的规则（`.zcodeignore` 同步）；
探针脚本里 10 处硬编码的 `F:\智谱Zcode数据存储\BOT` 全改成按 `__file__` 上推两级，换机器也能跑。

> ⚠️ 修的都是源码，**运行中的 exe 还是 08:54 那版**：要吃到上面 5 处修复得 `build_exe.bat` 重打包 +
> `deploy_exe.ps1`。这批源码改动本身已在运行态验证过（exe 比源码新）。

## 2026-10-08 光尘商店时间强关联：每天 1 点刷（官方商品级刷新时刻）+ 卡片标出「哪天的 1 点刷的」+ 全盘时钟锚北京时间

**用户报**：「光尘商店貌似还是没刷新啊，做一下时间强关联，输出时检查是否是当天刷新的数据，并在输出图片上
标注是哪天的 1 点刷新的数据」「今天已经是 8 号了为什么还会显示货架未刷新，检查有问题啊，注意全盘时钟使用
中国北京时间」。

**先查证据**（`_rtest/ev_refresh_probe.py` 拉真实 GetVendors，原始返回存 `_rtest/ev_vendor_raw.json`）：

| 层 | 官方字段 | 实测值（2026-10-08 08:20） | 说明 |
|---|---|---|---|
| vendor 实体 | `nextRefreshDate` | 10-14 01:00（周三，10 个光尘 vendor 一致） | 周值——**只看它会把货架误判成周刷** |
| **每件商品** | `overrideNextRefreshDate` | **10-09 01:00**（18 件全一致） | **货架每天 1 点换**（用户口径对） |

**「没刷新」的真实成因**（三件事叠在一起）：
1. 10-07/10-08 官方维护，**货架冻住不换**：10-07 03:18 与 10-08 01:05 两次重取拿回同一批货（`领先时代…`），
   直到维护结束（10-08 上午）官方才切成 `我的财富…`；
2. 旧实现只看「缓存键 == 今天」：1 点一过确实会重取，**但重取回来的还是不是新货架它不管**——维护期重取到的
   旧货架照样落盘当真；而且**没有任何 TTL**，重取完这一轮就要等到下一个 1 点（连维护结束都不触发重取，
   因为缓存键没变），于是旧货架挂一整天；
3. 10-08 那轮维护防护只堵了「成功但空货架」一半，**「非空但过期的旧货架」这一路没堵**，正是这次的现象。

**改法（`destiny_data.py`，时间强关联）**：
- **刷新时刻一律读官方字段，商品级优先**：`overrideNextRefreshDate`（取全货架最早的那个）→ 没有才退回 vendor 的
  `nextRefreshDate`；`9999-12-31` 哨兵（不再刷新）与坏值当没有。`_ev_next_refresh()` / `_ev_parse_when()` 一处负责。
- 每条货架盖时间戳：`refresh_at`（**本轮归属的 01:00**；跨刷新点时用「上一轮官方说的下次刷新」反推，最准，
  首跑才按周期回推）、`next_refresh`（官方下次刷新）、`fetched_at`、`cycle_days`（1=每天 / 7=每周）。
- **输出前必查有效期**（`_ev_reason`）：过了官方刷新点 → 重取（10 分钟内问过就先忍着，别打成死循环）；复位点后
  30 分钟内缓存只留 10 分钟（官方常在复位点之后才真正切完货架）；**落在维护窗口内、或早于最近一次维护恢复的
  货架一律重取**（这次「没刷新」的根因，与 raid_history/heatmap 那些缓存同一套窗口判定）；其余 30 分钟核一次。
- 官方晚切（刷新点过了接口还给上一轮）不装新：`stale=True`、归属点停在上一轮，`ev_behind()` 判「手里不是当前这一轮」。
- 缓存 `ver` 3 → 4（data 里带时间戳；跨天不再整条作废，改成按时间判有效期）；`force=True` 语义不变。

**卡片/网页（`bot_cards.eververse_card` 同一份排版，QQ 与 `/eververse` 都吃）**：抬头换成时间条——
`本轮货架 10月8日 01:00（周四） 刷新 · 下次刷新 10月9日 01:00（周五） · 数据抓取 10月8日 08:40 · 每天 01:00 刷新 · 每 30 分钟自动核对`；
万一手里的还是上一轮，时间条上多一句红字并写明缺的是哪一轮：`⚠ 还没取到 10月8日 01:00（周四） 这轮新货架（手里仍是 10月7日 01:00（周三） 那轮），每 10 分钟自动重试`。
叶脚写「数据归属 … 那轮」。**旧的「每天凌晨 1 点刷新」在页脚硬编码的说法、以及中途试过的「官方货架/周重置」那套
提示都删了**——一天一刷就是货架的真相，用户口径为准（那套「官方货架还没切到新一轮」的长提示条只在 `_rtest` 的
样本渲染里出现过一次，被用户当成真实输出看，已把样本图删掉，脚本仍可 `--stale` 现出）。

**全盘时钟锚北京时间（用户点名要求）**：新增 `EV_TZ`(+08) / `_ev_dt()` / `_cn_now()` / `cn_str()` 作为唯一时间口径，
下面这些以前跟本机时区走的点全部换成北京时间（机器时区一变就会把「今天/这周/谁在场」算错）：
`_ev_day`（商店与遗失区域的日键）、`_xur_present` / `_xur_next_arrival`（老九周六 01:00 到场、周三 01:00 离场的窗口）、
`_gm_week_key`（宗师周键）、`distortion_now`（扭曲星球时段表）、`bot_scheduler.rotation_week_key`（轮换推送周键，
顺带修了它 `combine()` 出 naive 时间再和 aware 比大小的混用）、`bot_log` 的时间戳/未发出图片文件名、
`bungie_status` 维护窗口提示、`destiny_data` 的进度 ETA 与面板任务时刻、`weapon_usage` 快照时间戳（带 +08 偏移，
`[:10]` 取到的就是北京日期）。**唯一故意不用北京时区的一处**：`season_time_cache` 维护回退水位 `since_day` 用
UTC 日（因为日表键来自对局 `period` 的 UTC 日期，用北京日会晚 8 小时、少作废几天缓存），注释里写明了。

**测试**：`_rtest/ev_time_test.py` 80 项全通——时间旅行（拨钟）验 1 点前后、跨刷新点换轮、官方晚切、TTL、
复位点后 10 分钟窗、维护窗口/恢复点作废、落盘复用与 ver 作废、force、空货架拦下不落盘；卡片标注与「还是上一轮」
分支各渲染一次；最后一段是**时区独立性护栏**：用一个「不带 tz 的 `fromtimestamp/now/today` 一律抛错」的假
`datetime` 模块替换进 `destiny_data` / `bot_scheduler`，把上面所有时间函数再跑一遍——以后谁改回本机时间，
在 +08 机器上也照样当场失败。实拍：`_rtest/ev_card_shot.py` → `_rtest/_ev_card.png`（真实 18 件货架、时间条齐全）。
`test_sweep.py` 的 `/eververse` 断言加「本轮货架」「数据抓取」。

## 2026-10-08 维护期防护：判定 / 拦查询 / 自动中止 / 补查复核 + 发送失败的图片可预览重发

**用户报**：「bungie 昨天晚上服务器维护关闭，导致在此期间查的数据全都有问题，给数据再套一层补查
检查，以及维护时自动中断查询并提醒，并在维护时查询自动告知用户服务器维护中」「貌似由于维护，
光尘商店的自动刷新也失效了，做好全盘对服务器关闭维护的应对措施」「发送失败的消息应该允许重发或
支持打开文件夹直接读取对应未发送图片进行检查」。

**先查证据（面板 stdout 日志 `dist_new/D2Query/exe_stdout.log`，10-02~10-08）**：维护期官方给的签名
有四种，全都不是「网络抖动」：

| 形态 | 实测样本 |
|---|---|
| `ErrorStatus: DestinyThrottledByGameServer`（ErrorCode **1672**） | `10-06 01:08:14` 三连：token 刷新 HTTP 400 `{"error":"server_error","error_description":"DestinyThrottledByGameServer"}`、`✘ 每日光尘商店 失败…Bungie: DestinyThrottledByGameServer`、`[sched] 预取 光尘商店 失败` |
| 非 JSON 的 HTML 错误页 | `[bind] 改名核对失败（…）：JSONDecodeError: Expecting value: line 1 column 1 (char 0)`（维护页 / 风控页） |
| `ErrorCode 5 SystemDisabled` / 1218·1233·1236·1300·1500·1643·1644·1651·1652·1688 | 官方 `PlatformErrorCodes` 里的系统级关闭码（按 `_rtest/openapi.json` 枚举核对） |
| **HTTP 200 + 空数据**（最阴的一种） | 不报错也没内容：各调用点以前各自静默退化（`{}` / `[]` / `0`），卡片照出，用户看到的是**「全是 0」的假统计** |

**四层防线（新模块 `bungie_status.py` + 数据层补查层）**：

1. **事前拦** `bst.guard()`：每个 API 请求前过一道闸。维护中直接抛 `BungieMaintenanceError`，
   **不打接口、不写缓存、不出卡片**（非维护态是纯内存读，零开销）。维护态下每 60 秒探一次
   官方 `/Platform/Settings/`（拿 `systems.Destiny2.enabled` 与官方原文），并每 60 秒**放行一个
   真实请求去核实恢复**——恢复信号只认「真实请求成功」（`note_ok`），不认状态接口正常：
   部分维护时状态接口照样活，拿它当恢复信号会立刻把刚点亮的维护态抹掉、下个请求又炸
   （写测试时真踩到，已改成放行探测）。
2. **事中判** `_resp_check()`：每个响应过一遍错误码 / HTML 判定，命中维护类即**点亮维护态**（清空
   响应缓存 + 自动中止所有在跑 / 排队的后台任务 + 面板日志留一行）；**HTTP 200 但 ErrorCode≠1 的
   响应再也不进响应缓存**（以前只看 HTTP 200 就缓存，维护期的脏响应能在 PGCR/Manifest 档里躺 6 小时）。
   纯限流（31/36/37…）单列一档：**不冻结全局**，客户端等 2 秒重试一次，仍失败就交回调用点按老口径
   退化——几千场的 PGCR 长扫描撞一下限流不该整条任务失败。
3. **补查复核**（`DataSuspiciousError`）：官方维护还有「ErrorCode==1 但 Response 整块为空」这种
   「成功但没数据」。实测真角色的 `groups=101,103,104` 一定有 `allPvP/allPvE/…` 七块，而**已删角色
   是「键在、每块空」——这必须继续当合法空**，所以「Response 整块为空」只可能是服务端没给数据。
   命中就**绕开响应缓存重取一次**，仍旧可疑就报错——宁可让用户稍后重发，也不出一份错的统计。
   覆盖：`char_stats`（复核）、`activity_history`（以前**完全不看 ErrorCode**，维护期静默当成「没有
   对局」）、`get_pgcr`、`_char_stats_full`（以前某批挂了就少算那批）、`rotation_week`（以前落
   `key=unknown` 的空缓存）、`_fetch_daily_secs`（**失败不再推进封存水位**——水位一推过去那几天就
   永久少算）、`gambit_career`、`resolve_member`（以前维护期静默变成「没找到玩家」）、`_item_def`
   （失败不再把空定义永久缓存）。光尘商店另加「空货架」守卫：`_ev_from_vendors` 一件商品都没有就
   不落缓存——缓存键 1 点才换，落下去就是**一整天都是空商店**（用户说的「光尘商店自动刷新失效」）。
4. **缓存污染判定**（`maintenance_log.json`）：点亮维护态时窗口起点取 `last_ok`（最后一次正常响应
   的时刻，比「发现维护的时刻」准——维护开始到发现之间查的数据同样有问题），恢复时闭合窗口。
   落在窗口内的缓存条目一律作废：`raid_history_cache`（按 `ts`）、`heatmap_cache`／`weapon_agg_cache`
   （按 `updated`）、`season_time_cache`（`mw` 代次 + 水位回退到窗口起点，只重拉这段，不整份重算）；
   翻页类还会「本次一场都没翻到但上次有 → 保留上次缓存不覆盖」（历史只增不减，翻空必是接口问题）。

**用户可见文案**：QQ 侧加了一道 `run_preprocessor` 维护闸门——维护中**所有碰 Bungie 的指令**统一回一张
「服务器维护中」说明卡（含官方原文 + 「不用一直重发」），纯本地数据的指令照旧可用（`/帮助` `/武器查询`
`/perk查询` `/护甲查询` `/护甲套装` `/武器筛选` `/掉落` `/解绑` `/我的` `/进度`，以及 `@机器人 武器名`
这条本地 at_lookup 通路；靠 `Matcher.state["maint_ok"]` 标记，同一条事件只提示一次）。面板侧：总览加
「Bungie 服务器状态」瓷砖 + 每一页顶部的红色维护横幅（`/api/bungie/maint`，带「重新探测」按钮），
`/轮换` `/eververse` 与全局兜底错误页都换成维护 / 数据不完整两种专用文案；后台任务被维护中止时
面板显示「已中止 · Bungie 服务器维护中」（与「管理员中止」分开，见 `_maint_abort`）。

**全盘应对定时任务**：维护中调度器 `_tick` 整轮跳过（token 续期 / 预取 / 推送都是必失败的请求，只会刷
错误），并注册 `bst.on_clear` → 维护一结束**立刻补跑一轮**（不等 30 分钟 tick）——「维护恰好在每天 1 点
的光尘刷新上、之后一整天没刷出来」就是这么补的。预取失败还会往面板日志写一行（以前只打 stdout，
用户能察觉到的只是「商店一整天是空的」）。

**发送失败的图片可查可重发**（用户第二条诉求）：卡片是现渲染现发的，QQ 侧发送失败（被动回复窗口
过期 / 掉线 / 风控）后进程里没有第二份。现在插件在发送失败的那一刻把图片落到 `unsent_images/`（文件名
带时间 + 收件人 + 场景，只留最近 120 张），日志条目带上文件名与收件人；面板消息日志里直接**显示这张
没发出去的卡的缩略图**（点开看大图 `/unsent/<文件名>`）+ 两个按钮：
- **重发**：`POST /api/bot/resend` → 把协程投到 nonebot 驱动循环上发（视图函数是同步的 → FastAPI 丢线程池，
  `fut.result()` 不卡面板自己的循环）。NapCat 走 `send_group_msg`/`send_private_msg`（base64），
  官方通道走 `send_to_group`/`send_to_c2c`（**不传 msg_id＝主动消息**，被动回复窗口早就过了）；
- **打开文件夹**：`POST /api/bot/unsent/open` → `os.startfile` 直接开资源管理器人工检查。

**顺带修的隐患**：`"onebot" in type(bot).__module__` 这个通道判据是错的——`"onebot"` 是
`"nonebot"` 的子串（适配器都在 `nonebot.adapters.*` 下），官方通道的 bot 会被一起算成 NapCat。
轮换推送（`bot_scheduler._push_rotation`）因此每次都会往官方通道也发一遍然后失败；
新写的重发分派里它直接表现为「官方通道的重发根本不发」（测试里抓到，`_rtest/resend_test.py`）。
两处都改成 `"adapters.onebot" in ...`。

**测试**：`_rtest/maint_guard_test.py` 37 项全通（假 httpx 客户端喂维护 / 限流 / 空响应，覆盖判定表、
维护中拦下且不打接口、脏响应不落缓存、自动中止任务、补查复核、窗口污染判定与水位回退、恢复回调、
未发送图片落盘与路径穿越防护）；`_rtest/resend_test.py` 16 项全通（假 bot 验 NapCat / 官方两条通道
各走对接口，含「不传 msg_id＝主动消息」与发送失败如实回错）；`_rtest/_plugin_import_check.py` 验证插件
能加载、13 条本地指令标记 `maint_ok`、14 条 Bungie 指令未误标。`test_sweep.py` 加了两项（维护态接口 /
未发送图片接口），并修掉一条**失效断言**：地牢详情页的「最近对局」标题早已挪到 `render_match_card`，
详情页共用 `render_matches` 只出对局格子（实测 24 格），原断言永远不可能通过（10-07 那次是超时掩盖了它）。
面板样式人工核过：维护横幅 / 缩略图 / 两个按钮 / 「已中止 · Bungie 维护中」都如预期
（`_rtest/_panel_shot.png`，用 8999 端口的样本实例 + 无头 Edge 截图）。

**顺带核实（不是 bug）**：现有落盘缓存**没有被维护污染**——`season_time_cache` 六角色合计与官方
`secondsPlayed` 逐项对比最大差 0.1%（Wj#8984 主号 2.0h/2187h，是未封存的最近 10 天尾巴），
`raid_history_cache`（1969/1211/1591/882 场）与 `weapon_agg_cache`（missed=0）都自洽。
所以没有做「一次性清库」，只让窗口判定管事（`maintenance_log.json` 目前为空＝无维护史）。

## 2026-10-07 `/队伍` 名单改成「此刻仍在场的人」——PGCR 是参与流水账，不是队伍

**用户报**：「/队伍 和 队伍配装 的功能还是有问题啊，刚才甚至识别出来 7 个人，你确定你不是识别的
当前对局所参与过的所有人？我要的是实时数据」——**说对了**：在活动中名单直接取本场 PGCR 的
entries，而 PGCR 记的是**本场参与过的所有人**：

- 实测（2026-10-07 真实对局，探针 `_rtest/ft_window_probe.py`）：
  - 3 人位「金星，阿蒂西亚山：破坏」一局里躺着 **6 条 entry**：harmony 2–116s / Xinatus 0–145s /
    本人 129–300s / Ciege 205–590s / antroc 79–580s / Avearri 402–661s —— 中途退的、补位进来的
    全留着，卡片会把 3 人活动列成 6 个人。
  - 6 人位自定义突袭 7 条 entry：白鹿#4533 先 41 秒退出、又重进（两条进场记录）；旧卡 7 行里
    有一个重复的人（上一条记录里靠 mid 去重盖掉一部分，但"换人"型多出来的照旧留着）。
- **判据是官方给的**：每个 entry 带对局内计时 `values.startSeconds`（进场秒）+
  `values.timePlayedSeconds`（在场秒）→ `PGCR.period + start` = 进场时刻、`+ play` = 离场时刻。
- **修法（`bot_fireteam.py` `_pgcr_roster()`）**：按在场窗口过滤名单——
  - 进行中（live）：只留「离场时刻 ≥ now − 3 分钟」的人。3 分钟是给 PGCR 分钟级延迟留的宽限
    （Bungie 慢时整份报告会滞后），宁可多留一个人几分钟，也不把还在场的队友删掉；
  - 刚打完（ended）：只留「离场时刻 ≥ 对局结束时刻」的人（最后还在队里的）；
  - 拿不到计时字段或对局起止（老报告 / 刚开局）→ 不筛，保持旧行为；
  - 同一 mid 多条：优先留**在场**的那条，同样在场再按击杀多（原来只按击杀多，会误留已离场那条）。
  - 被过滤掉的人不凭空消失：返回 `left_out` 计数，卡片抬头注一行
    「另有 N 人已中途离场（不计入名单）」，页脚写明口径「名单＝本场对局里此刻仍在场的人
    （中途离场 / 被替补换下的不计入）」。
  - **刚打完的卡不再并 transitory 可见队伍**（`_match_brief` 只在 live 时并）：出本后的"当前队伍"
    不等于上一场的队伍，往上一场名单里塞人正是当年「凑出假名单」的老坑。
  - PGCR 一律 fresh 拉（原来只有 live 绕缓存）：ended 也可能撞上"打一半时抓的残本"。
  - 进行中但名单还没发布（匹配局打一半）：卡片报官方实时人数
    「本场实时 N 人在场（官方名单还没发布，先列可见队伍成员）」（`players_now` = transitory 的
    `numberOfPlayers + numberOfOpponents`）。
- **顺带修 `/队伍配装` 的「查询」标记**：原来认 `roster[0]`，而名单按击杀排序、自己未必在第一位
  （徽章会标到队友头上）；现在数据层带 `self_mid`，`bot_loadout.collect` 按它标。
- **边界（Bungie 接口限制，不是缺实现）**：正在打的**匹配局**拿不到实时名单——实例 id 只能从
  对局历史拿，而匹配局的历史行要等活动结束才出现；所以这条路只能报「本场实时 N 人在场」。
  自定义突袭/地牢这类历史行当场就有的，走实时 PGCR（名单随队友进本补全）。
- **回归/探针**：新增 `_rtest/ft_real_time_test.py`（用上面两局真实 instance 做单测 + 渲染：
  突袭 7 条 → 名单 6 人 / 离场 0；破坏 6 条 → 结束时在场 3 人 / 离场 3 人；进行中 t=400s →
  早退超宽限者剔除、本人仍在场）、`_rtest/ft_roster_probe.py`（逐 entry 实时签名）、
  `_rtest/ft_window_probe.py`（在场窗口还原）；`_rtest/ft_states_render.py` 的「刚结束」样本
  改成从历史里自动挑最新一场突袭（原来硬编码「众神殿」，用户一换局就断言失败）。

## 2026-10-07 `/队伍` 出本瞬间被判成「在轨道」+ `/队伍配装` 去机灵改武器 perk 栏

**用户报**：「/队伍 为什么还不是实时的」——群里那张卡写「在轨道待机」，而游戏里火线面板正显示
「正在进行突袭 // 众神殿：革命暴动首领」。查下来**卡没算错，是数据本身在那个瞬间就是这个样子**：

- **时间线（面板日志 + 官方历史双向对上）**：那场自定义突袭 `05:27:32 → 05:50:17`（UTC，
  `duration` 1397s）；用户 `/队伍 Required#9992` 发在 **05:50:51**——**出本后 34 秒**。
- **实测签名（一次 20 秒粒度的 25 分钟轮询 + 5 人样本）**：
  - 轨道：204 `currentActivityHash` = **占位 82913930**（manifest 无名字），且它带的
    `dateActivityStarted` 是**上一场的开局时间**（不是回轨道的时间）；1000 的
    `currentActivity.startTime` 同样停在上一场、`numberOfPlayers` 归 0。
  - 在打（突袭/地牢/社交空间都一样）：204 = 真活动 hash，1000 的 startTime 与它**逐秒相等**。
  - **出本：204 立刻换成占位 hash** → 旧判据（`_activity_kind(hash)=="none"` 就丢掉 cur）只能落到
    「在轨道」，而此刻火线面板/游戏里还写着「正在进行突袭」，用户看到的就是"不实时"。
  - **`numberOfPlayers` 会滞后**：实测 14:22:12 某人 204 = 占位 hash、1000 `players=7` 而
    `startTime` 还是 10 分钟前高塔那次——7 人是**刚离开高塔的残留**。所以**不能**拿
    「players≥1」当"在打"的兜底，否则坐在轨道里也会被报成进行中。
- **修法（`bot_fireteam.py` 重构）**：真活动 hash 仍然是在打的唯一判据，但补两条路——
  - **「刚打完」**：真活动 hash + `players=0` + 历史行里这一场**结束在 10 分钟内** →
    直接出**已结束卡**（活动名 / 开始时间 / 对局时长 / 本场 PGCR 名单 + 该模式生涯与指标砖）。
  - **「上一场」行**：轨道 / 自由漫游 / 社交空间 / 不在线卡，抬头下补一行
    `上一场 众神殿：革命暴动首领: 自定义 · 23 分钟（13:27–13:50）`（同样 10 分钟宽限）——
    名单仍是**官方实时队伍**（当前队伍的人），不拿上一场的人冒充。
  - **刚进本兜底**：真活动 hash + `players=0` + 开局在 5 分钟内（transitory 还没跟上）→
    按 hash 出「进行中 · 本场名单还没发布」，不再掉进轨道分支。
  - 顺带：`_match_brief` / `_rows_for` 抽出复用（刚进本/进行中/刚结束三条路共用名单与取数），
    **PGCR 名单按 membershipId 去重**（自定义突袭实测同一账号因中途换号在 entries 里出现两次，
    卡片成员数 7 里有一个重复的人，见 `_rtest/_ft_ended.png`）。
  - 新增探针 `_rtest/ft_live_probe.py` / `_ft_watch.py`（20 秒粒度记录 204+1000 原始形态）/
    `_ft_check.py` / `ft_states_render.py`（状态单测：把 now 拨回那场突袭结束 +2 秒，
    `_newest_finished` 必须认出它，宽限窗口外必须不认）。

## 2026-10-07 `/队伍配装`：去掉机灵，武器列改成带名字的 perk 栏 + 与护甲列等高

用户口径：**左列留三把武器的 perk 栏，和右边 5 件护甲栏对齐**。

- 机灵（桶 4023194814）**整条从卡片与采集里去掉**（实装卡 `_member_loadout` 与游戏内配装
  `/配装 数字` 两条路都去）——原来它挤在左列第 4 行，让 3 武器 + 1 机灵 对 5 护甲永远对不齐。
- 武器芯片从「只有图标」改成 **icon + perk 名**的 `.lo-perks` 芯片排（`_lo_item_html(perks=True)`），
  护甲仍是图标排（模组名太长，保持原样）。
- 布局：`.lo-grid` 的 `align-items:start` → `stretch`，武器列的 `.lo-item` 加 `flex:1`——
  三块武器平分左列高度、perk 栏贴底，**与右列 5 件护甲底部齐平**（渲染核对过
  `_rtest/_loadout_new.png`）。

## 2026-10-07 fix(生涯武器): 全生涯只统计到 2023-07 —— 老汇总的假边界

用户报「我的全生涯武器为什么只到 23 年」。查下来是**汇总缓存的锅**，不是接口不给：

- **现象**：`weapon_agg_cache.json` 里 `…|pve|all` 是「3014 场，2023-07-06 → 今天」，
  而同一账号直接翻历史能翻到 **8559 场、2019-11-13**（探针实测，逐页打印过）。
- **成因**：`3014 = 3000 + 14`。最早那次全生涯是在 `pve_match_cap` 还是内置默认 3000 时跑的，
  被上限截断在 3000 场（正好停在 2023-07-06）；后来用户把上限调到 20000，可**汇总缓存只按
  「范围」复用、不看「上限」**，于是每次查都走增量路径——只把新对局累加上去，
  那个 2023-07-06 的假边界就一直被沿用下来。上限调大对它完全无效。
- **修法**：缓存里记下当时的 `cap`；复用前先判断这份老汇总可不可信——上限调大过、或者这份
  缓存写于 2026-10-07 之前（当年没记录 cap/capped，判断不了完整性），就**丢掉它整段重算**。
  不采用「只往前补拉更早那段」的原因：截断是**按角色顺序停的**，中间那一段（后两个角色的
  对局）也可能整块没统计到，只补头部会留下一个看不见的窟窿——实测这么补出来是 6619 场，
  而真实的 PVE 全生涯是 8559 场。整段重算的代价很小：逐场明细有 `_PVP_MATCH_CACHE` 兜底，
  重算主要是重新翻一遍活动历史（约 1–2 分钟）+ 补拉从没拉过的那几千场明细。
  重算一次就把 `cap`/`capped` 写回去，之后恢复正常的增量路径，不会每次都重算。
- 顺带一处收紧：`_sub_agg_segments` 不再拿「没记录 cap 的段」当子段折（判断不了完整性，
  折进来可能少算）。

自测：`_rtest/job_engine_test.py` 新增 [13] 组用例（上限调大后往前补拉 / 上限没变不重复补拉 /
升级前留下的无 cap 老缓存也会补一次），全绿。

## 2026-10-07 feat+fix(后台任务): 并行多任务 · 逐条暂停/中止/重跑 · 分段计时 · 跨范围缓存复用 · 「在跑却报失败」

用户一次报了六件事，外加一条现场故障，全部落地：

- **「后台明明在跑却告诉用户失败」**（现场故障，最高优先级）：`nonebot_plugins/destiny2.py`
  的 `_wait_job` 写死 **20 分钟墙钟上限**，而生涯任务排队时排在后面的等 20 分钟以上是常态
  ——超时返回未完成的任务字典，`_jobs_card` 见 `status != "done"` 就报「统计没跑完，稍后再试」。
  用户看到的 4 张「PVE 武器使用失败」全是还在队列里正常等待的任务。现在只有**任务从 JOBS
  里消失**才算失败，排队/运行/被暂停一律继续等（安全上限 6 小时，只防协程挂死）；
  被管理员中止、还在统计中各有自己的说法，不再一律报失败。
- **多任务并行**：队列原来是**一条队逐个跑**。现在按「并行任务数」开槽（默认 2，面板可调
  1–4，1 = 回到串行），关键是几个任务**共用同一个逐场 PGCR 并发闸门**（`_pgcr_sem()`，
  容量取「并发上限」设置），所以并行只是把几个人的等待重叠起来，**总请求量不会超速**。
  闸门按事件循环分开存：webui 与 QQ bot 各有一条 loop，asyncio 信号量跨循环用会报
  「bound to a different event loop」。
- **逐条控制**：每条任务可**暂停 / 继续 / 中止 / 重跑**（面板条目上的按钮 → `/api/bot/jobs/control`）。
  暂停走**轮询检查点**（`_job_checkpoint`，0.5 秒一眼），刻意不用 `asyncio.Event`——任务可能跑在
  webui 的循环上而暂停来自 QQ bot 那条循环（或反过来），跨线程 `set()` 不是线程安全的；
  中止用 `loop.call_soon_threadsafe(task.cancel)`，同理。被暂停/中止的任务**不占并行槽位**，
  队列会自动把后面的任务提上来；中止时把已经拉到的逐场明细**落盘**，重跑直接复用。
- **分段计时**：面板原来只有一个 `elapsed`（排队中显示的是「从发起到现在」，语义混乱）。
  现在拆成 `queued_s` / `run_s` / `total_s`：排队中显示「已排队 X」，运行中单列「已跑 Y」
  并把排队时长括起来，**已完成的也留用时**（原来是空白）。耗时过小时不再显示成「95分3秒」。
- **跨范围缓存复用**：原来只有「同一玩家同一范围再查」走增量补拉，先查 S27 再查全生涯
  仍要把那段时间的活动历史重翻一遍。现在把**已经整段落在本次范围内、已经结束、且没被
  场次上限截断**的其它范围缓存直接折进来（`_sub_agg_segments` / `_fold_segments`），
  只枚举剩下的空档（`_gap_windows`，相邻段错开一天，边界那天不会算两遍）。还在进行的段
  （如「当前赛季」）不折——它每天都有新对局，折进来会漏掉查询之后打的那几场。
- **发送超时自查**：`[发送超时·多半已送达]` 原来只是**猜**。现在超时会翻一次消息历史
  （`get_group_msg_history` / `get_friend_msg_history`，只看最近 180 秒、只认机器人自己发的）
  核实这条到底进没进群，日志分成「**已核实进群** / 未查到，建议重发 / 查不了」三种；
  掉线、风控期间消息根本没出去，不查。**绝不重发**（重发就是同一条消息两张）。
- **排队位次实时播报**：原来只有发「统计中」卡片那一下提一句位次。现在 `_wait_job` 每轮把
  状态喂给回调，**位次一变、轮到开跑、被暂停/恢复/中止**各补一条短提示（位次挪动限流 45 秒，
  关键变化不受限流）。

自测：`_rtest/job_engine_test.py`（新增，12 组用例：并行槽位 / 分段计时 / 中止排队与运行 /
暂停与继续 / 暂停不占槽 / 重跑 / 跨范围折入与空档切分 / 并行数钳制）全绿；
`_rtest/job_dedup_test.py`、`_rtest/heat_cache_test.py` 回归全绿。

## 2026-10-07 fix(战绩卡顶部 / NapCat 掉线): 顶部不再压纹章方图；修「脚本退出误杀 NapCat」+ 掉线自动拉起

用户报：战绩卡顶部的纹章方图把整条名片挡住了，只保留完整名片即可；顺带查 NapCat 老掉线。

- **战绩卡顶部**（`webui.render_match_card`，`bot_cards.mode_card` 复用它 → 机器人侧同样生效）：
  顶部一直是「整条名片底图（emblemBackgroundPath 474×96）+ 叠一张 96×96 纹章方图
  （emblemPath）」，方图正好盖住名片左半边。现在只铺整条名片、去掉方图（`.hero .hm`
  与其 CSS 一并删掉），名字/标题仍叠在名片上。**只动这一处**：/生涯 的名牌与分职业条、
  /玩家 与网站总览的角色行都保持原样（用户：生涯原来的就挺好）。
- **NapCat 老掉线，两个原因**：
  - **脚本退出会误杀 NapCat**：`napcat_runtime` 被 `webui` 导入，而它在 `atexit` 上挂了
    「退出时带走 NapCat」——早期实现不看归属直接 `stop()`，于是任何 import 过 webui 的
    Python 进程（渲染自测 `_rtest/*.py`、临时探针）一退出就把正在跑的 NapCat 连根杀掉，
    机器人随即"离线"。实测吻合到秒：脚本 07:22:30 出图、07:22:32 反连就断（08:12:24、
    09:42:09 同样）。现在加了归属标记 `_owner`：只有认领过 NapCat 的进程（面板/启动器
    调过 `start()`）才在退出时带走它。
  - **崩了不一定自己回来**：2026-10-07 10:07:58 NapCat 进程树崩掉后 WebUI 6099 无响应、
    反向 WS 也回不来，机器人一直"离线"到人工重启（这次挂了一个多小时）。新增看门狗
    `napcat_runtime.watch_loop`（启动器随自动重连一起拉起）：每分钟查一次 8901 上的
    OneBot 反连，连续 3 分钟没有且 NapCat 已不在（WebUI 无响应）就按面板存的 uin 快速
    登录重新拉起；过程写进面板「任务与日志」。`start()` 自带 300 秒冷却，不会频繁登录
    触发 QQ 风控。
  - 顺带把内置 NapCat 4.18.28 → **4.18.33**：本机 QQ 是 9.9.36-53644，旧版的启动日志一直
    在报「当前版本Appid未内置 / NativePacketClient 未找到对应版本的偏移数据 / PacketBackend
    不支持当前QQ版本架构」，新版支持表里已有 9.9.36 系列。旧文件备份在
    `napcat_shell/backup_4.18.28/`（仓库与 dist_new 两份都更新）。

## 2026-10-07 fix+feat(智谋 / PvE / 面板): 智谋恢复胜点图并补荧光·入侵；PvE 去掉近期战绩、加终极征服与现有成就分；日志改实时轮询

用户逐条报的问题：①智谋的胜点图被上一轮改版一起删掉了（只让删 PvP 的）；②胜点图画多少场
也该能调；③智谋数据不该照抄 PvP——智谋有荧光和入侵；④PvE 里「打过 N 局」多余；⑤PvE 缺
终极征服；⑥PvE 摆「近期战绩」没用；⑦成就分只显示生涯累计，还应有现有成就分；⑧面板日志
要切标签页才刷新。逐条改完：

- **智谋卡**（webui.render_match_card + destiny_data）：
  - 顶部改成官方 gambit 桶（**必须带 `modes=63` 请求**，不然响应里根本没有 `pvecomp_gambit`
    ——只给 groups 时只有 allPvP/allPvE/raid… 那几个键）：生涯统计（场次 / 胜率 / K/D /
    场均击杀 / 时长）+「荧光」（存入 / 拾取 / 截夺 / 丢失）+「入侵」（入侵次数 / 入侵击杀 /
    击败入侵者 / 被入侵者击败 / 原始使者击杀 / 高分目标击杀）。官方 gambit 桶实测**完整**
    （Wj#8984 三角色 285+4+109 = 398 = 对局历史去重 398 场，逐角色相等），所以不像 PvP 那样
    得绕历史聚合。
  - 「近期战绩」里给智谋换掉「平均效率」，改成**存入荧光**：对局历史的 `score` 就是存入荧光
    （120 场逐场与 PGCR `motesDeposited` 相等、生涯 8,075 = 官方 8,075，零额外请求）。
  - **胜点图回来了**（`render_matches(grid=...)` 改成传对局列表而不是 bool），数量可在
    「运行状态 / 参数设置」页调：`gambit_grid_count`（默认 **100**，0 = 不画）、
    `pvp_grid_count`（默认 **0**，PvP 那一片格子还是太吵）、新增 `GET/POST /api/settings/grid`；
    点子图比窗口长时会自动多翻几页历史（`fetch_n = max(窗口, 格子数)`）。副本详情页的
    点图保持原样。智谋窗口局数也独立成 `gambit_recent_count`（以前跟着 PvE 的设置走）。
- **PvE 卡**：删掉「近期战绩」数字条（用户：根本没用），终局通关去掉「打过 N 局」小字，
  改成 c6 一行六格并补 **终极征服**（对局历史里 `终极征服*` 的完成数 + 记录里的本季进度
  x/y，如 4 / 本赛季 0/2）；生涯概况里成就分拆成**现有成就分**（`profileRecords.score`，
  游戏内当前凯旋分）与副标题「生涯累计」（`lifetimeScore`，含已过期传承分）。
- **/pve 的终局通关数不再吃「生涯武器场次上限」**：它只翻历史页、不逐场拉 PGCR，按上限
  截断会把老记录直接数丢（实测截到 3000 场/角色时 Wj#8984 的大师日落从 30 变 **0**、
  突袭 460→401）；现在默认翻全生涯（实测 11,823 场 / 10 秒，命中 45 秒 HTTP 缓存后秒回），
  上限设置里补了说明。
- **面板日志实时化**：以前只在切标签页时拉一次 `/api/bot/logs`，群里来消息面板不动；现在
  「任务与日志」页可见时每 2.5s 轮询，内容真变了才重绘（保住滚动位置、不重建群筛选下拉框）。
- README 与插件头注释同步；`test_sweep.py` 断言更新（智谋必须含 荧光 / 入侵 / 胜点图，
  PvE 不得出现「近期战绩」与「打过 」）。

## 2026-10-07 feat(/pve): 面板改成 raid.report 式（时长/成就分/总击杀 + 突袭·地牢·宗师·大师日落 + 镀金征服者）

用户给了 raid.report 的个人页截图，要求 `/pve` 别再摆 PvP 那套参数，改成像它那样显示
raid 通关数、宗师日落、征服者镀金数。口径逐个核对过：

- **突袭 / 地牢通关数**：官方统计只有 mode 4 / 82 的聚合数，且与 raid.report 对不上；
  改成从对局历史里数**完成的对局**（同一局换角色重进按 instanceId 去重，只算一次）。
- **宗师 / 大师日落**：官方没有分难度桶，只能按活动名分类（`宗师日落: X` / `日落: 宗师`
  / `日落: 大师`），实测 Wj#8984 大师日落 30 与 raid.report 完全一致、突袭 460（去重后，
  raid.report 536 是**没去重**的口径——同一局换角色重进会被算两次）。
- **镀金征服者**：`征服者` 称号镀金记录（`titleInfo.gildingTrackingRecordHash`）的
  `completedCount` = 历史累计镀金次数（实测 4，与 raid.report 一致）；objectives 里的
  progress/completionValue 只是**本季**进度（0/4），别拿它当次数。
- **成就分**：`profileRecords.data.lifetimeScore`（实测 84,592，与 raid.report 一致；
  注意分数和 records 都嵌在 `profileRecords.data` 里，读顶层会拿到 None）。
- **游戏时长**：官方 allPvE 的 `secondsPlayed`（2,630 小时，= raid.report 的 TIME PLAYED）；
  三角色总时长（4,043 小时，含 PvP/轨道）放进副标题。

- **数据层**：`mode_report(..., endgame=True)` 翻全生涯 PvE 历史（受 `pve_match_cap` 约束），
  新增 `_pve_endgame()`（突袭/地牢/宗师/大师日落通关数 + 打过的局数）、`_profile_extras()`
  （成就分 + 镀金次数，一次 components=900）、`_conqueror_gild_hash()`、`_is_gm_nightfall()`、
  `_is_master_nightfall()`；rep 新增 `playtime_hours` / `triumph` / `gilds` / `endgame`。
- **渲染层**：`_pve_chips()` → 「生涯概况」（c4：游戏时长 / 成就分 / 总击杀 / 精准击杀）
  + 「终局通关」（c5：突袭 / 地牢 / 宗师日落 / 大师日落 / 镀金征服者，副标题写"打过 N 局"），
  替换原来的官方参数块；`_official_chips()` 退居 智谋 等没有专属面板的场景。
- 代价：`/pve` 现在要翻全生涯 PvE 历史（Wj 这个号 12,895 场 / 约 8~13 秒，命中 45 秒
  HTTP 缓存后秒回）；想更快就在「运行状态」页把 PvE 生涯统计场次上限调小。
- `test_sweep.py` 的 PVE 断言改为必须含「生涯概况 / 终局通关 / 突袭通关 / 成就分」且不得
  出现「模式细分」；README 同步。

## 2026-10-07 feat(/pvp /pve 战绩卡): 顶部生涯统计改「全模式」、近期窗口可调、去掉红绿点图

起因是排查「/pvp 数据是不是不全」：卡片上的数字没算错（官方账号级 merged 与手工按角色
求和逐位一致），**不全的是 Bungie 自己的生涯统计接口**——`GetHistoricalStats` 对 2023 年
之后的试炼、铁旗完全不记（只保留 2020 年那批）。实测 Wj#8984 三角色分解逐项对上：
官方 allPvP 759 场 vs 对局历史 1338 场，差 579 = 试炼 454（历史 593 / 官方 139）
+ 铁旗 120（历史 154 / 官方 34）+ 快雀竞速 5。对局历史才是真实场次（PGCR 逐场核对过
归属、instanceId 无重复），于是顶部生涯统计改用**对局历史聚合**。

- **数据层**（destiny_data.py）：`mode_report(name, mode, count=0, career=False)`——
  `career=True` 时按 `match_cap("pvp")` 翻完每角色对局历史（250 场/页，同角色 4 页并发、
  跨角色 6 路限流），跨角色按 instance 去重后聚合出 `rep["career"]`（被上限截断时
  `capped=True`，卡片上注明）。新增 `recent_count("pvp"/"pve")`：近期窗口局数读
  `bot_config.json` 的 `pvp_recent_count` / `pve_recent_count`（默认 100，口径是**跨角色合并后**
  的最近 N 局——设定 300 就是全账号最近 300 局，不是每角色各 300 局）。
  聚合逻辑抽成 `_agg_matches()`，「近期」与「生涯」共用同一套口径；`_sum()` 补
  `secondsPlayed` / `bestSingleGameKills` / `longestKillSpree`（求和 / 取最大）。
- **/pvp 顶部**：全模式生涯统计（场次 / 胜率 / K-D / KDA / 场均击杀 / 时长 / 击杀 / 死亡 /
  协助 / 对手击杀 / 场均效率），跨角色去重、含试炼 / 铁旗 / 快雀竞速；不再用官方 allPvP
  （会少算一半以上）。
- **/pve 顶部**：官方 allPvE（击杀 / 死亡 / K-D / 场次 / 生涯时长 / 单场最高击杀 /
  精准击杀 / 协助），近期块换成通关率 / 场均击杀 / 击杀死亡 / 效率 / 时长 / 完成数——
  **PvE 不再列模式细分**（胜率-K/D 按模式比是竞技口径），最近对局列也换成
  击杀 / 死亡·协助。
- **排版**（webui.render_match_card 重做）：顶部改成玩家名片横幅（**按 474×96 名片原比例
  整张显示，不裁切不拉伸** + 64px 纹章 + 34px 名字，数据来自本次已经拉过的 GetProfile，
  无额外请求）+ 数字 chip 网格（对齐 /常用武器 卡的 chip/表格样式）+ 紧凑单行对局列表
  （52×30 缩略图，一行一场，带表头）；**取消红绿点图**（`render_matches(grid=False)`，
  副本详情页保留）；整卡从 2723px 压到 ~1500px（-45%）。
- **模式细分表**：场次加占比条，行高与武器卡对齐；PvP / 智谋仍显示，PvE 不显示。
- **后台可调**：「运行状态」页与面板「参数设置」新增**近期战绩局数**（PvP / PvE 各一个
  下拉，默认 100，可 200/300/500/1000 或自定义），新增
  `GET/POST /api/settings/recent`；保存即生效，只影响之后发起的查询。
- 面板 `/card?mode=pvp` 同步走 career 路径；`test_sweep.py` 断言随之调整：PVP 卡必须
  含「全模式 · 跨角色去重」且**不得出现 `class='gridline'`**（点图已撤），PVE 卡**不得出现
  「模式细分」**；README 与插件头注释同步。

## 2026-10-07 docs(帮助清单): /帮助 补齐到「除刻意隐藏的三条外全量」

- 起因：核对 `/帮助` 是否含全部指令——脚本按注册表（34 条 `on_command`）逐个匹配，
  发现**图片卡 `bot_cards.help_card()` 缺 /登录 /回调 /仓库 /队伍配装**（正是最近那次
  「多用户授权 + 游戏内配装 + 仓库搜索」提交新增的，卡片清单没跟上），纯文本兜底
  `HELP_PLAIN` 则缺 /进度 /武器筛选 /掉落。
- **按用户要求，/登录 /回调 /仓库 三条暂不进任何用户可见清单**（群友自助授权还在小范围
  试用，先不宣传）；指令本身照旧可用。`/配装 数字` 依赖 /登录，也一并从清单里去掉。
- 于是三处清单对齐为「全量 − 这 3 条」共 30 条：
  - `bot_cards.help_card()`：玩家分类补 `/队伍配装`（公开数据路径，不需要授权）；
  - `HELP_PLAIN`：去掉 /登录 /仓库 /配装数字，补上 /武器筛选 /掉落 /进度；
  - README：战绩表补 `/队伍配装` 行；「词条语言」段里顺手加的 `/仓库` 示例去掉。
- 另外三处清单面核对过不需要动：QQ 官方预设面板（20 项上限，本就没有这三条）、
  单聊菜单、面板首页指令行。插件头注释补了一句「这三条是刻意隐藏、要公开时三处一起补」，
  免得以后有人当成漏写又加回去。

## 2026-10-07 fix(钩子 + API 路径): 两个「静默失效」的老 bug

- **① `on_bot_connect` 钩子从没生效过**（每次 NapCat 重连都刷一段 ERROR）：
  `bot_runtime.py` 的 `_sched_attach` 是**同步函数**，NoneBot 对同步钩子走
  `run_sync`（`nonebot/dependencies/__init__.py` → `anyio.to_thread.run_sync`）丢进工作线程执行，
  工作线程里没有正在运行的事件循环 → `asyncio.get_running_loop()` 抛
  `RuntimeError: no running event loop`，被 nonebot 的 catch 打成
  `Error when running WebSocketConnection hook`。功能上一直被 `on_startup` 那条异步钩子
  兜住（每日预取照跑），所以没暴露；代价是日志被假 ERROR 污染 + 「重连兜底」是假的
  （哪天 on_startup 没跑到，token 保活/轮换推送会静默停摆）。改法：`async def _sched_attach(bot)`。
  回归测试 `_rtest/test_bot_hook.py` 直接复现 nonebot 的调用链，证明「同步写法必炸且挂不上循环、
  async 写法才生效」；重启后日志 0 条 ERROR，`[sched] Bungie token 已续期` 证明调度器真挂上了。
- **② `GetMembershipsById` 两处漏了 `/Platform` 前缀**（`destiny_data.py:1777` / `:2513`）：
  `client()` 的 base_url 是 `https://www.bungie.net`，路径不写 `/Platform` 就打到网站 404 页面
  （实测返回 `\ufeff<!DOCTYPE html>…404 Page`），`r.json()` 抛
  `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。两处都被 `except` 吞掉，于是：
  **/raid /地牢 的「跨存档全家桶」静默失效**（只算主平台场次，注释里 87 vs 86 那笔账再也合不上）、
  **每日改名核对 100% 失败**（日志里累计 723 次 `[bind] 改名核对失败`）。加前缀后实测返回 200 JSON，
  部署目录 21 个绑定逐个核验：21/21 一致、0 失败（无人处于待改名状态，所以不会写回任何东西）；
  Wj 这个账号下确实挂着 Steam + 另一个平台的成员，跨存档合并有实际意义。
- 全仓 26 处 `client().get/post` 路径一起扫过，只有这两处漏前缀，其余都带 `/Platform/`。

## 2026-10-07 feat(查询词条): 所有按名字查的功能认英文名与台服繁体名

- **背景**：`weapons_full.json` / `perks.json` / `activities.json` / `armor_sets.json` / `item_zh.json`
  全部出自 **zh-chs** manifest，玩家拿英文名（`Fatebringer`）或台服繁体名（`龍之氣息`、`手持加農砲`）
  来查一律空手而归；`/护甲查询` 是唯一有 `en` 字段的入口，`/掉落` 只认 `ce`/`ron` 这类缩写。
  **指令触发词保持中文不变**，这次只让「查什么名字」这层认英文与繁体。
- **不做繁简字形转换**：台服叫法是词形差异（克洛塔/克羅塔、突袭/掠夺、手炮/手持加農砲），字形转换对不上，
  所以直接采 Bungie 官方 `en` / `zh-cht` 名字建索引——`build_locale_index.py` 下载
  `DestinyInventoryItemLiteDefinition(zh-cht)` + `DestinySandboxPerkDefinition` + `DestinyActivityDefinition`
  + `DestinyEquipableItemSetDefinition`（en/zh-cht），按 **同一个 hash** 把三语名对齐，产出两份紧凑索引：
  ① `name_i18n.json`（`wname` 武器 hash→[英文,繁体]、`weapons`/`perks`/`activities` 三语名倒排、
  `terms` 筛选词表 2430 条、`sets` 套装 109 条、`charts` 掉落表副本名 35 条，922KB）；
  ② `item_cht.json` 繁体物品名→hash（17846 条，与已有 `item_en.json` 同构，843KB）。
  下载原始定义（65MB 物品 + 活动/perk，共 ~94MB）留在 `manifest_index/raw_*` 作缓存，
  **已在 D2Query.spec `_MI_SKIP` 里排除，不进包**。
- **运行时**新增 `name_i18n.py`（不 import destiny_data，自带 `_idx_file` 三级定位；缺索引文件全部静默
  降级成空结果/原词）：`match_weapons`/`match_perks`/`set_name`/`chart_key`/`translate`/`item_hashes`/
  `name_hit`/`matched_name`。接进**全部按名字查的入口**，且都在**中文原路径没命中之后**才兜底，原有行为零改动：
  `/武器查询`、`@机器人 直查`、`/perk查询`、`/护甲查询`（繁体名走 item_cht→hash）、`/护甲套装`、
  `/掉落`（`Crota's End`、`國王的殞落`、`Vault of Glass`）、`/仓库`（`Vex Mythoclast`、`威寇斯破神者`）、
  `/武器筛选`（`Hand Cannon`、`脈衝步槍`、`adaptive frame` 这类多词连读也认）、面板图鉴 `/catalog`
  与 `/api/suggest`、面板单武器校准。`/武器筛选` 与图鉴索引 `weapon_filter_index.json` /
  `weapon_catalog.json` 各加 `en`/`cht` 两个字段（构脚本从 `name_i18n.json` 的 `wname` 读，缺索引时留空不报错）。
- **卡片上对号**：用英文/繁体名查出来的武器，副标题带出你输的那个名字
  （`手炮 · Fatebringer · 能量武器`），避免"我查 Fatebringer 怎么出了个中文名"的错位感。
- **面板**：`/catalog` 注入的别名表改成 `{**terms, **SYNONYM}`（英文/繁体词先查，再查社区叫法），
  前端 `syncToks` 加多词连读（`hand cannon`/`adaptive frame` 拆开逐词都不成立），haystack 补 `en`/`cht`。
- **构建顺序**：`build_locale_index.py` 刻意**不读** `weapon_filter_index.json`（锻造来源词表直接读
  `pattern_groups.json`），免得两个索引脚本互相依赖；顺序固定为
  `build_weapon_details` → `enrich_weapons_ci` → **`build_locale_index`** → `build_weapon_filter_index`
  → `build_weapon_catalog`。
- **实测**（`_rtest/test_i18n_smoke.py` 56 项 + `test_i18n_web.py` 11 项 + `test_i18n_plugin.py` 全绿）：
  `Fatebringer`/`龍之氣息`/`宿命使者`/`加拉尔号角` 同出一张卡；`Incandescent`→辉耀炽热；
  `Seventh Seraph`/`第七熾天使`/`Nezarec`→对应套装；18 张掉落图英文繁体名全对上；
  `/武器筛选 Hand Cannon exotic` 12 把、`脈衝步槍 烈日` 26 把、`手炮`（中文老路径）202 把不变。
- 顺带把 README 里「外置副本改完不用重打包」的旧说法改正：`bot_cards` / `weapon_filter` 等在 spec
  `hiddenimports` 里，运行时 FrozenImporter 优先，**改这些必须重打包**（deploy_exe.ps1 里早有实证注释）。

## 2026-10-07 feat(数据管理): 点刷新自动拉起 light.gg 通道 + 真进度条

- **点「全库刷新/只补缺失/校准」不再只弹「没有可用的 light.gg 通道」**：`weapon_usage.ensure_channel()`
  用独立调试 profile（默认 `F:\edge_debug_profile`，可用 `D2_EDGE_DEBUG_PROFILE` 覆盖；没有则读
  start_edge_debug.bat 里的 `EDGE_PROFILE=`）自己起一个 Edge 实例带 `--remote-debugging-port=9222`，
  端口就绪后照旧走 CDP 抓取。**全程不 taskkill、不关用户正在用的 Edge**——独立 user-data-dir 的第二个
  Edge 跟正常 Edge 并存，实测端口 2 秒就绪；拉起成功后顺手清掉 CDP 的 10 分钟失败冷却，群指令立刻
  能重新走 CDP。拉起过程放进刷新任务里（状态卡显示「正在启动调试浏览器…」），HTTP 请求不会卡 45 秒；
  只有「连 msedge.exe 都找不到」才当场报错让人走 bat 兜底。
- 面板新增「启动通道」按钮 + `POST /api/usage/channel/start`（不刷新、只想先把通道打开时用）；
  离线提示文案改成「点『全库刷新』会自动拉起调试 Edge」。
- **真进度条**：抓取阶段显示 `百分比 · done/total（成功 N · 失败 M）` + `剩余约 X 分钟`（后端新增
  `rate`/`eta_s` 字段，消息里的重复计数去掉）；起通道/连浏览器/等人机验证的准备阶段走不定进度滚动条
  +「准备中…」；撞上 Cloudflare 人机验证时把调试 Edge 窗口置前并在面板提示要手点一下。
- 顺带修的三个老问题：①`stop` 标志跑完不重置——点过一次「停止」之后，每次刷新都会立刻"完成"
  （0 条）；②「只补缺失」用 int 键去比字符串键的快照，永远等于全库重抓（现在真只补缺的 538 条，
  确认弹窗会写明这批多是 light.gg 本就没有统计的武器——异域/固定词条/老随机掉落，补完多半仍是
  「失败」，属正常）；③刷新中断/失败时进度百分比归零（现在如实显示 done/total）。
- `start_edge_debug.bat` 改成非破坏式兜底：先探 9222（在线就直接退出什么都不动），只关占用调试
  profile 的 Edge 进程；真起不来才提示「按任意键强制重启」（那时才会 taskkill 所有 Edge）。该脚本
  已加进 deploy_exe.ps1 的外置清单（漏同步会让 exe 旁的兜底脚本与 EDGE_PROFILE 解析脱节）。

## 2026-10-07 feat(后台UI): 后端管理重做为标签式管理台

- **旧版问题**：面板把 连接状态/运行状态(iframe)/数据缓存/QQ登录/Bungie授权/消息日志/
  生效群聊/使用率数据/指令说明 九块内容摞成一列 + 右侧两块，找什么都要滚很久，
  运行状态用 details+iframe 嵌套很别扭。
- **重做为六个标签页**（客户端切换，记住上次所在页）：
  总览（QQ连接/NapCat/Bungie/数据概况 四块状态瓷砖 + 各处轮询顺带刷新 + 快捷跳转按钮）、
  登录与授权（NapCat 扫码 + Bungie OAuth）、任务与日志（后台任务与消息日志双栏）、
  群与绑定（生效群聊 + 账号绑定双栏）、数据管理（light.gg 使用率 + 数据与缓存）、
  参数设置（CPU/内存/网络资源瓷砖 + 并发上限 + 生涯统计场次上限，原生页面，
  废弃 iframe 嵌入）。轮询只在对应标签页可见时才刷新重型内容（二维码/资源采样）。
- 旧 /runtime 路由保留（?embed=1 不带导航），收藏夹直链不受影响。

## 2026-10-07 feat(界面整合): 导航收敛为 查询站/后端管理 两栏 + 后端管理中心

- **导航从 8 个按钮收敛为 2 个栏目**：查询站（原玩家查询首页，内部标签本就覆盖
  总览/PVP/PVE/智谋/战绩/Raid/地牢/生涯武器/宗师/热力图/称号/锻造）+ 后端管理
  （原 Bot 面板）。武器图鉴 / Perk查询 / 光尘商店 / 本周轮换 / 护甲套装 不再占导航，
  入口改挂在查询站首页的链接行；运行状态页从导航移除，改为后端管理里的折叠卡片
  （iframe 内嵌 /runtime?embed=1，页面本体与全部路由保留，收藏夹直链不受影响）。
  资料页顶部导航点亮「查询站」。
- **后端管理新增「数据与缓存」卡片**：列出 15 项管线本地缓存（light.gg 使用率契约、
  生涯武器汇总/明细、团本地牢历史/PGCR、raidreport、宗师、热力图、失落Sector、
  光尘、轮换、赛季、兜售者、玩家查询记录、图标缓存），显示体积/更新时间/重建代价
  （低/中/高），逐项一键清除（确认后删除，需要时自动重建）；绝不含绑定表/token/配置。
  缓存定位 cwd 优先、exe 目录兜底，打包版同样可用。
- 新端点：GET /api/backend/caches、POST /api/backend/cache/clear。

## 2026-10-07 feat(生涯统计): PVP/PVE 场次上限可配置（运行状态页，支持无限制全生涯）

- **背景**：/pvp生涯武器 逐场统计默认封顶 2000 场、/pve生涯武器 3000 场（PVP_MATCH_CAP /
  PVE_MATCH_CAP 常量，防止十年老号把逐场 PGCR 拉取拖成几十分钟），全生涯大号会被截断。
- **运行状态页新增「生涯统计场次上限」设置**：PvP / PvE 各一个下拉（默认 / 5000 /
  10000 / 20000 / 无限制），存 bot_config.json 的 `pvp_match_cap` / `pve_match_cap`；
  未设键 = 内置默认，**0 = 无限制**（统计全部可读生涯，仍受 Bungie 接口每角色
  60 页 × 250 场的可读历史硬顶）。保存即生效，只对之后发起的任务生效；面板保存时
  回显当前生效值。
- `_collect_matches` 对 cap<=0 按无限处理（翻满 60 页或翻空为止），结果里的
  `capped` 标记与卡片「已达逐场统计上限」提示带上实际 cap 值并提示去运行状态页调整；
  QQ 卡片与网页共用 render_wpvp，一处改动两处生效。

## 2026-10-07 fix(武器卡片): light.gg 组合使用率前导零 bug + 面板全库刷新/单武器校准

- **热门组合使用率放大 100 倍的根因**：light.gg 的 combo 百分比有省略前导零的写法
  （`.88% of Rolls` = 0.88%），组合/大师杰作解析用的 `(\d{1,3}...)` 正则不认 `.88`，
  把它匹配成 88%——冷门组合（真实值 <1%）全部放大 100 倍，降序排序后反而霸榜
  （散射信号「丰盈满溢+柔缓 85%」实为 0.85%，榜首本该是「丰盈满溢+受控连射 43.9%」）。
  改为 `\d*\.?\d+` + `_pct_num` 补零（combo、大师杰作、DOM 兜底启发式三处同修），
  已对 light.gg 实页 HTML 回归验证：榜首组合与单特性占比交叉吻合（49.5%×87.2%≈43.2%）。
- **面板新增「武器使用率数据」卡片**（Bot 面板）：显示快照/目标/缺失/缓存条数、
  最新数据日期与调试 Edge（9222 CDP 通道）在线状态；按钮「全库刷新（最新数据）」
  「只补缺失」「停止」，进度条实时显示 抓取中 n/N · 成功/失败 · ETA（每 25 条落盘一次，
  中途崩溃保留进度）。爬取引擎常驻 weapon_usage.py（复用 build_weapon_usage_fast 的
  CDP + 页内 fetch 套路，3 worker 错峰），挑战页自动重开页面等放行。
- **单武器校准**：面板输入武器名（支持模糊）/ hash → 只重抓该武器，几秒完。
- **快照双副本读写**：刷新产物写 exe 旁 `manifest_index/weapon_usage_snapshot.json`
  （cwd），读取时 cwd 副本优先于打包捆绑副本——打包版运行时刷新立即生效，不用重打包。
  刷新完成/启动时自动失效派生契约缓存（weapon_usage_cache.json，含负缓存）。

## 2026-10-07 /登录 多用户授权 + /配装数字 + /仓库搜索

- **公网隧道自动回跳（小日向同款单链接登录）**：仓库根放一份 cloudflared.exe（免费临时
  隧道，免安装免注册），/登录 时按需拉起 `cloudflared tunnel --url http://127.0.0.1:8903`
  → 得到 https://xxx.trycloudflare.com 公网地址，授权链接把回跳指过去——**任意网络的
  设备点完「允许」直接落回 bot 自动绑定**，无需粘贴。隧道转发的本机口（8903，只听
  127.0.0.1）与 TLS 口一样只挂 bungie_tls_app 单个回调路由；flow 记住每次授权的
  redirect_uri，回调经隧道进来本机看到 127.0.0.1 也不会错配。隧道域名随机且只在
  /登录 时按需常驻；没放 cloudflared.exe 或起不来时自动退回 局域网链接 + /回调 粘贴。
  实测公网链路（边缘→隧道→本机回调）200 全通。注意本机若 DNS 解析不了
  trycloudflare.com（实测这台机器 getaddrinfo 失败、nslookup 正常，疑似加速器 NRPT
  残留），不影响远程用户打开，只影响本机自己点隧道链接。
- **局域网设备免粘贴**：/登录 附第二条回跳指向本机内网 IP（lan_redirect_origin，
  UDP connect 探测）的授权链接——同一 Wi-Fi 的设备点完「允许」直接落到 bot 的 TLS
  授权成功页，自动完成，不用再 /回调 粘贴。为此：TLS 口改绑 0.0.0.0 且换成只含
  /bungie/callback 的 `bungie_tls_app`（不把面板/管理接口暴露给局域网）；flow 里记住
  每次授权的 redirect_uri，换 token 优先用它。跨网络的设备在隧道不可用时仍走
  /回调 粘贴。首次从局域网访问浏览器会有自签证书警告（高级→继续访问），面板
  「信任本机证书」按钮（certutil -user -addstore Root）可一次性消除。
- **授权链接不再传 scope**：Bungie 对 authorize 里的 scope 参数直接报 invalid_scope
  （「Scope is always configured value. Do not specify scope parameter.」），权限只认
  开发者应用页注册值；去掉后实测旧 token 也能读游戏内配装 206 与完整库存。
- **面板「信任本机证书」按钮**：/api/bungie/trust_cert 把 certs/localhost.pem 装进
  当前用户受信任根，消除授权回跳的「不安全」警告。
- **/登录（别名 绑定登录/授权登录）**：群友各自授权自己的 Bungie 账号——发 /登录 拿
  授权链接（state 绑定发起 QQ），授权完浏览器落到 127.0.0.1:8902（本机操作直接成功；
  手机/别的电脑打不开没关系，复制地址栏整条发 /回调 那串地址）。token 按 QQ 存
  `bungie_tokens.json`（过期自动续，一次性 refresh_token 失败重试一次），与面板授权的
  主账号（`bungie_token.json`，/每日光尘 用）互不影响；/绑定 玩家绑定保持原样。
- **/配装 数字（1-20）**：读发起者自己的第 N 套**游戏内配装**（官方组件 206
  CharacterLoadouts，DIM 同款；仅 token 本人可见）。实测要点：
  - itemComponents(300/305) 只对「随请求一起拉了 102/201/205 的物品」生成——只给
    206/300/305 时 instances 是空的，配装件拿不到 hash/光等；
  - instances 组件**不带 itemHash/bucketHash**，要从 102/201/205 物品清单按
    instanceId 反查；
  - 配装件的 `plugItemHashes` = 逐插槽下标的当前插值（空插槽 = 2166136261，DIM 的
    UNSET_PLUG_HASH 同值），碎片/模组/特长全在里面，芯片过滤与实装卡同一套口径。
  卡片复用配装模板，标题「游戏内配装 N」。默认 /配装（不带数字）仍读当前已装备。
- **/仓库 关键词**：搜发起者自己的 仓库/角色背包/已装备（组件 102/201/205+300，仅本人
  可见），中文名片段匹配（含无符号归一化），按光等排序，最多 30 条双列卡片。
- 未授权发 /配装数字 或 /仓库 会得到「先发 /登录」提示卡，不静默失败。

## 2026-10-06 /队伍配装 + /轮换 宗师掉落武器

- **新增 /队伍配装（别名 配装 / loadout）**：当前队伍各成员已装备栏整卡——徽标横幅 +
  职业/光等 + 六维 + 子职业（超能/技能/分支/碎片四行芯片）+ 左武器右护甲两列（武器
  固有/枪管/弹匣/双特长/原始特性芯片，护甲插着的模组芯片 + 光等）。
  - 名单复用 `bot_fireteam.collect` 的队伍发现（对局里→本场 PGCR 名单，轨道/在线→
    transitory 实时队伍），新增 `bot_fireteam` 两条返回路径带 `roster`（mid/mtype/name）。
  - 每人一次 `GetProfile components=200,205,300,305`（免授权，走对方库存隐私设置；
    205/305 缺 = 库存隐私私有，出「装备不可见」占位行不拖垮整卡）。命名/图标全走本地
    manifest（raw_items zh + plug_meta），除名单发现外每人只发 1 个 API 请求。
  - 插件分类按 zh manifest `itemTypeDisplayName`（2026-10 实测）：武器芯片白名单
    「固有/枪管/弹匣/特性(强化特征变体)/原始特性」子串匹配，自动滤掉着色器/外观/
    击杀记录器/塑形/空插槽；护甲芯片 = 「护甲模组」结尾 + 调谐模组（+属性/-属性）；
    子职业 = 超能技能→超能、「星相」→分支、「碎片」→碎片、其余→技能。
  - 新增 `bot_loadout.py`（采集）+ `bot_cards.loadout_card`（模板，样式内联 _LO_CSS）。
- **配装卡三处修正（2026-10-07 首轮反馈）**：①六维换 Edge of Fate 新属性制——Bungie
  沿用旧 stat hash 但含义已换成 武器/生命/职业/超能/手雷/近战（上限 200+），旧代码按
  移动/韧性/恢复/纪律/智慧/力量标注全是错的；②子职业大图标：装备槽 def 的图标是通用
  元素菱形（火焰等），好看的职业纹章在同名的 itemCategoryHashes 含 3109687656 的 def
  上——`build_item_index.py` 新增 `sub_crest.json`（名→纹章图标，覆盖光系+棱镜，
  冰影/缠绕无纹章 def 回退菱形）；③排版：武器行带光等、机灵（含机灵模组芯片）补进
  武器列平衡左右高度、成员块间距收紧。
- **配装卡名牌重做（同日反馈）**：原先把 96×96 的 `emblemPath` 纹章方块当横幅背景
  整条拉伸，糊成一团。正确结构是两个字段各司其职——`emblemPath`（96×96 纹章）原
  尺寸放左侧圆角方块，`emblemBackgroundPath`（474×96 宽幅底图）原比例左铺，右侧
  渐隐进面板底色；底图缺失才回退拉伸。
- **重要教训：改完必须重打包**——bot 跑的是 `dist_new\D2Query\D2Query.exe`（PyInstaller），
  外置 .py 只是留档（FrozenImporter 优先），源码改了不 build+deploy_exe.ps1 等于没改；
  且 raw_items.json / raw_items_en_lite.json（220MB/65MB）不进包，运行时需要它们的
  新功能要先抽成紧凑索引（build_item_index.py）。
- **/轮换 宗师板块加首通掉落武器**：lfcarry 轮换页本身带「weekly challenge weapon」
  一句，`gm_this_week` 顺带解析——英文武器名经 raw_items_en_lite 反查 hash、过
  weapons.json 映射中文名/类型/图标，宗师横图左下出「首通掉落 · 急锋（刀剑）」芯片。
  `gm_cache.json` 缓存版本升 v2（本周缓存自动重取）。

## 2026-10-05 /raid /地牢 缓存提速 + 后台任务进度 + 地牢徽章对齐（二）

- **对局历史落盘缓存 + 增量翻页**：官方对局历史只增不减（倒序返回、旧场不改），新增
  `raid_history_cache.json`（per 玩家×模式：对局列表 + gate=最新一场 period）。已统计过
  的人再查只补 gate 之后的新场——老玩家从「40 页/角色全量翻几分钟」降到几秒；全量翻过
  且一场没有的（角色没变）直接复用。翻了 40 页仍翻不到底的极端长历史不受影响（超出
  部分本来也拿不到，口径不变）。
- **PGCR 复核永久缓存**：`raid_pgcr_cache.json` 按 instance 缓存「是否从头开始/账号数/
  私局」（定局数据）。冷查询里占大头的特殊通关逐场复核，第二次起零网络。
- **/raid /地牢 走后台任务队列**：新增 `start_raid_report`/`_run_raid_job`，与热力图/
  生涯武器同一条串行队列（去重、复用窗口、面板后台任务列表全套生效）。群里发指令先回
  「统计中」卡（含队列位次），跑完出结果卡；面板能看到实时进度条。此前的痛点：同步
  直调几分钟无任何回复、面板也没进度。
- **地牢徽章再对齐 dungeon.report**：抓 dungeon.report 前端 bundle 核对，徽章全集只有
  Solo / Solo Flawless / Flawless / Day One / Contest Day One / Contest / Week One /
  All Feats——**没有 Duo 系**。地牢卡去掉「双人无暇」徽章与摘要行（上一轮保留错了），
  详情页同口径（单人通关/单人无暇）。raid 卡不动（raid.report 有 Duo/Trion 系）。
- 增量合并逻辑抽成 `_merge_hist_page` 纯函数，口径单测在 `_rtest/test_raid_hist_gate.py`。

## 2026-10-05 五处并发/数据安全隐患修复

- **面板事件循环不再被卡死**：`/api/napcat/status`、`/api/bot/status` 里的
  `napcat_runtime.status()`（同步 httpx，NapCat 不在线时单次可卡 10~15s，面板 5s 一轮询）
  改 `asyncio.to_thread`；`/api/napcat/start`、`/api/napcat/reset` 改同步 def（内含
  sleep/netstat/tasklist，FastAPI 自动丢线程池）。此前面板所有页面会被一起卡住。
- **全项目 JSON 原子写**：新增 `jsonio.dump_json`（同目录临时文件 + `os.replace`），
  替换 destiny_data / bungie_auth / napcat_runtime / bot_runtime 共 15 处
  `json.dump` 直写——写一半崩溃/断电不再损坏绑定表、token、各缓存。
- **bot_config.json 读改写加锁**：调度线程写 `rot_push_day` 与面板保存群开关/并发
  分属不同线程，无锁 load→改→save 会互相覆盖丢更新（典型：当周轮换重复推送）。
  新增 `bot_runtime.update_config()`（RLock 全程持锁），调度器两处、面板 setter 已改走它。
- **调度器任务超时补 `fut.cancel()`**：`run_coroutine_threadsafe(...).result(timeout)`
  超时后协程原本还在事件循环上跑，下个 tick 重复提交——轮换推送存在向全部群重复
  推送的可能。统一收口到 `_wait()`（超时取消 + 传播到 Task）。
- **图标下载移出渲染锁**：`card_render.html_to_png` 原本在 `st.lock` 内做图标 CDN
  下载和落盘 IO，一张慢图标卡住所有通道出图十几秒；`inline_icons` 移到锁外
  （模块级缓存本身线程安全，逐事件循环各持客户端）。

## 2026-10-04 绑定核验说明 + 改名自动同步（含实例：万籁皆为我而歌 → 绀野）

- **澄清**：/绑定 本来就过棒鸡核验——`resolve_member` 走 `SearchDestinyPlayerByBungieName`
  精确搜索，查不到直接回「没找到玩家」，存的是大小写规范的官方名。带错编号绑不进去。
- **别人网站的「模糊搜索」**：不是棒鸡官方接口（免鉴权模糊搜索 SearchDestinyPlayers 已
  404 下线），是它们自己长年爬 PGCR 攒的本地玩家索引——先在自己库里模糊匹配，再拿精确
  `名字#编号` 回棒鸡核实。本项目同款设计（seen_players.json），索引小是因为只从查过的
  对局里采集。
- **改名自动同步（新机制）**：绑定值原本只存「名#编号」，改了名就对不回去。新增
  `user_bindings_meta.json`（uid → membershipId/mtype/name/checked，与绑定表同目录）：
  - /绑定 时随 resolve_member 存下 membershipId；/解绑 一并清掉；
  - `destiny_data.sync_bindings()`：有 meta 的走 `GetMembershipsById` 对现名（Bungie
    改名不变 #编号，membershipId 终身不变），现名 != 绑定名就自动写回；老绑定没 meta
    的先精确搜索补种子，搜不到=已改名或不存在，打 `[sched]` 日志留人工核实；
  - bot_scheduler 每天一次（启动后第一轮也跑），逐条失败不挡其余。
- **实例核实**：万籁皆为我而歌#4916 → 绀野#4916（#编号一致+精确搜索命中
  mid 4611686018540633354，跨存档主平台 PSN）。已改生产绑定并种 meta。
  线索来自用户截图 Guardian.Report 的搜索结果——它家也是自建索引。
- **顺手修**：/绑定 空参数时发完用法卡会继续往下走报「没找到玩家 」，补了 return。
- **坑**：直接 httpx 调 Bungie 别漏 `/Platform` 前缀（404 返回 HTML 页，json() 直接炸）；
  PGCR 连发会限流返回非 JSON，需限速+重试；跨平台玩家 `LastSeenDisplayName` 是各平台
  平台名（PSN/Steam 昵称），不是棒鸡曾用名，别拿它对改名。

## 2026-10-04 官方通道 @别人 查询修通：at_target mentions 兜底 + 双通道身份桥

- **现象**：官方 QQ 通道 `@bot /队伍 @别的人` 不生效。两层原因：① 官方群@消息里
  @成员 是 `<qqbot-at-user id="openid"/>` 标记（adapter 解析成 mention_user 段），
  拿到 openid 后去绑定表查——表里全是大家 NapCat 侧 /绑定 存的 **QQ 号键**，
  openid 必然查不到（QQ 官方 API 刻意不提供 openid↔QQ号 映射）；② 部分场景
  content 不带成员标记、只带事件 `mentions` 数组，旧 at_target 就连人都取不到。
- **修复 ①（bot_platform.at_target）**：官方事件在 mention_user 段找不到时，从
  `event.mentions`（GroupMentionUser，带 member_openid/username/bot 标记）兜底取
  被@成员，过滤 bot 自己。
- **修复 ②（身份桥 official_binding_bridge）**：官方 openid 无绑定时，用 mention
  自带 username（QQ 昵称）去 NapCat 的群成员列表（get_group_member_list，
  enabled_groups 逐群拉，群名片/昵称双键，10 分钟 TTL 缓存）里**唯一匹配**出
  QQ 号，借他在 NapCat 侧的既有绑定。同名不唯一/匹配不到都放弃，走原有
  「对方还没绑定账号」提示卡——宁可让他绑一次，不能错查别人的账号。
- **接线（destiny2._resolve_name）**：`if not b and bp.is_official(event)` 才试桥，
  NapCat 通道行为完全不变；/队伍 /生涯 /战绩 等所有带 @目标 的指令一起受益。
- **验证**：py_compile + 伪造事件单测（mention 段/mentions 数组/唯一命中/重名
  放弃/无 username 五例）全过；打包部署后 8900/8901/8902 监听、bot 已连接。

## 2026-10-04 后台调度器：每日预取 + token 保活 + 新轮换群推送 + 图标缓存清理

- **新模块 `bot_scheduler.py`**：常驻 daemon 线程，每 30 分钟一个 tick，协程全部投递到
  nonebot 驱动循环（`bot_runtime._serve` 里 `driver.on_bot_connect` 钩子把
  `asyncio.get_running_loop()` 交给它，协议端每次重连幂等重挂）。不引 APScheduler，
  sleep 循环够用少一个依赖。
- **每日预取**：光尘商店 / 遗失区域 / 轮换 / 宗师 / 老九五类缓存定时预热——这些缓存键
  按日（每天 1 点）/按周（周三 1 点）翻转，tick 里不带 force 重调即可，键没换是纯内存/
  落盘命中近零成本，换了键就把新一轮拉好，用户首次查询从十几秒抖动变秒回。
  未授权（BungieAuthRequired）静默跳过等面板授权。
- **token 保活**：`expires_at` 剩余不足 1 小时就调 `bungie_auth.access_token()` 续期，
  失败打 `[sched]` 日志提示去面板重新授权。原有按需刷新（带锁+重试）保留不动。
- **新轮换推送**：`rotation_week_key()` 算最近周三 01:00 的 ISO 日期当周期键；与
  bot_config.json 的 `rot_push_day` 不同 → 渲染 `bot_cards.rotation_card` 完整卡推给
  `enabled_groups`（含扭曲星球/宗师/遗失区域附属板块，单板块失败不挡主卡）。只走
  NapCat 通道（官方机器人只有 5 分钟被动窗口发不了主动消息）；**enabled_groups 为空
  不推**（它兼作指令白名单，「空=所有群」对推送不可枚举）。推送成功才写
  `rot_push_day`，取数据失败/NapCat 未连接下个 tick 重试，exe 中途重启自动补推。
  推送记录进 bot_log（面板消息日志可见）。
- **icon_cache 清理（card_render.cleanup_icon_cache）**：bot_runtime.start() 时执行，
  两级哈希子目录 walk 递归——先删 45 天未用的，再按最久未用补删到 800 个以内，删了
  多少打一行日志。此前只写不删（内容寻址安全但 19MB 起步无限涨）。
- **吞异常补日志**：配置/凭证路径上的裸 `except: return` 拆开——文件不存在是常态
  静默，其余（JSON 损坏等）打 `[bot]`/`[platform]`/`[auth]` 日志按默认值继续
  （bot_runtime official_creds/load_config/max_concurrency、bot_platform._cfg、
  bungie_auth._load/_save）。
- **核实**：`.gitignore` 已完备（`*_cache.json`/dist_*/icon_cache 均已排除），无需改。
  本地验证：py_compile 全过；rotation_week_key 边界 5 用例（周三 0:30 归上周、1:00 归新周等）全对。

## 2026-10-04 面板新增「运行状态」页：进程资源实时监控 + 并发上限可调

- **动机**：用户希望后台能看到机器人实时占用（CPU/内存/网络），且能把并发压低以减少对电脑的影响。
- **运行状态页（导航新增，/runtime）**：2 秒轮询 `/api/runtime/stats`（psutil）——进程 CPU/内存(RSS+占系统比)/线程数/运行时长、系统 CPU/内存、系统网络 ↑↓ 速率与累计；均带进度条与阈值变色。psutil 7.2.2 已进 requirements 与 D2Query.spec hiddenimports。
- **并发上限设置（bot_config.json 的 max_concurrency）**：面板下拉（0=默认/2/4/6/8/12/16/24/32），保存即生效无需重启；`bot_runtime.concurrency_limit(default)` 每次从文件读（三个事件循环共享），PvP/PvE 逐场对局拉取（destiny_data `_PVP_CONCURRENCY`）与卡片图标下载（card_render `_ICON_LIMIT`）两处 Semaphore 改走该值。
- **坑**：进程级「网络速率」拿不到（Windows 无按进程的 net 计数，io_counters 是磁盘 IO），网络瓦片显示系统级 net_io_counters 差分速率；`/api/runtime/stats` 写成同步 def 让 FastAPI 丢线程池，psutil 采样不卡事件循环。

## 2026-10-04 武器卡「获取方式」补全：爬 light.gg source-hint 填世界掉落老枪

- **缺口**：pattern_sources.json 依赖 Bungie 藏品 sourceString，36 个名字（51 hash）无来源——
  往季世界掉落老枪官方已撤藏品，卡片上「获取方式」整行缺失。
- **通道**：light.gg 物品页的 `<div class="source-hint">` 仍给这批枪维护来源
  （zh-chs 页物品名是中文但 hint 恒英文）；直连必被 Cloudflare 拦，走 weapon_usage 同款
  CDP 通道（9222 已验证 Edge，独立调试 profile 可与日常 Edge 共存，无需先杀当前 Edge）。
- **新脚本 `build_pattern_sources_lightgg.py`**：抓缺失 hash → 落 `manifest_index/
  lightgg_sources_raw.json` 断点缓存 → 8 种去重句式手工翻译（仄/萨瓦拉/萨克斯/班西-44/圣-14/
  先锋军械库周常/日晷水晶/无法之地边境）→ 按名合并进 pattern_sources.json（同名多版本
  多来源用「/」拼接）。补齐 25/36 个名字。
- **剩余 11 个名字三处皆无数据**（官方藏品无 sourceString/无藏品 + light.gg 无 hint +
  全量扫描 2170 个商人定义 itemList 也不在）：S1 远古世界掉落（超级坏蛋/考勤卡/责难AX-GL/
  急板-48）、S27 狼毒、S28 无投放变体（塔霍马01/艾伦05 等 6 把，当季 light.gg 尚未维护）
  留空不硬编；审判（专家）按基础版补「“预言”地牢（专家版）」。
- **坑**：商人定义缓存 `manifest_index/raw_vendors.json`（41MB，gitignore 内）可复用于
  以后查「哪个商人卖什么」；数据 json 只需同步 `dist_new/D2Query/_internal/manifest_index/`
  并重启 exe 生效，无需重打包。

## 2026-10-04（二）raid 卡多账号比对：修「同副本多 hash 求和」+ 跨存档全平台历史合并

- **多账号验证**（5 名榜单玩家 + Wj，覆盖 steam/psn/xbox/epic；对比脚本 `_rtest/multi_compare_raid.py`）：
  逐副本对「页面 / api.raidreport.dev / 本地卡片」三方比对。
- **差异根因 ①**：同一副本在 raid.report 里有**多个活动 hash**（原版/重制版/竞赛版等，如克洛塔
  「标准」37 + 「普通」51），页面的难度行是**求和**（88），合并逻辑原来取 max 导致系统性少算。
  已改 `_merge_rr_stats` 按 (base,大师) 分组内 **rr_sum 累加**、与官方历史取 max。
- **差异根因 ②**：raid.report 页面合并同一 bungie 账号**全部平台**的对局（GetMembershipsById），
  主平台单拉会少算跨存档前的场次（Benson 克洛塔页面 93 vs 主平台 92）。`raid_report()` 现在
  枚举全部平台 membership，逐平台×角色深翻历史并按 instanceId 去重（无跨存档的玩家零开销）。
- **验证结果**：Wj raid 卡与 raid.report 页面完全一致（克洛塔 33、KF 42、DSC 84、忧愁王冠 8、
  最后一愿 15、玻璃拱顶 21+16）；Niko 地牢 11/11 全对；合计 OK 27 / DIFF 6。
- **剩余 6 处差异（≤4 场）均为 raid.report 自身口径漂移**：页面「总数」与「难度行之和」自相矛盾
  （Benson 守望者尖塔 总数 122 vs 行和 99）、自家 DB 与页面不同步（Wj 预言 39 vs API 38）、
  页面 fastest 与 API fastestFullClear 差 45~50s（忧愁王冠 1h18m vs 1h18m45s）。卡片取
  「官方历史(全平台) 与 rr API 求和 的 max」，只多不少，属更完整口径。
- **对比脚本的坑**：页面 innerText 解析别用文本启发式剥难度后缀（「永恒沙漠」会被难度词
  「永恒」整行吞掉）；直接解析 HTML 行结构（`<b>名</b><span class='rdiff'>难度</span>…通关<b>N</b>`，
  徽章与数字之间**无空格**）。rr 页面数字是前端现算的，抓取要等 `Kills\t数字` 出现；
  连续导航会让标签页 renderer 崩掉（`document.body` 变 null），每页新开标签抓完即关。

## 2026-10-04 `/raid` `/地牢` 通关数对齐 raid.report + 最右列改「最快全程」

- **现象（用户报）**：卡片数据跟 raid.report 对不上（深岩墓室 56 vs 84、忧愁王冠 1 vs 8、
  玻璃拱顶 28 vs 37……全线偏低）；最右侧「最快」显示的是所有通关场里的最短时长
  （检查点进局的 3 分钟能吊打正经全程），raid.report 显示的是 fastest full clear。
- **根因**：①官方对局历史会被裁剪——只留 ~2020-03 起的场，**已删除角色**的历史完全拉不到；
  raid.report 的数据库是「历史累计」（对局发生时就入库），所以老副本通关数永远比官方历史全。
  ②「最快」没过滤检查点局。
- **修复**：新主源 `api.raidreport.dev /{raid|dungeon}/player/{mid}`（一条请求逐 hash 给
  `clears / fullClears / fastestFullClear`，与它页面显示完全同源）：
  - `_rr_stats()`：30 分钟 TTL 磁盘缓存（raidreport_stats.json）；拉不到时**退回过期缓存**；
    连缓存都没有才回落官方历史口径（`_rr_fetch_json` 直连→CDP 复用既有通道）。
  - `_merge_rr_stats()`：hash→中文名用 manifest_index/activities.json，`split_activity` 拆
    难度后并入历史分组；通关取 max、最快全程取 min；官方历史完全没有的组只在发售表里的
    主副本补建（参与=通关兜底，别让「通关>参与」打架）；众神殿分身等杂项 hash 没历史组就跳过。
  - 「最快全程」兜底口径：raid.report 不可用时，把最快的 24 场通关补进 PGCR 复核
    （验 `activityWasStartedFromBeginning`），只认**明确验证过从头开始**的场。
  - 顶部「总通关次数」改为 Σ分组通关（对齐后与 raid.report 口径一致）；副标题标注
    「通关数/最快全程已对齐 raid.report（含官方历史已裁剪的老对局）」。
  - 徽章（无暇/低人/首日/首周）仍走官方历史+PGCR 复核，raid.report 接口不提供这些。
- **实测核对**（Wj#8984，合并逻辑离线单测 + 线上卡片）：深岩墓室 84✓ 忧愁王冠 8✓
  玻璃拱顶 21+16✓ 国王的陨落 42✓ 梦魇根源 78+2✓ 救赎的边缘 18+3✓ 最后一愿 15✓；
  最快全程 = raid.report FASTEST 列同值（如 DSC 37m49s，不再是检查点局 3分0秒）。
- **注意**：利维坦家族（2018 年 3 个副本）raid.report 页面与自家 API 数字略有出入（±3），
  以 API 为准；`raidreport_stats.json` 已入 .gitignore。

## 2026-10-04 修复：武器卡「催化」列重复条目（枯骨鳞片）

- **现象**：`/武器查询 枯骨鳞片` 的催化列并排两条「枯骨鳞片催化」，热门组合也两两重复。
- **根因**：官方 Manifest 里同一份催化常挂**原版/重制版两个插件 hash**（枯骨鳞片
  = 229096166/2132353550，名字图标效果完全一样），与「空催化插槽」同住一个
  plugSet；`patch_catalyst_cols.py` 补「塑形异域催化列」时不按名字去重全塞进列，
  `weapon_usage.py` 注入列与「催化×枪托 均分重算组合」又把重复放大一倍。
- **修复**：`weapon_usage.py` 四处按名去重（列构建留高出现率、催化列注入、
  大师杰作/改装 side()、组合按 names 去重留最高值）——去重后不足两选项的催化列
  整列不注入；两份 `weapons_full.json` 数据同步去重（保留带数值的
  catalysts 桶 hash）；遗留补丁脚本同样加上；受影响的用量缓存条目已清（下次查询重抓）。
- **波及检查**：全量扫描 2208 把武器 + 两份用量缓存——列内重名只有枯骨鳞片；
  另有 6 把枪缓存契约的「特性 1」列「脉搏监控」双 hash 重复，同批修复（重抓解决）；
  单人合唱/零号修订等真·多选催化（名字各不同）不受影响，回归验证通过。


## 2026-10-03 新指令 `/进度`：d2checkpoint 尾王存档点实时列表

- **功能**：群里发 `/进度`（用户定的触发词；别名 进度机器人/进度点/存档点/checkpoint）
  回复两条：①**可复制文字**（小日向式：`🟢 最后一愿 第2关·舒罗-祈（有位 2/6）` 下一行
  整行 `/j CheckpointBot#0422`，玩家长按即可复制去游戏里粘贴）②**紧凑卡片图**（两列
  并排：官方活动小图 + 官方中文副本名 + 第几关/尾王金色高亮 + 人数，离场行置灰）。
  数据源与「小日向」同一家（d2checkpoint）。
- **接口（免授权）**：`POST https://d2checkpoint.com/_actions/bots.getBotsFromDb`
  ——Astro Actions，路径是**点号**拼接（用斜杠会 500）；响应为 devalue 序列化数组
  （A[0]=bot 下标列表，对象是「{键: 绝对下标}」模板），`_parse_devalue_bots` 解码。
  activityHash=0 的 bot 是离场不摆的；encounter 是该活动 encounterList 的下标。
- **实时状态**：官方 `GetProfile components=1000`（profileTransitoryData）核对每车
  人数——`numberOfPlayers` 含 bot 自己：≥fireteam=满员，0/缺失=已离场（bot 退出了
  副本，点位多半没了），其余=有位。并发 5 路拉取，全链路 3~4s；进程内缓存 90s。
  **已离场/状态没核对上的直接不显示**（用户要求只留能用的车）。
- **官方译名与图标**：活动中文名/小图直接取 `manifest_index/activities.json`
  （hash → {name, pgcr, icon}，官方 DestinyActivityDefinition 精简表）——Pantheon
  官方译名是「众神殿：辉煌卡鲁斯/超越摩格斯/革命暴动首领」，**不要自己音译**
  （此前手写「万神殿：卡鲁斯」就是错的）；`name` 带「: 标准/: 自定义」尾巴要按
  ASCII 冒号切掉。pgcr 大图走 card_render 的 inline_icons 自动落盘缓存内联。
  「第几关/尾王」= encounter 下标 + `_CP_ACT_CN` 关卡表长度（末位=尾王）；关卡
  中文名仍手写官方译名，认不出的 hash 回退英文名+第N关。
- **改动**：`destiny_data.fetch_checkpoints()/checkpoints_text()` +
  `bot_cards.checkpoint_card()`（v2 皮肤两列网格）+ `nonebot_plugins/destiny2.py`
  注册 `/进度`（先 `matcher.send` 文字再发图——`_reply` 会 finish 掉处理器不能复用）；
  帮助卡「资料」行补 `/进度`。官方 QQ 通道同样可查（被动回复）。


## 2026-10-03 程序退出带走 NapCat + 面板授权态补「刷新 Token / 重新授权」+ 部署落点限死工作区

- **NapCat 随程序关闭**：`napcat_runtime` 注册 atexit——面板窗口关闭（`webview.start()`
  返回、解释器正常收尾）时 `stop()` 连根清掉 NapCatWinBootMain + QQ 进程树；`stop()`
  新增 `_kill_orphan_boot()` 兜底，把「exe 崩溃后遗留 / 被新实例认领（_proc 为空）」的
  引导进程也按 EXE 名扫杀（与 `_cleanup.ps1` 同口径，不会碰用户自己的主号 QQ）。
  部署脚本 `taskkill /F` 停 exe 不走 atexit，NapCat 存活 → 重启 exe 后原会话自动重连，
  依旧免扫码。注意：关窗再开程序后需在面板点一次「启动并扫码登录」（uin 已存，快速
  登录免扫码）。
- **面板授权态补按钮**：已授权分支此前只有「取消授权」，token 过期（本例 access_token
  已于 10-03 凌晨过期且一直没触发过自动续期）没有任何手动入口。现补：token 到期时间
  展示 + 过期高亮；「刷新 Token」（新端点 `POST /api/bungie/refresh`：`authorized()`
  检查 + `access_token()` 就地续期，未过期直接复用、不浪费一次性 refresh_token；失败
  报真实原因并附重新授权链接）；「重新授权」直达 `/bungie/authorize`。
- **部署落点限死工作区**：`deploy_exe.ps1` 默认 `-Dst` 从 `dist\D2Query` 改为
  `dist_new\D2Query`；工作区外落点（`F:\D2Query`）已废弃，在用户要求下删除——删除前
  已核对两边数据文件（.env / user_bindings / seen_players / 凭据 / token /
  bot_config / napcat 登录态）为同一代且 marker/inode 测试确认互为独立目录。

## 2026-10-03 `/老九` 改版：武器/催化/隼月整卷上线（Kyber 周货主源 + 分节修复）

- **用户反馈的四个问题**：只有金装没武器；术士职业金（唯我主义，ty=`术士臂环`）没和
  泰坦印记/猎人披风放一起（`臂环` 不在职业金关键词里，掉进了异域护甲按职业分节）；
  周常任务「异星学」（Xenology）不该出卡；武器列表/隼月详情/金枪/催化整栏缺失。
- **根因（武器栏缺失）**：老九的异域武器/催化/传说栏被账号解锁门槛挡住——未解锁账号
  的单店接口 `sales.data` 直接给 `privacy` 锁或空（实测同一窗口内单店与全量还会**抖动**，
  同参数时有时无），只有护甲/材料/任务约 17 件。这是 Bungie 侧的门，账号侧无解。
- **修法（主源换 Kyber's Corner）**：`kyberscorner.com/destiny2/xur/` 每周把完整货单
  内嵌在页面 JSON `window.KYBER_XUR_DATA`（来源是他们自家已解锁账号的 vendor 组件，
  generatedAt=周五 17:00 UTC）：异域护甲×9 / 隼月整卷 sockets / 金枪 / 催化 /
  传说武器 / 传说护甲（含每职业 5 件的周套装）/ 材料价目，**hash 与本地
  vendor_items.json 全对得上**。`destiny_data._kyber_xur()` 抓取（正则锚
  `window.KYBER_XUR_DATA\s*=\s*` + `JSONDecoder.raw_decode`；注意**不能**裸
  find 首次出现——页面 JS 函数体里也引用这个变量名），内存缓存 2h + 落盘
  `xur_kyber_cache.json` 兜底；在其 arrival~departure 窗口内出卡**免授权**，
  窗口外（周四/周五/未更新）退回原 Bungie 接口兜底（未授权才提示去面板）。
  中文名/职业/价格货币全走本地 vendor_items 索引，perk 插槽名走 plug_meta.json
  （隼月整卷=固有「超因果射击」+「风暴之眼」+「战斗握把」，英文兜底）。
- **分节修复**：`_xur_sec` 职业金关键词补 `臂环`；护甲关键词补 `臂铠`（剑圣手套/
  护臂/臂铠这 3 件传说护甲曾被分进传说武器）；记忆水晶改归「材料」出卡
  （有价格，如奇异护甲/武器记忆水晶）；「任务」节整体不出卡（异星学）。
  随机卷判定：异域武器带 `grips` 槽（隼月专属）或 frames/grips 槽 reusablePlugs>1
  → 大卡整卷，其余金枪小卡只出固有特性。
- **验证**：`_rtest/xur_probe7.py`（只读 dump 全量货单，access_token 直用不刷新）、
  `_rtest/xur_render2.py`（真数据 49 件 8 节 + 隼月大卡渲染断言）；`_rtest/
  _kyber_xur.json` 留档本周原始 JSON 并预置进 dist_new 作首次缓存。
  ⚠ 踩坑：Git Bash 传 `-Dst dist_new\D2Query` 反斜杠被吃，要用正斜杠。
- **二轮反馈修复**（隼月图不完整 + perk 少枪管弹夹）：① 大卡背景 cover 会把
  1920×1080 截图裁成 120px 高的横条，改 `contain` 整枪缩放靠右（`background-
  position:right 14px center`）；**不能用物品图标替代**——隼月图标本身就是特写，
  看着还是裁切。② `_xur_kyber_plugs` 类别补 `barrels`/`magazines`（全口径枪膛/
  合金弹匣等中文名 plug_meta 全有）：大卡=固有+枪管+弹夹+特性+握把整卷；传说
  小卡 `plugs[-2:]` 槽序（枪管→弹夹→特性×2）不变仍出两特性。

## 2026-10-03 `/队伍` 巡逻区/社交空间被当成对局（「已结束 2 分钟」事故）

- **现象**（用户截图）：玩家正在打「移民号的坠毁」打击（已进行 20+ 分钟），卡片却写
  「不稳定半人马座，涅索斯 · **已结束** · 对局时长 2 分钟」，开始时间只比当前这局早 2 分钟。
- **根因**（`_rtest/ft_live_dump.py` 实时复现）：
  1. 旧判据「currentActivityHash 在 manifest 里有没有名字」把**自由漫游区**也当成真活动——
     巡逻区在 manifest 里明明有名字（`activityModeTypes=[6,7]`，6=Explore）；社交空间同理
     （`activityModeTypes=[40]`，高塔/农庄/蛛王藏身处）。两者都不是一局对局。
  2. 于是进了对局分支，`_match_entry` 只按 ±10 分钟配开始时间，配上的其实是**上一场巡逻会话**
     （18:14 开始、147 秒、18:16:27 结束），而当前打击 18:16:29 才加载 —— 配到的历史行
     在本局开始前就结束了，只能是"上一把"。卡片接着按那条行的 `duration` 报「2 分钟」。
- **修法**：新增 `bot_fireteam._activity_kind()`，按活动定义的 `activityModeTypes` 分四类
  `match / patrol / social / none`（none 仍含轨道占位 hash）。
  - patrol / social → 新状态 `world`（卡片标签「自由漫游」/「社交空间」）：不出对局信息，
    只给队内每人的生涯总时长 + 成就点数（与轨道态同口径）；
  - 对局分支里历史行先过滤成真对局；`_match_entry()` 加 `end >= 本局开始时间` 守卫。
- **验证**：`_rtest/ft_kind_test.py`（类型判定 + 守卫 + `world` 卡渲染断言；含 高塔=social、
  巡逻区=patrol、打击=match、轨道占位=none）与 `_rtest/ft_verify_fix.py`（对同一玩家跑真实
  `collect()`，卡头由「已结束 / 2 分钟」变为「进行中 PvE 移民号的坠毁: 自定义 开始 10-03
  02:16 已进行 26 分钟」）。自由漫游态版式另可用 `_rtest/ft_orbit_mock.py` 同款 mock 核对。

## 2026-10-03 老九（仄/Xûr）每周商品查询 `/老九`

- **指令**：`/老九`（别名 `/仄` `/d2老九` `/xur` `/老九商品` `/老九在哪`）。官方中文名
  就叫「仄」（vendor 展示物品 3329627384）。出卡内容：异域装备 / 异域护甲（按职业）/
  异域印痕 / 材料 / 任务，价格=奇异硬币（图标走本地索引）。
- **数据**：官方 GetVendors（OAuth，复用光尘管线；`bungie_auth.authorized_get`）。
  2190858386 = 仄。**实测他未到场（周六 01:00 前）全量接口也带预上架 saleItems**
  （异域护甲等，hash 为真、可解名），单店 `Vendors/{hash}/` 到场才开（未到场
  `DestinyVendorNotFound`），且只有单店给 `itemComponents.perks`（随机卷）——所以
  到场优先单店一次拿全（components 400,401,402,300,302,304），失败退回全量兜底。
  单角色即可：全职业护甲在同一份返回里。
- **在场窗口/缓存**：周六 01:00 → 周三 01:00（维护离场）；在场缓存 2 小时、不在场
  10 分钟（`_XUR_CACHE` 内存缓存）。未到场时只显示 `saleStatus==0` 的预上架商品，
  卡头出抵达倒计时（`_xur_next_arrival` 算下一个周六 01:00）。
- **名称解析**：新索引 `manifest_index/vendor_items.json`（`build_vendor_items.py`
  从 raw_items.json 裁出，29126 条 / 4.3MB，懒加载）——保留可装备金紫蓝（不要求
  equippable，异域职业臂是 False）+ 印痕/记忆水晶/货币/材料/任务/可兑换/阵营奖励。
  换赛季后如果商人货解不出名，重跑一遍该脚本即可。
- **验证方式**：探针在 `_rtest/xur_probe*.py`。跑探针要把 `bungie_auth.TOKEN_FILE`
  直接指到**真文件** `dist_new/D2Query/bungie_token.json`（刷新写回同一份，exe 也读
  这份，这才是对的）。**千万别拷贝副本做实验**——refresh_token 一次性，副本刷新会
  把真文件里的作废掉（本次踩过：副本刷掉 R0 后 exe 下次刷新必 400，幸而同轮把新
  token 写回了真文件修复）。仓库根目录的 bungie_token.json 是占位符（'x'/'y'），
  指到它会报同样的 400 Base-64 错。
- **当日二次改版（对齐用户参照稿，即小日向商人页风格）**：三角色各查一遍再合并——
  职业栏位按角色发（实测 idx 133/134/135 分别只给猎人/泰坦/术士的职业臂），单角色
  会缺另外两个职业；同名双 hash 变体（异色版）只留第一份。卡片改藏青渐变底 + 橙色
  分节条 + 稀有度框图标 + 「币上数下」价格列；**异域护甲按 泰坦/猎人/术士 分节**；
  perk 直接带中文名（perks.json 图标+名）。**带随机卷的异域武器（如隼月）单独大卡**：
  大图背景 + 「本周随机卷」角标 + 可见 perk 逐个图标+名（可见 perk≥2 判定为有卷）。
  新增分节：异域武器 / 异域武器催化 / 传说武器 / 传说护甲。
- **重要发现：武器/催化/传说栏被账号解锁门槛挡住**——未解锁账号（我们）的接口返回
  只有 17 件（异域甲+印痕+任务+材料），购买游戏内「更多奇异优惠 / 奇异装备优惠」
  （或异域等级达标）后同一接口才会吐出武器/传说装备；卡上已加提示行。vendor 定义
  的 sales 为空、displayCategories 只有 identifier（category_exotic_weapons 等），
  定义里预埋不了每周货单。异域武器催化物品 tier/ty 双空，索引靠名字后缀「催化」抓
  （build_vendor_items.py 已加规则，29318 条）。
- **当日三次修正（用户对照游戏内实况反馈）**：
  - 职业金（泰坦印记/猎人披风/术士猎环）从各职业节抽出，单独「职业金」一横列
    （xugrid3 三列）；金装卡不显示 perk 芯片，只有武器显示。
  - 武器 perk 来源重做：按 socketType→类别 抽槽（WEAPON PERKS=4241085061、
    INTRINSIC=3956125808；模组/外观/击杀记录器类剔除——老武器定义把记录器塞在
    特性组里，按插件名含「记录器/计数器」过滤）。实盘 sockets 与定义 socketEntries
    按下标对齐取真值，物品单件定义走公开实体接口（client 自带 6h /Manifest/ 缓存）。
    **插件 hash 是物品空间，perks.json 是 perk 定义空间，两套不通用**——名字一律
    现拉插件定义补（perks.json 命中就直接用）。隼月判定=特性槽带
    randomizedPlugSetHash；大卡=固有特性+全部特性槽；传说武器小卡=特性槽末两个
    （游戏 3/4 号位）；固定卷异域（蒙特卡洛）小卡=固有特性（蒙特卡洛法则在
    INTRINSIC 类里，不在特性组）。
  - 异域记忆水晶（印痕）不出卡；mock 构造器（_rtest/xur_render.py）与线上逻辑
    同源，从定义插槽池取样例卷，专验隼月大卡排版。

## 2026-10-03 扭曲星球板块改版：压缩时间表 + 侧边武器掉落/套装名

- **改版内容**（用户拍板的预览方案）：`/轮换` 卡扭曲板块从「当前时段 + 24 格全天表」
  改为左右两栏——左栏保留绿色当前时段框（下一个目的地/整点倒计时），全天表压缩成
  **7 格「未来一轮」**（现在 →+6 小时，当前格绿高亮、次格淡绿描边，格内标注
  `现在·剩X分` / `+N 小时`）；右栏新增**本时段掉落面板**：目的地武器池逐把列出
  （图标+官方中文名+类型，双列小卡）+ 金色「奖励 · XX 套装」行，随整点目的地切换。
- **数据**：`manifest_index/distortion_loot.json`（新）——武器池 = Monument of Triumph
  更新后的目的地武器池（7 区域 48 把，抄录 blueberries.gg 2026-10 版，区域↔套装对应
  GameRant），中文名/图标/类型全部走本地 manifest 解析（raw_items_en_lite 找 hash →
  raw_items 取 zh），套装中文名复用 rotation_zh.json sets（去件类后缀）。
  spec 的 manifest_index 是 glob 自动收集，该 json 以后重打包自动带上。
- **接线**：bot_cards.py `_dist_block` 换新版（`_dist_block_basic` 保留为兜底：
  掉落 json 缺失或轮换环算不出时自动退回旧版 24 格全天表，不炸卡）。
- **重要踩坑——「外置模块同步」对这几个模块根本不生效**：D2Query.spec 的
  `hiddenimports` 里显式列了 bot_cards/card_render/bot_platform 等，它们被
  **打进 exe 压缩归档**，运行时 FrozenImporter 优先于 exe 同目录的外置 .py。
  实证：第一轮部署只同步了外置 bot_cards.py（未重打包），QQ 卡仍是旧版；
  build_exe.bat 重打包后才生效。**改这些模块必须重打包**，deploy_exe.ps1 的
  「sync external modules」一步只是摆设（webui.py 不在 hiddenimports 里，
  同样在归档内，同理）。
- **验证路径**：webui `/rotation` 页调用 `rotation_card(rot)` 单参数，本来就不渲染
  扭曲板块，不能用它验证；页面 `<style>` 里出现新类名（rd2-*）可反证 exe 内
  bot_cards 已是新版。QQ 端发 `d2 轮换` 看实际效果。
- 渲染自测脚本：`_rtest/preview_distortion_side.py`（可传区域名拨时针出任意区域预览）、
  `_rtest/test_dist_block_live.py`（渲染真实 `_dist_block` 输出）。

## 2026-10-03 副本口径修正：全程无暇 + 首日排名 + 时间审计

- **无暇/低人口径重算**（用户 2026-10-03：只通尾王的不算无暇）：官方 PGCR 顶层有
  `activityWasStartedFromBeginning`——检查点（尾王）进局为 `false`，从头打为 `true`。
  `raid_report()` 现在对每场 0 死亡通关 / 低人通关补拉 PGCR（并发 6，走 5~6h TTL 缓存），
  只有 **从头开始 + 0 死亡 + 非私局** 才计 无暇/单人无暇/双人无暇/三人无暇。
  实测 Wj#8984 突袭 0 死「通关」137 场里 136 场是检查点/私局，真无暇只有 1（2020-03 救赎花园）。
  - 低人（单人/双人/三人）改用 **PGCR 全程出现过的账号数**（raid.report 的 accountCount 口径，
    取 max(账号数, 场上人数) 防老 PGCR 被官方裁剪误判）：6 人团退到剩 2 人通关不再算双人；
    不要求从头（与 raid.report 低人口径一致）。
  - 私局（isPrivate，如众神殿自定义装载 2 分钟杀尾王）不进任何特殊徽章（无暇/低人/首日/首周），
    参与/通关计数维持原样。
  - 副本详情页（`render_raid_detail`）同步同一套口径，与主卡数字一致。
- **首日排名**：接入 **api.raidreport.dev**（raid.report/dungeon.report 的后端，无 CF 可直连；
  前端 JS 枚举里挖出 worldsfirst 首日榜端点与全部副本 slug）。
  `/raid|dungeon/leaderboard/worldsfirst/{slug}?membershipId=` → 该号首日名次（`entries[].rank`
  + `metadata.totalResults`）；没打过首日返回 JSON 404（也是终态）。徽章显示 `首日 #12/683`，
  悬停看「首日通关 ×N · raid.report 首日赛第 12 名」。
  - 该 API **慢（冷启动 ~20s+）且 CF 风控飘**：连续快查/非浏览器指纹都可能 403 →
    结果（含「无排名」）落盘 `raidreport_ranks.json` 永久缓存（首日名次不会变），
    直连失败自动走**调试 Edge CDP 兜底**（weapon_usage 同款 9222 通道；注意必须
    「先开 raid.report 页、再页内 fetch」——直接 goto API 地址是顶层导航，缺 Origin 头，
    对方 Lambda 回 Bad Request）。
  - slug 表 `_RR_SLUG`（destiny_data.py）覆盖全部已收录副本；永恒沙漠（史诗）依次试
    epic→epiccontest，大师组追加 /master（404 即无此榜）。新副本记得同步这张表。
- **「最近」修成最后一次通关**：分组循环按时间**倒序**遍历，原代码 `g["last"] = m["period_cn"]`
  每次通关都被覆盖，最终留下的是**最旧**一场通关（2023-06-03 那种就是首通）——老版本
  「最近」一直是错的。改 `max()` 取最新；且只统计通关场（打过没通不推进）。
- **时间审计**（用户 2026-10-03：战绩界面还是 UTC）：战绩卡（webui `render_history_card`）、
  对局详情页（PGCR 卡头部）、/宗师卡「最后」全部转北京时间（`period_cn`/`_cn8`）；
  `history_report`/`get_pgcr` 现在都带 `period_cn`。其余位置（raid 行、按月筛选、
  上次在线）此前已走 period_cn，复核无遗漏。
- 口径说明：本卡「无暇」= **本人 0 死亡** + 全程；raid.report 官网的 Flawless 是**全队无人死亡**，
  两边数字天然会差（个人无死全通 > 全队无死），属预期。

## 2026-10-02 赛季等级修复（S27+ 通行证改版）+ `/raid` `/地牢` 报告大修

- **赛季等级错误的根因**：S27（溯回，2025-07）起 Bungie 改版通行证——一个 progression 统一计级
  （可超 100），且 `DestinySeasonDefinition.seasonPassList` 里出现**多条** pass（赛季 pass + 同一年的
  另一条，如铁旗余灰/无序）。`build_seasons.py` 原来只取 `seasonPassList[0]`，S28 拿到的是另一条
  （玩家等级只有 4），而真正的「凯旋」pass 上是 151。修复：
  - `build_seasons.py` 全量记录 `passes: [{rew, pres, name}]`（prog/pres 仍指第 0 条兼容旧消费方）；
  - `destiny_data._SEASON_PROG` 三元组 (赛季号, 是否声望档, pass下标) + `_SEASON_PASS_MAIN`
    按「pass 名 ⊆ 赛季名」选主条目（S28: 无序≠凯旋纪念碑→选凯旋；匹配不到运行时取最大）；
  - 等级公式 S27+ 且奖励档>100 时**直接取奖励档**（声望档是迁移遗留，两条轨仍并行吃同一条
    XP 流——段内 progressToNextLevel 只差 528，计入会双算）；S8–S26 维持「奖励+声望」。
  - 口径经 DIM 源码核对（`SeasonalRank.tsx`: `min(rew, baseLevels) + prestige`，本例 151），
    DIM 另证实：`Destiny2CoreSettings.currentSeasonPassHash` 才是权威选轨方式。
- **`/raid` `/地牢` 报告**（用户 2026-10-02 四连反馈）：
  - **低人/老记录收不全**：对局历史原来每人只翻 3 页×250，现在最多 40 页（实测 Wj#8984 突袭
    1152→1969 场，最早回到 2020-03，低人通关全部入账；通关场 player_count=0 的为 0，无缺口径）。
  - **最近一场日期不对**：Bungie 的 period 是 UTC，卡片直出会差 8 小时。raid 链路对局加
    `period_cn`（北京时间），分组「最近」、按月筛选、对局行、生涯/玩家卡「上次在线」全部转 UTC+8；
    bot_fireteam 自带 `_cn()` 转换、内部比较逻辑保持 UTC 不动，避免二次平移。
  - **排序改发售先后**：新增 `_RAID_ORDER`（利维坦→…→永恒沙漠；地牢 破碎王座→…→平衡），
    收录外的活动（众神殿/安可/探索者等）排在已收录之后按名排；`build_raid_metrics.py` 的
    RAIDS 表同步改成发售顺序（/队伍 完成数砖顺序一致）。
  - **首日/首周徽章**：`_RAID_RELEASE_UTC` 发售时刻表（UTC，来源 Destiny2Team/X、Bungie press、
    destinypedia，见下），通关场次落在发售 24h/7 天窗口内计 首日/首周，金色高亮徽章只在有数时出现。
    实测命中：克洛塔 首周3、救赎边缘 首周1、深渊机灵 首日1、战争领主废墟 首日2 首周5、
    晚星之主 首周2、分离教义 首日2 首周3、预言 首周1。
  - **零值徽章不再灰着占位**（用户指定）：无暇/单人/双人/三人/单人无暇/双人无暇/三人无暇 为 0
    直接不渲染，只显示已达成的特殊通关；参与/通关恒显。
- **发售时刻表**（全部核实过，竞赛时长 24h/48h 不影响本判定）：永恒沙漠=The Desert Perpetual
  2025-07-19 17:00 UTC（史诗版 2025-09-27）、平衡=Equilibrium 2025-12-13、晚星之主=Vesper's Host
  **2024-10-11**（一开始预填 10-04 差一周会漏首日）、分离教义 2025-02-07、忧愁王冠 2019-06-04
  **23:00** UTC（当年唯一非 17:00 上线）、其余 17:00/18:00 UTC（夏令时差）。
- 踩坑：S27+ 双 pass 不是并行双轨而是**年内两次重置**的先后轨道（窗口 2025-12→2026-06→2099），
  不能按下标也不建议按等级 max 硬猜，跟名字/核心设置走；PvE 模式 4=Raid、82=地牢，
  众神殿（Pantheon）等活动也挂 raid 模式会进分组，排序时归入「未收录」尾部即可。

## 2026-10-02 `/轮换` 新增「当前宗师」+「今日遗失区域」板块

- **官方 API 没有这两样**（实测 GetPublicMilestones 只有突袭/公会/赛季活动里程碑，
  manifest 的 DestinyMilestoneDefinition 全量里也没有 Lost Sector 定义），走社区页：
  - **遗失区域**：2025-07 的 9.0.0.1 改版后不再有全球每日一个，改成 **9 个目的地各自每日轮换**。
    数据源 [d2lostsector.report](https://d2lostsector.report) 首页是**服务端直渲染**（无需浏览器/无 API），
    卡片背景图 URL 里就带活动 hash、勇士/护盾/强化武器在图标 alt 文本里，正则整卡提取即可。
  - **宗师**：lfcarry 的[周轮换页](https://lfcarry.com/guides/destiny-2-weekly-rotation)是固定 URL 每周更新
    （本周实测 = Exodus Crash，与 Kyber 周报互证），解析"Grandmaster: <副本>"句式，只认映射表里认识的名字防抓到导航标题。
- **中文名全部本地化**：新 `build_rotation_zh.py` 生成 `manifest_index/rotation_zh.json`——
  遗失区域 hash→中文名（28 区全映射，如 exodus_garden_2a=黑色移民号花园2A）、宗师池英文名→中文名+宗师变体 hash+pgcr 横图
  （如 exodus crash=移民号的坠毁）、目的地英文名→中文名（manifest 用全称，短名 EDZ/Moon 走别名表）、
  奖励套装英文名→中文名（zh 物品清单按图标文件名反查，显示时剥部位后缀：「第七炽天使斗篷」→「第七炽天使 套装」）。
  **重跑时机：新赛季 / 首页出现新套装名时**；映射缺失运行时回退英文。
- **缓存**：遗失区域按天（北京时间凌晨 1 点换天）、宗师按周（周三凌晨 1 点），落盘
  `lost_sector_cache.json` / `gm_cache.json`；抓取失败各自独立容错，卡片出「没抓到」缺省行，不拖垮整卡。
- 第三方抓取用独立 httpx 客户端（不带 X-API-Key 出门）。卡片布局：突袭/地牢/宗师横图大卡之后
  插 3×3 遗失区域网格（rotdist 同款面板风格），页脚补数据来源。
- 踩坑：lfcarry 页目的地在句子里（"It is the Nessus strike"）不在括号里；d2lostsector.report 的
  勇士图标 URL 也含 `for-website/`，卡片切分 lookahead 必须要求数字 hash。

## 2026-10-02 `/队伍` 三连修：不在线态 + 选人界面误判 + 砖图标口径

- **选人界面/退出游戏不再被当成"进行中"**：退出后 204 组件会把上一场的 currentActivityHash
  挂好一阵子（真 hash、真开始时间），第二次截图事故「人都停在选人界面了还显示突袭已进行 17 分钟」。
  现在"真在打"必须同时满足 **真活动 hash + transitory.currentActivity 的 numberOfPlayers+Opponents ≥ 1**
  （打本时 ≥1，轨道 = 0）。实测样本：打本=6、轨道=0、下线几分钟=transitory 整个消失。
- **新增「不在线」态**：transitory 没了 → 卡片标 **不在线**（最后游玩时间 UTC+8 + 自己的生涯累计），
  不再硬编一场没在打的活动。
- **指标砖图标口径**（用户指定）：**完成数 = metric 自带的通用「突袭/地牢」图标；
  导师 = 对应副本的成就徽章**（印章 seal 图标）。印章按名字匹配：/称号 两个根（616318467/
  1881970629）下的一级子节点名与副本名 17/17 全同名，徽章图 = 印章**节点**的 displayProperties.icon
  （部分印章记录本身没有 icon，不能拿记录图标当准）。`raid_metrics.json` 加 `seal_icon` 字段。
- **短卡留白修复**：渲染器改复用浏览器页面时视口高度误设 800，卡片 CSS 会撑到视口高度，
  /帮助 等矮卡下半全空白——改回 200。
- 遗留已知项：exe 刚重启后的第一波查询若赶上 Bungie 慢窗口，个别成员资料可能 12 秒内没拉到而
  留空（显示 …mid 尾号），重发一次即可补上（有缓存）。

## 2026-10-02 `/队伍` 轨道态修正 + 响应缓存 / 渲染提速

- **轨道不再被当成"在打活动"**：官方在轨道待机时 204 组件照样给 `currentActivityHash`
  （实测 82913930，manifest 里查得到实体但没有任何名字）。旧代码把它当真活动，再拿
  `dateActivityStarted` 去历史里 ±10 分钟配对，正好配上刚打完的那一场 → 卡片显示
  「在打某某副本 · 已进行 12 分钟」。现在按「这个 hash 在 manifest/本地索引里有没有名字」
  判定真活动，占位 hash 一律算轨道。
- **轨道/组队态改成队内生涯总览**（用户口径：轨道不需要多余数据）：名单 = 官方实时队伍
  （1000 组件 `partyMembers`）；每人只显示 **生涯总时长**（200 组件各角色 `minutesPlayedTotal`
  求和）+ **成就点数**（900 组件 `profileRecords.score`，即游戏内凯旋分数）。不再抛
  `NotInActivity`（独自在轨道也照常出卡）。
- **进行中的活动按 hash 认名**：本场名单还没发布时，活动名与模式直接取 `activity hash`
  （本地索引中文名 → 线上 manifest），不再拿"上一把"的历史行顶替（实测：人在打救赎花园，
  卡片却写上一把的"试炼场"）。查不到名字才回退历史。
- **删掉"12h 内同名突袭名单补齐"**：那正是用户在轨道时凑出 6 人假名单的来源（真实队伍 3 人）。
  本场名单只认 本场 PGCR + 官方可见队伍，宁缺毋滥；每人的抓取加 12 秒上限，网络卡时宁可这一行留空。
- **响应缓存**（`destiny_data._CachedClient`）：GET/POST 统一走 TTL 缓存 —— 实时组件
  （204/1000）15s、突袭指标 1100 600s、生涯/角色 180s、对局历史 45s、PGCR/manifest 6h、
  按名字搜账号 300s；只缓 HTTP 200，读超时只在"很快就失败"时重试一次（等满超时的说明链路
  正堵，重试只会让用户多等一整个超时）。实测 `/队伍`（6 人突袭）：冷 34.9s → 7.5s，
  10 秒内重查 **0.0s**（全部命中缓存）。
- **卡片渲染提速**（`card_render.py`）：
  · 复用同一个 BrowserContext + 同一个页面（原来每次 `new_page()` = 每次一个空缓存）：
    带 6 张远程图的渲染 1190ms → 575ms；
  · **图标本地化**：渲染前把 bungie.net 图标拉一次存进 `icon_cache/`（URL 内容寻址、重启不失效）
    并内联成 data URI，浏览器不再等 CDN —— 60 张图标首次 15.0s → 8.0s，第二次 1.2s；
  · 等图改成「img 全 complete ≤2.5s + networkidle ≤1.2s」，不再死等满 8 秒；
  · QQ 通道启动时后台 `prewarm()` 预热浏览器/页面（首条查询省 ~1.8s 冷启动）。
- 实测网络前提：Bungie 从本机时快时慢（同一个接口 0.1~25 秒，还夹读超时），
  所以"能省的调用全省 + 每条请求都有上限"是这轮提速的主线，而不是加并发（并发 6 与并发 3 实测无差别）。

## 2026-10-02 `/队伍` 突袭卡改版：全队 + 每副本「完成数/导师」指标砖（对齐小日向）

- 突袭模式下每位队员渲染成一块：名字条 + **20 块指标砖**（10 个在役突袭的「完成数」+ 10 个「导师」，
  3 列网格、带图标与「职业生涯//突袭」来源行），与参考卡一致。
- 数据源：**官方统计指标（Metric）**——`build_raid_metrics.py` 从线上 manifest 的
  `DestinyMetricDefinition` 提取 10 个突袭的 clear/sherpa metric hash（同名的「职业生涯/赛季」两个
  变体取职业生涯），来源行由 `DestinyPresentationNodeDefinition` 祖先链解析（节点名 统计数据→
  展示为「职业生涯」）；取值走 Profile **组件 1100** 的 `metrics.data.metrics[h].objectiveProgress.progress`，
  和拉外观的 `100,200` 合并成一次请求。产出 `manifest_index/raid_metrics.json`（3KB）。
- 名单：进行中的突袭官方还没发布本场名单 → 用 **12 小时内同名突袭的上一场名单**补齐（连着打的
  概率高）；本场记录存在时（历史行与 `dateActivityStarted` ±10 分钟对上）优先用本场。
- 卡片状态：进行中 / 已结束 / 进行中·本场名单还没发布（+ 开始时间与已进行时长）。
- **砖图标 = 副本专属图标**：metric 自带的图标是通用的（20 个只去重出 2 个），活动定义图标也全是
  同一张「突袭」图；改用**收藏页同名展示节点的 displayProperties.icon**（每个副本一枚专属徽记；
  地牢的 预言/贪婪之握 没有节点图标，回退 metric 图标）。
- **地牢同样出砖**（`dungeons` 段）：7 个有「完成数」的地牢（深渊机灵/平衡 没有完成数 metric）
  + 有「导师」的（晚星之主/分离教义）；来源行「职业生涯//地牢」。
- **熔炉/智谋改为列全局、按阵营分组**：不再只列同队——按 entry 的 `values.team` 分成
  「你的阵营 / 对方阵营」两组（你的在前），无队伍信息的可见 party 成员并入你的阵营；每人的
  数据仍是该模式生涯（场次/胜率/K-D/…），不显示单场结算。

## 2026-10-02 生涯每赛季时长 + S8–S10 徽标 + 无符号触发词 + `/队伍` 指令（含进行中边界）

- **`/生涯` 每个赛季格新增「⏱ x 小时」**（`destiny_data.py` + `bot_cards.py`）：新增按日历史统计
  （`periodType=Daily&groups=General`，实测**单次窗口上限 31 天** → 按自然月分块）+ `season_time_cache.json`
  增量缓存（已封存的日期永久缓存，每次只补最近 ~10 天）；`career_report` 的 seasons 每项加 `time_h` 字段。
  口径 = allPvE + allPvP + allPvECompetitive 三类（实测互斥且完备；细分键会漏打击/智谋）。注意官方只保留
  约 2.5 年日粒度数据，更早赛季显示 0（Wj 实测 S8 仅剩 1.1h），脚注已注明。
- **赛季等级核查：验证无误**。21 个赛季逐角色原始「奖励+声望」progression 与卡片汇总全部对上
  （S21 100+309=409、S23 100+360=460…），prestige 叠加 >100 属篇章赛季长通行证，非取错 hash。
- **S8–S10 赛季角标换图**（`build_seasons.py` 新增 `_ICON_OVERRIDE`）：这三个赛季的
  `displayProperties.icon` 不可用——S8/S9 的图被 Bungie 在**同名 URL 上换过内容**（现为 416×416
  非徽标图），S10 本来就为空。改用各赛季**头衔印记展示节点**的官方 200×200 徽记（URL 带内容 hash，
  不会再被换内容）：S8 不朽 / S9 黎明 / S10 全知全能，三张都实际下载验证过。
- **无符号纯文本触发词**（`destiny_data.norm_key()`）：NFKC + 去非 `\w` 字符的归一化键，接入护甲
  `_armor_match`、武器 `search_weapons_full/search_weapons`、`search_perks`、护甲套装 `_norm_set`
  四条匹配链的兜底级（原始精确 → 原始子串 → 归一化精确 → 归一化子串，向后兼容）。「阿尔法鲁皮之脊」
  「鲁皮之脊」可精确命中；374 把带符号武器（希律-C 等）的无符号写法全覆盖；`build_exotic_armor.py`
  的 ALIASES 同步补了该条并重建 json。
- **新指令 `/队伍`**（`bot_fireteam.py` + `bot_cards.fireteam_card`，别名 队友/fireteam/d2队伍）：
  「当前在打什么 + 队内（同队）成员在该模式的生涯数据」——每人一块：徽标/名字/职业/光能 +
  该模式角色级生涯（突袭/地牢=通关/场次/通关率/击杀/死亡/K-D/时长/场均；PvP=场次/胜率/KD/
  最佳单场/最长连胜；智谋=场次/胜率/KD/存光尘），**不显示任何单场结算**（击杀/胜负那套已删，
  修掉「局内显示上一局判负」）。
  - **名单来源 = 本场对局的 PGCR**：在历史里找与 204 组件 `dateActivityStarted` 对得上的一场
    （±10 分钟），用它的 entries——突袭/地牢是**全队 6 人**（实测隐私设置不挡历史名单，潘通实测
    6/6 全员拿到），熔炉/智谋按 entry 的 `values.team` 过滤成**同一阵营**；窗口 +2 分钟外按「已结束」出卡。
  - **实测边界：匹配局开局时本场还没进历史**（新局 17:46 开局、当刻历史里没有它，对局数据出来
    才有名单；同样地突袭进行中也没有）→ 该状态给「进行中 · 本场名单还没发布」+ 先显示可见队伍成员。
  - 拿不到本场记录时的兜底 = Profile 组件 1000 `profileTransitoryData.partyMembers`（含自己、最多
    6 人，**受隐私限制**，实测 6 人队只见 1-2 人）；不在活动且只看得到自己 → 一条说明（含最后活动）。
  - 模式桶：优先本场 PGCR 的 `activityDetails.modes`；兜底运行时查 Manifest 实体
    `DestinyActivityDefinition.activityTypeHash`，实测 Raid=2043403989 / Dungeon=608898761 /
    Crucible=4088006058 / Gambit=248695599 / Vanguard=3652020199（**82913930 这类占位 hash 线上
    也没有名字**，所以活动名一律以历史行为准，不用 currentActivityHash 查名）。
  - 每人的模式数据 = 角色级 `/Character/{cid}/Stats/?groups=101,103&modes=N` 的 allTime
    （账号级 Account Stats 实测**忽略 mode 参数**）；角色取 `dateLastPlayed` 最近的（在打的人=在玩角色）。
  - **同一指令出现两张卡（一张正常卡 + 一张「查询失败」）的根因**：QQ 侧 sendMsg 超时
    （retcode 1200）时卡片其实已送达，但 nonebot 抛 ActionFailed → matcher 失败 → 兜底后处理器
    又补一张「查询失败」卡。修法：`_reply_image`/`_reply` 的发送层异常改为**只记日志不上抛**，
    后处理器再对 `ActionFailed` 名加保险丝。
  - 运维发现：启动器 `webview.start()` 是主线程最后一句 —— **面板窗口一关，进程就退出**（bot 线程是
    daemon）。今天两次「协议端未连接」（15:34、~17:00）大概率都是窗口被关/崩，NapCat 一直在重连，
    重开 exe 即自动恢复、无需扫码。

## 2026-10-02 赛季主图补齐 + 赛季角标 + 全量耗时日志 + /战绩 改名

- **S10–S15 的赛季主图补齐**（`build_seasons.py` 新增 `_ART_OVERRIDE` + `manifest_index/seasons.json` 新增 `art` 字段）：
  这 6 个赛季在 manifest 里**根本没有 `backgroundImagePath`**（`background_season_10..15.*` 全 404，
  又并发探了 4500 组命名/目录组合，非 404 命中 0；英文 manifest 同样没有；item 表 220MB 里也没有任何
  `/img/destiny_content/seasons/` 路径），只能用官方 key art 镜像并逐张人工确认：
  S10 英杰 `SotWCover.jpg`、S11 影临 `Season_of_Arrivals_Banner.jpg`、S12 狂猎 `SotHFullRes.jpg`、
  S13 天选 `SotC.jpg`、S14 永夜 `SotS.jpg`、S15 神隐 `SotL.jpg`（destiny.wiki.gallery，即 Destinypedia 上
  注明「from the Bungie.net Season of X page」的那批官方图）。取值规则 `art = 人工表 or bg`，
  21 条**全部非空**。顺带否掉一个猜想：赛季活动的 `pgcrImage` 是地图/过场截图，**不是** key art。
- **赛季角标放到每张赛季卡左上角**（`bot_cards.py`）：底图优先级改为 `art → bg → 赛季号派生渐变`
  （不再把 150×150 的 `icon` 拉成整块底图）；`icon` 改作左上角 22×22 角标（暗底 + 金描边 + 投影，
  压在任何亮度的 key art 上都看得清），`#号` 与角标并排、`Lv.` 在右上并加深色底片。S10 无 icon，
  只显示 `#号`，不留空洞。
- **所有耗时任务都有日志进度**（`destiny_data.py`）：新增通用装饰器 `_traced(label)`（`functools.wraps`，
  异常原样抛），一次覆盖 `history_report`(/战绩)、`node_report`(/锻造 /称号)、`eververse_store`(每日光尘)、
  `rotation_week`(轮换)、`get_pgcr`、`search_players_fuzzy`、`lifetime_stats` 等联网入口；
  `full_report`/`career_report`/`raid_report`/`mode_report` 补上 `▶ 开始` 与 `✔ 完成，实际耗时 X` 首尾行。
  口径统一为 **开始 → 进度条 → 预计剩余 + 预计完成时刻 → 实际耗时**；多步/翻页/逐场的走
  `log_progress`，秒级的走 `▶/✔ + 实际耗时`。纯本地索引查询（武器/perk/护甲/掉落图）不联网，不加日志免得刷屏。
- **`/历史` 改名为 `/战绩`**（`nonebot_plugins/destiny2.py`）：`/战绩` 为主命令，`/历史` 保留为别名，
  帮助文案（`destiny2.py` / `bot_cards.help_card()`）同步改掉。

## 2026-10-02 耗时接口的日志进度条 + 卡片底图裁切/空白修复

三件事：日志里能看见长任务跑到哪了、职业横幅底图不再只显示一条、"没图"的赛季卡不再空白。

- **后台日志进度条 + 预估时间**（`destiny_data.log_progress()`）：需要拉接口、要跑一会儿的活儿
  统一打一行 `[进度] 标签 [████░░░░] 62.0% (312/503) · 已用 0:48 · 预计剩余 0:29（约 12:34:56 完成） · 速度 0.3s/项`。
  - 后台任务（PVP/PVE 生涯武器、热力图、宗师）另加 `[任务] ▶ 开始 / ✔ 完成 · 总用时` 两行；
    进度条与面板 `/api/bot/jobs` 共用同一份 `JOBS.done/total`，不额外记账。
  - 逐场拉 PGCR 是主要耗时段：翻页阶段打「翻取对局历史：角色 i/N · 第 p 页 · 已收集 m 场」，
    拿到总量后按 done/total 给 ETA；`_collect_matches()` 新增 `on_page` 回调供调用方接日志。
  - 热力图这种「翻到 2019-06 为止」没有天然总量的，按**已扫到多早**相对 2019-06→今天 折算百分比
    （`_scan_pct()`），进度条与 ETA 才有意义。
  - 非任务的耗时查询同样接上：`career_report`（3 角色 × 5 批分模式统计）、`full_report`（/玩家）、
    `mode_report`（/pvp /pve /智谋 近期战绩）、`raid_report`（/raid /地牢 翻页）。
  - 节流：默认「距上次 ≥2s 或百分比涨 ≥2」才打，避免刷屏；日志走 `print(flush=True)`，
    即 exe 同目录的 `exe_stdout.log`。
- **名片（徽章底图）改成完整长条**（`bot_cards.py`）：`emblemBackgroundPath` 是 474×96 的长横条
  （三个职业徽章 + 立绘），原来放进 ~900×58 的横幅里用 `background-size:cover`，只能看到中间一条，
  用户看到的「只有一半」就是这个。现在统一用 `.strip`：盒子 `aspect-ratio:474/96` + 底图
  `background-size:100% 100%`——盒子比例与图完全一致，**既不横向裁切也不拉伸**，整条完整露出；
  叠字加 `.veil` 暗色渐变保证可读。
  - `/生涯` 顶部玩家名牌区从纯文字标题改成 `class='namebar strip'` 长条（底图用主玩角色的徽章底图，
    玩家名 / 守护者等级 / 最高光能 / 总时长 / 上次在线全部叠在条上）；分职业 pane 的 `.cbanner`
    与 `/玩家` 的角色行 `.char` 同改。
- **赛季卡主图：不再有空白格**（`build_seasons.py` + `bot_cards._season_bg_style()`）：
  底图优先级 **官方 `backgroundImagePath` → 赛季 `displayProperties.icon`（150×150 主视觉）→ 赛季号派生渐变**。
  - 事实：manifest 里 **S10–S15 没有 `backgroundImagePath`**（`background_season_10..15.*` 一律 404，
    英文 manifest 也一样），但 **S11–S15 有 `displayProperties.icon`**；只有 S10 两者皆空。
  - `build_seasons.py` 现在把 `icon` 一并写进 `manifest_index/seasons.json`（新增字段，旧字段全保留）。
  - S10–S15 的真 key art 只存在于 Bungie 的营销素材/新闻页/旧版 manifest，当前接口拿不到；
    对齐全赛季真 key art 只能人工收集后单独托管。小日向那类 bot 大概率就是自维护了一张 key art 表。

## 2026-10-02 `/生涯` 改版为小日向式生涯面板

`/生涯` 从「三模式汇总」升级成生涯面板：**逐赛季网格 + 分职业分模式时长 + 三模式生涯**，
排版照小日向、皮肤仍用自有 v2 深灰。

- 新数据层 `destiny_data.career_report()`：只打 GetProfile / GetHistoricalStats，**不跑 PGCR**，
  比 `full_report()` 快一个量级（本机实测 6~10s 出图）。`/玩家` 仍用 `full_report()`。
- **分模式时长**：`GetHistoricalStats` 各模式的 `allTime.secondsPlayed`。`modes` 一次最多约 20 个，
  全 75 个会 ErrorCode 3，代码按 **15 个一批**分段再合并；返回键名 ≈ 模式定义 `friendlyName` 归一化，
  但聚合模式键名不一致，兜底表见 `destiny_data._MODE_KEY_FIX`。`modes.json` 新增 `key` 字段
  （`build_modes.py` 生成），键名↔模式号可自动对上。
- **赛季等级 = 奖励等级 + 声望等级**：赛季定义的 `seasonPassProgressionHash` 常年为 0，
  真正的 hash 在 `DestinySeasonPassDefinition`（经 `seasonPassList[0].seasonPassHash` 关联）。
  该口径与用户给的小日向截图**逐条吻合**（深渊 100+309=409、终愿 460、异端 231…）。
- **历史赛季的游玩时长官方拿不到**：`periodType` 只认 AllTime / Daily，Daily 仅保留最近 6 天，
  故赛季卡只显示赛季名 / 起止 / 天数 / 等级，不显示时长。
- 新增 `build_seasons.py`：生成 `manifest_index/seasons.json`（中文赛季名 / 起止 / 背景图 / prog / pres），
  `build_manifest.py` 一并调用。旧的英文赛季名与缺失的背景图一并修好。
- 卡片新增 `.sgrid/.scard/.cpane/.chips` 样式；分模式标签按 PvE（绿）/ PvP（红）/ 智谋（蓝）左侧色区分。
- 已知差异：当前赛季（manifest 里的 28 凯旋纪念碑）数值与用户截图的小日向快照对不上，
  属 manifest 与对方赛季边界漂移，非映射错误。

## 2026-10-02 下线 DIM 板块（背包 / 配装 / 配装器 / 管理器）

不再需要自建 DIM 页面，整块移除；本记录以下的 DIM 相关条目仅作历史留档。

- 删除源码：`dim_data.py` / `dim_web.py` / `dim_ui.py` / `dim_user.py` / `dim_opt.py` /
  `dim_host.py` / `build_dim_index.py` / `dim_user.json`，以及内置官方 DIM 静态站 `dim_app/`（约 98MB）。
- 删除生成索引：`manifest_index/dim_items.json`（8.7MB）/ `dim_buckets.json` / `dim_categories.json` /
  `dim_loadouts.json`。
- `webui.py`：去掉 `dim_web` / `dim_host` 的挂载与顶部导航「DIM背包」，OAuth 回跳不再转交 `dimauth-`。
- 打包/部署：`D2Query.spec` 去掉 `dim_*` hiddenimports，`deploy_exe.ps1` 去掉 `dim_app` 同步段。
- 配置：`.env` / `.env.example` 去掉 `DIM_HIDE_NAV`，`.gitignore` / `.zcodeignore` 去掉 dim 条目。
- 新增 `manifest_index/community_dim.json`（Clarity 社区洞察）**保留**：它给武器 perk 用，与 DIM 页面无关。

## 2026-10-02 QQ 官方机器人「预设指令」配齐（指令面板 + 单聊自定义菜单）

开放平台「高级设置 → 菜单与指令」在网上只写了「通过 API 配置」，没有可视化界面——新增
`qq_official_panel.py` 直接打官方 OpenAPI 把面板写进去（默认 dry-run，`--apply` 才写，`--list`
看线上现状，`--check` 按限额离线自检）。

- 接口：域名 `https://api.bot.qq.com`（旧 `api.sgroup.qq.com` 留作回退），鉴权头
  `Authorization: QQBot {access_token}` + `X-Union-Appid`，token 由
  `bots.qq.com/app/getAppAccessToken` 用 AppID/AppSecret 换（复用 `qq_official_creds.json`）；
  用到的接口是 `POST/GET/PUT/DELETE /v2/panels`、`PUT /v2/panels/{id}/target`、`GET/PUT /v2/menu`。
- **生效范围只能在创建时定**：全局面板要用 `POST /v2/panels` 带 `target_type=all` 建；
  `PUT /v2/panels/{id}` 只改元素与备注、改不了生效范围，`/target` 接口对全局面板直接报 40030021。
  这里踩过一次坑：早先探测 `target_type=specific` 能不能用时建的测试面板**其实建成功了**（脚本把
  后续请求的报错当成了建面板失败，没走到删除那步），后一次「更新」正好更新到它，群里那份于是变成
  `specific` + 空关联 = 对谁都不生效——用户看到的就是「面板突然没了」。脚本现在只复用
  `target_type=all` 的面板，其余一律删掉重建，`--list` 也会打印 target。
- **限额**：单面板最多 20 项、元素名 ≤14 字符、描述 ≤30 字符、菜单按钮名 ≤10 字符、子菜单 ≤5 个，
  **一个汉字算 2 个字符**（脚本 `--check` 按此校验）。另外列表接口在面板刚建好时可能只回一条
  （异步生效有传播延迟），别据此以为「一个场景只能有一个面板」。
- 写入内容：群聊 + 单聊各一份 20 项指令面板（帮助/绑定/武器查询/perk查询/武器筛选/护甲查询/
  护甲套装/掉落/每日光尘/轮换/玩家/生涯/raid/地牢/pvp/pve/历史/常用武器/pve生涯武器/宗师，
  每条带中文描述，就是小日向那种打 `/` 弹出的入口）；单聊另配 7 个底部按钮
  （武器查询/perk查询/护甲查询 + 战绩·资料·记录·账号 四个子菜单），把挤不进面板的
  `/智谋` `/热力图` `/称号` `/锻造` `/我的` `/解绑` 也带上了。
- 平台会把写入的元素名开头的 `/` 去掉，客户端按 `type=command` 自动补回，所以清单里照常写 `/指令`。
- 指令清单与描述都在脚本顶部 `PANEL_ITEMS` / `MENU_ITEMS`，改完重跑 `--apply` 即生效；
  首次 `--apply` 会把改动前的线上配置备份到 `qq_official_menu_backup.json`（已 gitignore）。

## 2026-10-02 全部查询指令接入 QQ 官方机器人（NapCat 之外的第二条通道）

### 平台层 bot_platform.py（新文件）

- 同一套指令要同时跑两种适配器，差异全部收进 `bot_platform.py`：平台判定、身份/群标识、
  @ 判定（`at_target`/`at_other_only`/`at_me_only`）、消息段构造（文本/图片/@）、回复打包、
  多图拆分、官方群开关。业务侧（`destiny2.py`）不再直接碰任何适配器类型。
- 关键平台差异（都写在模块 docstring 里）：
  - **身份**：NapCat 是 QQ 号，官方只有 `member_openid`/`user_openid` → 绑定表按 openid 另存一份
    （同一 store，`uid()` 取值不同，天然不撞车）；
  - **图片**：NapCat 走 `base64://` 直发；官方必须走富媒体上传（`MessageSegment.file_image`），
    且**一条消息只带一个媒体**（适配器 `_extract_qq_media` 只取最后一个媒体段）；
  - **被动回复**：官方群 5 分钟 / 最多 5 条，超窗口发不出去（没有主动推送）→ 长任务
    （热力图/生涯武器）先回「统计中」卡片再回结果，超时那条只能记日志、让用户重发（有缓存后秒出）；
  - **群开关**：官方群用 `bot_config.json` 的 `official_groups`（group_openid 列表）单独管，
    与 NapCat 的 `enabled_groups`（QQ 群号）分开——混在一起面板会把官方群判成"未勾选"。
- 官方通道的 @ 回执（回复里 @ 发起人）默认关：官方群 @ 语法（适配器渲染成 `<@openid>`）
  没在文档里得到确认，`bot_config.json` 的 `official_at_back=true` 可开，不用重新打包。

### destiny2.py 去 OneBot 化（`nonebot_plugins/destiny2.py`）

- 删掉 `from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment`：
  注解改用基类 `nonebot.adapters.Message`（`CommandArg()` 的 Depends 不做类型校验，两个适配器都注入），
  `msg_logger` 从 `on_type(MessageEvent)` 改成 `on_type(Event, rule=Rule(bp.is_message_event))`
  （两个适配器的 `MessageEvent.get_type()` 都是 `"message"`）。
- 回复统一走 `bp.reply_msg(event, *segs)`：NapCat 群聊前缀 `at+空格`，官方按 `official_at_back` 决定。
- `/掉落` 多图：NapCat 一条消息带全部；官方拆条（最多 4 张 + 1 条说明，被动回复 5 条上限）。

### bot_runtime.py：组合驱动 + 官方适配器（`bot_runtime.py`）

- 驱动从 `~fastapi` 换成 **`~fastapi+~httpx+~websockets`**：前者供协议端反向 WS（8901 不变），
  后两个是官方适配器要的（出站 WS 连网关 + HTTP 调开放平台 API）。组合驱动由 nonebot 的
  `combine_driver` 现场合成，不冲突。
- 凭证读 `qq_official_creds.json`（模板 `qq_official_creds.example.json`，已 gitignore）；
  没配/还是占位文本就**不挂官方适配器**，NapCat 通道照常。官方适配器注册失败也只打日志
  （`_serve` 里 try/except），不牵连 NapCat。
- 官方 intent 只开 `c2c_group_at_messages`（位 25：群 @ + 单聊），与冒烟脚本里验过的一致。

### 验证

- **离线双通道烟测** `_rtest/official_harness.py`：真插件 + 桩渲染 + 假 bot，事件按真实模型造、
  走完整 nonebot 管线。22 项全通过：NapCat（/帮助 /武器查询 @bot直查 /掉落 @别人不响应 /我的
  /指令@某人）/ 官方（/帮助 /武器查询 @bot直查 /@bot带指令词 /perk查询 /护甲查询 /护甲套装 /掉落
  /轮换 /武器筛选 /我的（openid 绑定）/私聊C2C /指令@某人），并断言官方每条回复都带被动 `msg_id`、
  媒体内容就是卡片 PNG。
- **真机**：`_rtest/official_live.py` 用真凭证连官方网关 → READY（bot `11824830429619768898`），
  并把 1.05MB 真实卡片图上传成功（`srv_send_msg=False`，不打扰群）。
- 打包：`D2Query.spec` 的 hiddenimports 补 `bot_platform`/`nonebot.adapters.qq`/`nonebot.drivers.httpx`；
  `deploy_exe.ps1` 外置同步清单加 `bot_platform.py`（漏了它 exe 一起就 ImportError）。

## 2026-10-02 全站卡片统一 v2 深灰风格 + 角落水印 + 重启后「未连接」修复

### 查询卡片统一到武器卡/护甲卡的深灰家族（bot_cards.py / webui.py）

- 除武器查询、异域护甲查询（原本就是该风格）外的**全部卡片**统一：900px 宽、页底 `#0f1113`、
  面板 `#16181b`、内块 `#1b1e22`、描边 `#2a2e33`、正文 `#e8e6e3`、弱化 `#9aa0a6`、注释 `#6d737b`、
  绿 `#35c66b`、金 `#d4b26a`、蓝 `#4b8fd4`；旧蓝调（`#0b0f19`/`#141c2e`/`#5ea8ff`/`#ffd76e`/`#7dff9c`）全部退场。
  覆盖 /玩家 /生涯 /pvp /pve /智谋 /raid /地牢 /历史 /热力图 /称号 /锻造 /生涯武器 /宗师
  /武器筛选 /perk查询 /护甲套装（单套·一览·全量页）/每日光尘 /轮换 /help 提示卡与各类候选列表。
- 版式对齐：主标题改大号白字横幅面板；分组标题改「绿色竖条 + 小号加大字距」；数值行从斑马纹改
  逐条浮起小格；统计块/热力图容器等半透明底改实心面板；提示卡标题横幅化（保留左侧色条表状态）。
- **实现方式是配色迁移 + 13 条结构补丁，不重写 markup**：每条结构补丁在源码里只命中 1 处
  （就是共用 CSS 里的那一条规则），其余全是纯换色；所以武器卡/护甲卡的 `w2-*`/`a2-*` 规则 0 改动，
  改前改后两张基准图字节数完全一致（可当作没碰坏基准的自检）。
- 网页端同步：`webui.card_page` 外壳内容列宽 800→940px（卡片定宽 900px）；站点自身样式表与卡片
  共用一套色，一起换成新配色（导航胶囊/输入框/查询按钮同样绿高亮）。
- 预览与改造工具在 `_rtest/v2/`（已随 `_rtest/v2/` 一并 gitignore）：`v2skin.py`（PALETTE + 补丁表 +
  `restyle()`）、`fetch_cards.py`（从本机 8900 实例抓战绩类卡片真实 HTML）、`preview_cards.py before|after`、
  `make_cmp.py`（拼「改造前|改造后」对比图）、`index.html`（总览页）、`before/`·`after/`·`cmp/`。
  战绩类卡片的「改造后」是抓线上旧 HTML 再套同一套 `restyle()`，所以预览=改源码后的实际效果。

### 角落水印

- `bot_cards.CSS` 与 `webui.CARD_CSS` 各加一条 `body::after{content:"雷尼克斯联合-1 · by Wj"}`：
  绝对定位挂在 `body` 上，`full_page` 截图时正好落在整张图右下角；`body` 同步留 30px 底部空白，
  不压页脚与最后一行内容。**改文案要两处一起改**。

### 重启后「未连接」修复（d2query_launcher.py / bot_runtime.py）

- **根因一：主界面端口会抢 8901/8902。** `_free_port` 从 8900 往上顺延且不设保留口，重启瞬间
  8900 若因 TIME_WAIT 被误判占用，主界面就落到 8901 上——而 8901 是协议端 NapCat 的
  `onebot11_<QQ>.json` 里写死的反向 WS 地址，于是协议端连到网页应用上，面板永远"未连接"，
  且重启也治不好（下次可能再抢一遍）。现在 `_free_port` 跳过 `bot_runtime.RESERVED_PORTS`
  （=8901/8902），端口契约统一收在 bot_runtime 一处。
- **根因二：端口判据与 uvicorn 不一致 + 等待太短且失败即死。** 旧探测用裸 `bind`，而 uvicorn 绑定时
  开 `SO_REUSEADDR`，于是 TIME_WAIT 被当成"被占用"（探测说不行、实际绑得上），60 秒等不到就
  `nonebot.run()` 抛错、bot 线程直接死掉且不重试。现改为 `port_usable()` 两步判定：① 先 `connect`
  探测，有人在监听就绝不抢（避免第二个实例把已在跑的端口抢绑过来）；② 连不上再开 `SO_REUSEADDR`
  试绑（TIME_WAIT 不再误判）。等待上限 60s→300s（Windows TIME_WAIT 约 4 分钟），并且
  `nonebot.init` 之前的失败会自动重试一次（init 之后再失败不能重来，只记日志）。
- 现场证据：该实例 `exe_stdout.log` 里有 `[Errno 10048] ... ('127.0.0.1', 8902)` 报错两行，
  说明端口顺延确实会踩到保留口。

## 2026-10-01 武器卡 v2 改版 + 使用率管线 + @bot 直查扩容 + 异域护甲查询

### 武器卡片 v2（照设计稿完全重排，bot_cards.weapon_card）

- 全新 900px 深色版式：头部横幅（品质/伤害/弹药 meta + 赛季行 + screenshot 大图 + 赛季水印全幅低透明叠加）、
  特长金名横条、版本胶囊（`#N 赛季文案`，当前绿描边+"当前"徽标）、左素体数值 + 右热门组合 2×4、
  perk 分列（发射管蓝/弹匣琥珀/特性1绿/特性2紫/起源橙红，第一名列色高亮+百分比）、
  大师杰作行、武器模组行、异域催化换肤、双栏页脚（雷尼克斯联合 · 武器图谱 / Bungie Manifest · light.gg 社区快照 · 非精确概率）。
- 素体数值对齐游戏内面板：主栏全部条形（含变焦/弹药生成），分隔线后底栏右对齐数量组
  （每分钟发射数/弹匣/充能时间/弹药容量等），**后坐方向=游戏内同款半圆仪表**
  （SVG 实心扇形自右端按 v/100 逆时针填充，100=满半圆即完全竖直，缺口方向=水平漂移方向）。
- 属性顺序修正为游戏内面板序（STAT_ORDER 重排：射速/充能在前→冲击/爆炸范围/弹头速度→射程/稳定/操控/填装/弹匣→辅助瞄准/变焦/后坐→空中效率/弹药生成→剑类）。
- 赛季年份修正：S24-26=年7、S27 起年8（旧公式 (S-4)//4+2 算错，现按 d2ai d2-season-info 发布日期分界推算，SEASON_YEAR 表+未知赛季外推）。
- **非锻造异域不显示使用率区**（热门组合面板整体隐藏、素体数值拉通全宽，靠 _wcat 品质 q==6 或 catalysts 判定）；
  传奇无数据时显示"暂无社区使用率数据"占位、页脚自动切回 Starside/Clarity 署名。

### light.gg 社区使用率管线（weapon_usage.py）

- `await weapon_usage.get_usage(item_hash)`：提供者链 = 本地快照 `manifest_index/weapon_usage_snapshot.json` →
  自定义端点（cwd `usage_config.json` 的 endpoint/proxy，或 `D2_USAGE_ENDPOINT`/`D2_USAGE_PROXY`，可指向自建 CF Worker）→
  d2foundry → light.gg（仅配置 proxy 才尝试：httpx 走代理，失败再 Playwright 真 Edge+proxy；解析失败 HTML 落 `logs/usage_debug/`）。
- 缓存 7 天 / 负缓存 24h / 同 hash 单飞 / 全局节流 10 次/分钟；combos=特性1×特性2 选取率乘积 top8（页脚注明非精确概率）。
- `build_plug_meta.py` → `manifest_index/plug_meta.json`（2574 插件 hash→中文名/图标，供大师杰作/模组 join）；
  `build_weapon_usage.py --proxy <代理> [--limit N]` 批量生成快照（节流 1.5s、断点续跑）。
- **CDP 通道（已打通，主用）**：`start_edge_debug.bat` 一键把 Edge 以独立调试 profile + `--remote-debugging-port=9222`
  重启（cookies 从默认配置复制，light.gg 验证直接复用；**新版 Edge 对默认配置目录会忽略调试端口，必须独立 user-data-dir**）。
  提供者③零配置接入用户已验证浏览器：开 item 页取 HTML → 真实 DOM 解析（community-average 五列选取率 + trait-combos
  真实组合百分比 + masterwork-stats）；标题仍挑战则轮询等放行，失败冷停 10 分钟防连击。
- **批量快照**：`build_weapon_usage_fast.py`（页内 fetch 免渲染 + 6 页面错峰并发，实测 ~3 条/秒，11 分钟跑完；
  导航模式另有 `build_weapon_usage.py --via cdp --workers`）产出 `manifest_index/weapon_usage_snapshot.json`
  （断点续跑、每 25 条落盘）。**覆盖率 1591/2154（74%）**：失败 545 把全部复核为落日/冷门武器——
  light.gg 对它们本就没有 community-average 区块（页面 200 正常、只是无数据），当代在用武器全覆盖。
  大师杰作行按「大师杰作：」前缀与武器模组分拣（light.gg 的 MW Bonus 列表混着备用弹匣等模组），并剥掉重复前缀。
- 校验与风控：`challenge-platform` 是 CF 注入在**正常页面**上的运行时脚本，不是挑战标志（只看开头 4KB 的
  "Just a moment/请稍候/cf-chl-"）；6 页同时开种子页会触发挑战，**错峰 3 秒启动**即可。
- 实测：42435996 完美逆行 端到端出数据（高爆弹药 28.9%、回转弹药 52.5%、组合「回转弹药+诱导推销」17.42%、大师杰作操控性 66.4%），
  中文名运行时 join（weapons_full/plug_meta），source=cdp。新武器无 community-average 属正常无数据。
- 备用通路照旧：快照 → 自定义端点（D2_USAGE_ENDPOINT） → d2foundry → 代理 light.gg（D2_USAGE_PROXY）。
- 接线：`destiny2.py _weapon_reply` 出详情卡前 `get_usage(int(top["hash"]))`，异常兜底 None。

### 触发方式扩容（destiny2.py）

- `@bot 武器名` 等价 `/武器查询`：支持结尾版本序号（`@bot 完美逆行 2`）、无命中回候选列表；
  旧 @bot 直查的缺陷（序号不拆、`d2武器` 前缀失效）已修。
- `@bot perk xxx` 显式强制 perk 查询；`/护甲查询`（别名 d2护甲）新增；
  @bot 自由文本判定顺序：武器 → 护甲 → perk；`/`、`d2` 开头不接（防双回复）。
- 已知边界：NapCat 把 @机器人 解析成 qq=0（群名片形式）时适配器不置 to_me，直查不触发。

### 异域护甲查询（/护甲查询 + @bot 护甲名）

- 数据 `manifest_index/exotic_armor.json`（`build_exotic_armor.py` 生成）：141 件 = 348 个 Manifest hash 去重，
  starside.work/exotic-armor（可达、无 CF、静态 HTML）提供特性详版文案/赛季/评测，本地 Manifest 提供 hash/图标/中文名并交叉校验。
- 卡片 `bot_cards.armor_card`（a2-* 同风格）：**不显示六维**（用户要求），特性名+完整数值描述；
  职业金（相对主义/唯我主义/坚忍克己）显示 perk_cols 两列 18+18 之灵全池（描述含 +18.2% 等数值），注明"共 324 种组合"；
  永劫教派臂甲显示三学派文案。aliases 外号（腚眼甲/滑板鞋/职业金等 7 条）在 build 脚本顶部 ALIASES 扩充后重跑。

### /轮换 新增「扭曲星球轮换」板块

- `scrape_starside_rotation.py` 增加 `parse_distortion()`：解析 starside 轮换页的扭曲 7 列表 → `rotation_pairs.json`
  新增 `distortion` 段（cycle[7] + slots[周几][小时] 共 168 时段 + 口径注释）。校验：周三 02:00=幽梦之城、
  周六 15:00=王座世界、周二 23:59=涅索斯，与页面逐格一致；北京周三 01:00（周复位）恰为 7 整循环回到表头。
- `destiny_data.distortion_now(dt=None)`：按本机时钟（UTC+8）算当前目的地/时段起止/下一个目的地与倒计时/今日剩余时段。
- `bot_cards.rotation_card` 追加扭曲板块：当前时段横幅高亮（绿色）+ 下一个目的地（金色）与切换倒计时 + 今日剩余时段网格；
  突袭/地牢板块原样。Bungie 接口失败但扭曲可用时仍出卡。

### 踩坑

- **spec 误收 58MB 缓存**：数据构建用了 `raw_items_en_lite.json`（英文名速查），D2Query.spec 的 `_MI_SKIP` 只排了
  raw_items/raw_plugsets，会把它打进包——已加入 skip 名单。新增 manifest_index/*.json 时记得检查 skip 名单。
- **starside 页脚混进末条描述**：页面最后的之灵/学派描述会吞进"更新 20xx/数据源：…/©/ICP备案"页脚文本，
  build_exotic_armor.py 已加 `_strip_footer()` 按标记截断（职业金列Ⅱ末条与永劫第三学派共 6 处，复跑后清零）。
- light.gg 数据源全线不可达的排查记录与解封路径见记忆/上文管线节。

- 已重打包旁路部署 `dist_new\D2Query\`（exe+_internal+外置模块，不杀运行中的实例）。

## 2026-10-01 新增 /宗师 —— 宗师征服 + 宗师警戒战绩（对齐 nightfall.report）

- **指令**：`/宗师`（别名 `宗师战绩` / `征服` / `gm战绩`），可带 `@某人` 和赛季参数（`s27` 等），
  默认当前赛季；网页首页新增「宗师」页签（后台任务模式，`/start_gm` + `/gm_result`）。
- **数据源**：全量对局历史（mode=0）按活动名前缀筛——征服是独立模式(18)，
  `mode=7` 过滤拿不到；活动 hash → 中文名来自 `manifest_index/activities.json`
  （含 6 个「宗师征服：X」和「宗师日落: X」轮换条目）。对局历史页自带 completed/duration，
  不用逐场拉 PGCR，一个赛季 3000+ 场也就翻几页历史，秒级出结果。
- **输出**：征服 / 宗师警戒两栏，每项 通关次数/通关率(含失败)/最快/平均（只算通关场）；
  顶部四个指标：宗师征服进度（成就 340857458）、终极征服进度（914587616）、
  历史镀金次数（伟大征服者 4018593209 的 objective progress）、宗师对局数。
- **踩坑**：伟大征服者记录的镀金 objective 是 completionValue=4 / progress=累计通关数，
  直接取 progress 当镀金计数比 nightfall.report 显示大 1（截图 6 vs 实测 7，
  应是截图后又完成了一次，姑且按 progress 展示）。
- 已重打包旁路部署 `dist_new\D2Query\`。

## 2026-10-01 修「实际在线但面板显示等待扫码登录」+ 新增退出重置按钮

- **现象**：Bot 实际在线能回指令，面板 QQ 登录卡片却一直「NapCat 运行中 等待扫码登录…」。
- **根因**：面板查询登录态走 NapCat WebUI 的 `QQLogin/CheckLoginStatus`，
  `napcat_runtime._api_post` 只在 HTTP 401 时换新凭证重试；但 NapCat 凭证失效时
  返回的是 **HTTP 200 + `{"code":-1,"message":"Unauthorized"}`**，不检查业务码导致
  过期凭证被永久缓存，每次查询都拿到空 data → isLogin 永远 false。
  （顺带发现：`CheckLoginStatus` 响应里本来就没有 uin 字段，登录后 uin 由
  `QQLogin/GetQQLoginInfo` 补齐。）
- **修复**：`_api_post` 按业务码 `code != 0` 判定失败并换新凭证重试一次；
  已用注入坏凭证方式对真实 NapCat 验证自愈。
- **新功能**：面板登录态新增「退出并重置（需重新扫码）」按钮 →
  `POST /api/napcat/reset` → `napcat_runtime.reset()`：taskkill 进程树杀掉
  NapCat/QQ（WebUI 没有 Logout API，重置只能杀进程），清凭证缓存，回到未启动态。
  按钮带风控提示（短时间反复重登可能触发 QQ 风控）。
- 已重打包并旁路部署到 `dist_new\D2Query\`（只覆盖 exe+_internal，未动正在跑的实例）。

## 2026-10-01 修「扫码登录后协议端未连接」——8901 绑定失败 + WS 配置没生效

- **现象**：二维码修复后能扫码登录（NapCat 已登录），但面板顶部一直「未连接」，
  NapCat 的反向 WS 没接到 NoneBot。
- **两个叠加原因**：
  1. 部署脚本杀旧 exe 后 800ms 就起新 exe，旧实例 8901 上的已建立连接进 TIME_WAIT
    （最长 4 分钟），新实例 NoneBot 绑定 8901 报 10048 → bot 线程直接退出，
    面板/网页还能用（8900 正常），但协议端永远没人接。修法：`bot_runtime._run()`
    启动前用裸 socket 试绑 8901，最多等 60 秒再 `nonebot.run()`。
  2. NapCat 运行实例里 `websocketClients` 是空的——watcher 下发配置那次没成功，
    之后没有任何兜底。修法：面板轮询 `/api/bot/status` 时，若「已登录但未连接」，
    低频（≥60s 一次）调 `ensure_ob11_via_api()` 热更反向 WS 配置，SetConfig 即时生效。
- **现场处置**：手工调 `ensure_ob11_via_api()` 后「雷尼克斯联合-2」立即上线
  （`/api/bot/status` 返回 connected:true）。

## 2026-10-01 修「二维码一直加载中」——napcat.mjs 分包文件缺失

- **现象**：面板「QQ 登录」卡在"二维码加载中…"，NapCat 壳进程活着但 6099 永远不监听、
  napcat.log 0 字节、logs/ 目录空，反复重启 NapCat 也一样（凌晨 03:18–04:00 五次全失败）。
- **根因**：`napcat.mjs` 是 rollup 打包产物，开头 `import { … } from "./conout-wiJ7YKRd.js"`
  引用同目录分包；dist 的 napcat_shell 里恰好缺这一个文件。loader 里 `import()` 失败是
  异步 rejection，**一个字都不打**——QQ 照常启动成普通 QQ，NapCat 永远不初始化，
  面板自然永远拿不到实时码。与"用户自己的 QQ 在跑"、风控、Defender 都无关（全部排除了）。
- **修法**：
  - 把 `napcat_shell/conout-wiJ7YKRd.js` 补进 dist（源仓库里一直都在）；
  - `napcat_runtime.start()` 启动前新增 `_missing_napcat_files()` 自检：解析 napcat.mjs 的
    `from "./xx.js"` / `import("./xx.js")` 引用，缺文件直接报
    「NapCat 文件不完整，缺少 …」，不再静默；
  - `deploy_exe.ps1` 新增 3.6 步：部署时把 napcat.mjs + conout-*.js 同步进 dist 的
    napcat_shell（该目录由 setup_napcat.py 一次性建出、打包流程从不重建，是这次断层的根源）。
- **排查手段（下次直接用）**：给 `loadNapCat.js` 包一层 fs.appendFileSync 追踪 +
  try/catch，重跑 BootMain 就能看到 import 失败的真实栈。
- 期间把自己的 QQ 和机器人 QQ 都杀过做对照实验：补齐 chunk 后两者**可共存**，
  NapCat 照常出码（数据目录冲突假说被证伪）。

## 2026-10-01 修「光尘商店获取失败」——token 刷新链路加固

- **现象**：01:25 私聊 `/每日光尘` 回「光尘商店获取失败」，但面板显示已授权；
  用 dist 里 01:25 落盘的新 token 复测，接口本身是通的（说明是那次刷新/请求
  赶上了 Bungie 偶发故障）。
- **根因**：`bungie_auth.access_token()` 把刷新异常整个吞掉返回空串，上层误报
  「未授权」；且 `authorized_get()` 对非 JSON 返回直接抛 JSONDecodeError
  （Bungie 偶发回 HTML 错误页）；并发刷新时一次性 refresh_token 还会互相作废。
- **修法**：刷新失败抛出真实原因（`Bungie token 刷新失败：…`）；非 JSON 响应
  报「HTTP 状态码 + 多为官方临时故障」；刷新加 asyncio.Lock、锁内重读避免竞态；
  `eververse_store` 里 membership() 吞错时再调一次接口透传真实异常。
- 已重新打包部署到 dist/D2Query（QQ 占用 napcat_shell/guild1.db 不能整目录重建，
  改旁路打包 dist_new 后只覆盖 exe + _internal），烟测通过后停掉自己的实例。
- **事故记录**：第一次打包没走旁路，PyInstaller 清 dist 时把根目录用户数据删了
  （.env、bungie_token.json、seen_players.json、dim_user.json），删到被 QQ 锁住的
  guild1.db 才中断。.env 已从仓库根恢复；token 无法恢复需重新授权；
  seen_players.json（本地玩家索引）丢失会随查询重建；dim_user.json 的 DIM 标签/配装丢失。

## 2026-10-01 修「授权换 token 报 redirect_uri does not match」

- **根因**：Bungie 对 redirect_uri 的校验只发生在**换 token** 这一步，authorize 那步
  不拦——所以会出现「浏览器拿到了 code、换 token 却 400」。发 code 的授权页用的
  地址（可能是旧会话/旧配置留下的，或 Bungie 应用里登记的还是旧值）与当前
  `BUNGIE_REDIRECT_URI` 不一致就会这样。
- **修法（自愈）**：`bungie_auth.exchange()` 支持传 redirect_uri，先试「实际发 code
  用的那个」再退回配置值；手动授权从粘贴的回调地址里取（`redirect_from_text`），
  自动回跳从落地 URL 里取。面板未授权块明示程序生效的回调地址，方便对照 Bungie
  应用后台登记值。

## 2026-10-01 错误卡片带上异常类型名

- `/每日光尘` 超时失败时卡片显示「Bungie 接口暂时不可用：」冒号后是空的——
  httpx 超时类异常的 `str()` 为空串。现统一经 `_exc_msg()` 拼 `类型名: 消息`，
  光尘商店与本周轮换两处生效，卡片能直接看出是 `ReadTimeout` 还是别的错。

## 2026-10-01 /武器筛选 显示同名武器全版本

- 此前筛选结果按武器名去重（Manifest 里同名武器按 perk 池/赛季分多个 hash），
  山巅这类多赛季复刻武器只出一条。现改为与参考图一致**逐版本列出**：
  只有「名字+赛季+水印」全同的才算重复条目；同名出现多个版本时在名字旁加
  赛季角标（首发 / S 号，赛季映射复用 weapon_versions.json 即 d2ai 水印表）。
- 网页 /catalog 图鉴页本来就在每条版本上算筛选，无需改。

## 2026-09-30 修「掉线后重新扫码一直二维码超时」

- **根因（僵尸 QQ 进程）**：`stop()`/卡死重启只 kill `NapCatWinBootMain.exe` 主进程，
  它拉起的 QQ.exe 是独立子进程会残留，继续占着 NapCat WebUI 端口 6099。之后
  `start()` 里 `login_qr_ready()` 误判「已在跑」认领僵尸进程，所有二维码 API 都打在
  半死的登录服务上 → 面板永远「正在获取新二维码…」，落盘兜底又显示旧的过期码，
  扫了必报「二维码超时」。
- **修复**：新增 `_tree_kill`（taskkill /T 连子进程）与 `_kill_webui_holder`
  （netstat 找到占 6099 的进程，只认 QQ.exe/NapCatWinBootMain.exe 再杀）；
  `stop()`、`_restart_login_for_qr` 改为树杀+清端口占用者；`start()` 认领前先用
  `login_status()` 探活，没响应就清僵尸走全新启动；qrcode.png 兜底只认 2 分钟内的
  （旧码扫了就是超时，显示出来纯属误导）。
- **掉线本身**（NapCat 日志：`快速登录错误： 登录需要手Q验证`）是 QQ 风控踢下线，
  代码治不了；NapCat 4.18.28 + QQ 9.9.36 组合偏旧（社区反馈该版本段掉线频繁，
  NapNeko/NapCatQQ#1728）。掉线后重启扫一次码即可，不会再卡在「二维码超时」。

## 2026-09-30 修 DIM 登录链路 + 面板授权回跳 + 二维码卡死自愈

- **DIM 构建产物路径烘焙错误（登录全挂的根因）**：打包 DIM 时 PUBLIC_PATH 被 MSYS 路径转换污染，
  React basename / publicPath / service-worker scope / authReturn 兜底路径全被烘焙成
  `C:/Program Files/Git/dim/`。已在 dim_app 四个 js 里替换为 `/dim/`（main-469a81dc /
  main-8a2dbe3b / runtime-d08515a3 / lo-worker-e99b7bc4 / authReturn-*）。
- **DIM 授权回跳打通**：dim_app 里补上了构建漏掉的 `authReturn.html`（手写壳 + 自带 runtime +
  authReturn chunk，chunk 自执行入口依赖 794/827/966）；DIM 的 authorize 请求注入
  `redirect_uri=https://127.0.0.1:8902/bungie/callback`（走我们已有的 TLS 口，不再撞
  `https://127.0.0.1:8900` 的 http 口报 ERR_SSL_PROTOCOL_ERROR）；DIM 的 state 强制
  `dimauth-` 前缀，`/bungie/callback` 见到该前缀就 302 到 `/dim/authReturn.html`，
  由 DIM 自己（烘焙的 client_secret）换 token，与面板授权互不干扰。
- **面板授权回跳有返回了**：/bungie/callback 成功页 2.5 秒自动回 /panel，失败页加
  「← 返回面板」链接（原生窗口没有后退键）。面板/接口里过时的
  「Redirect URL 填 8900」提示文案改成 8902。
- **二维码卡死自愈**：NapCat RefreshQRcode 一直 restarting/回旧码时（登录服务卡死），
  同一张码挂超 110 秒且续期失败 → 自动重启 NapCat 重新出码（10 分钟限一次、已登录绝不动、
  不受 5 分钟启动冷却限制）；`_qr_url` 全部改走不抛错版本，接口挂了不再把面板二维码打 500。
- 注意：DIM 的 service-worker 会缓存旧 bundle，用户首次打开如果还是旧行为，刷新一两次即可。

> 这些「实测踩出来的」结论是改代码前必读的约束，不要按直觉改。

## 2026-09-30 套装别名全面复核 + 模糊搜索兜底

- **别名按页面「来源」字段逐条重核**（此前按套装名猜错了 4 条）：共振狂怒实际来源是门徒誓约（vow/**vod** 都指它）、奈扎雷克的梦魇来源是梦魇根源（ron/rotn/噩梦根源）、第三百夫长=救赎花园（gos）、传承之誓=深岩墓室（dsc）。删掉了两个没把握的自创别名（沙漠永昼/深海幽灵）。
- 地牢套全部补齐别名：st/破碎王座、poh/异端深渊、prophecy/预言、goa/贪婪之握、duality/二象性、sow/守望者尖塔、gotd/深渊机灵、wr/战争领主的废墟、vh/晚星之主、sd/分离教义。
- **来源（副本名）可直接搜**：`/护甲套装 玻璃拱顶`、`?q=永恒沙漠` 都能命中对应套装（精确匹配）。
- **模糊兜底**：精确/包含全没命中时按编辑距离匹配（2-5 字符容 1 错、6+ 容 2 错，最多返回 3 个候选），vog 打成 vof、一愿打成"一愿1"都能中；vod 已是正式别名不受影响。
- exe 已重打包部署（destiny_data 在 exe 内，json 在 _internal/manifest_index —— 改别名表后两个位置都要同步）。

## 2026-09-30 护甲套装页加入查询站顶部导航



- 网页 `/armorsets` 改为**全量详情版**：56 套 2/4 件效果全文照搬 Starside（`bot_cards.armor_sets_full_card`），效果数值金色加粗（含正负号）；顶部加搜索框（回车提交 `?q=`，别名通用）。QQ 卡片不变：不带名字仍是索引卡。
- QQ 索引卡套装名**右侧留白处加效果标签简介**（回转/减伤/构造体等，Starside 官方 tags，最多 4 个），便于玩家筛选。
- `_hl_nums` 正则修正：数值加粗带上 `+/-` 号（此前 `+30` 只加粗 `30`）。

- 顶部导航新增「护甲套装」（`/armorsets`），排在「本周轮换」后面；排版复用 QQ 卡片（`bot_cards.armor_sets_card` / `armor_set_card`），外面套 `card_page` 网页外壳。
- 支持查询参数：`/armorsets` 出全部套装索引；`/armorsets?q=一愿套` 等出单套全文，检索逻辑与机器人共用 `d2.search_armor_sets`（别名通用）；没命中出「没找到套装」提示页。
- 已用 TestClient 逐路由断言（索引/单套/别名/未命中 + 导航项存在）；webui.py 打在 exe 里，已重打包并同步外置文件。



## 2026-09-30 /护甲套装（Starside 中文套装效果查询）

- 新指令 `/护甲套装 [套装名]`（别名 `套装效果` / `d2套装` / `套装`）：数据抓自 Starside 中文护甲套装资料页（56 套，2/4 件效果全中文+精确数值，`[?]` 待核实、方括号 PvP 数值照搬）。
- 不带名字 → 全套装索引卡（按目的地/先锋/熔炉/智谋/突袭/地牢/活动分组，每套列 2/4 件效果名）；带名字 → 单套全文卡（数值金色加粗）。
- **别名触发**：维护在 `scrape_starside_armor_sets.py` 的 ALIASES 表（页面里没有，重抓不会丢）。已配：炽天使套装/炽天使套、一愿套/一愿/遗愿套/最后一愿/lw（伟大狩猎=最后一愿）、vog/玻璃宝库（埃希恩记忆）、kf/王陨（欧里克斯记忆）、梦魇套（奈扎雷克的梦魇）、vow（传承之誓）、ce/克罗塔（克洛塔记忆）、ron（共振狂怒）、救赎花园/gos（集体心灵）、救赎边缘（应许之物）。归一化会自动去掉结尾「套/套装」、忽略大小写空格。
- 新文件 `scrape_starside_armor_sets.py`（抓取 → `manifest_index/armor_sets.json`，页面更新后重跑）；`destiny_data.py` 加 `search_armor_sets` / `all_armor_sets`；`bot_cards.py` 加 `armor_set_card` / `armor_sets_card`（CSS 追加 as-* 类）。
- 部署：外置文件同步了 `nonebot_plugins/destiny2.py`、`bot_cards.py`、`manifest_index/armor_sets.json`；exe 已重打包（destiny_data 打在 exe 里）。

## 2026-09-30 内置官方 DIM（替换自研 DIM 板块）

- 不再自己重写 DIM：官方开源版 DIM（DestinyItemManager/DIM v8.144.0）源码构建后挂到 webui 的 `/dim` 路径，顶部导航「DIM背包」直达。
- 源码在 `F:\智谱Zcode数据存储\DIM_src`（浅克隆，勿删，重构建要用）；构建命令：
  `cd DIM_src && MSYS2_ENV_CONV_EXCL=PUBLIC_PATH 设置 WEB_API_KEY/WEB_OAUTH_CLIENT_ID/WEB_OAUTH_CLIENT_SECRET（取自 BOT/.env） PUBLIC_PATH=/dim/ && corepack pnpm -s bundle --env=release --node-env=production`
  产物 dist/ 去掉 *.map *.br 后拷到 `BOT/dim_app/`（部署脚本会同步到 `dist\D2Query\dim_app`，exe 外置，不打进 exe）。
- **大坑**：Git Bash 会把环境变量 `PUBLIC_PATH=/dim/` 自动转换成 `C:/Program Files/Git/dim/` 写进产物，构建必须带 `MSYS2_ENV_CONV_EXCL=PUBLIC_PATH`。
- OAuth：DIM 用浏览器自己的授权（localStorage 存 token），与服务器端 bungie_auth.py 互不影响；构建时嵌入了 .env 里的 BUNGIE_API_KEY / CLIENT_ID / SECRET。**用户需在 Bungie 应用管理页新增回调地址 `http://localhost:8900/dim/index.html`**（Bungie 要求 http 仅限 localhost）。
- 隐藏功能栏：`.env` 的 `DIM_HIDE_NAV`（逗号分隔：inventory,progress,vendors,records,loadouts,organizer），启动时注入 CSS 到 dim_app/index.html；About/What's New 默认隐藏。改过值要删掉 dim_app/index.html 里的 `<style id="dim-hide-nav">` 重新注入（或重拷产物）。
- 登录页建议关掉 DIM Sync（我们没配 DIM 官方 API key，同步会失败；标签/配装存在浏览器本地 IndexedDB，和官方 DIM 本地模式一致）。
- 旧自研 DIM 代码（dim_web.py 等）保留未删；dim_app 不存在时自动回退到旧板块。



# D2 查询 QQ 机器人（本地版）

NoneBot2 + OneBot V11 + Bungie API。协议端用 NapCat。

## 本地图形化查询界面（网页版）

```
.venv\Scripts\python webui.py            :: 启动（占用 8900 = 查询页，8901 = nonebot 的 OneBot 反向 WS）
powershell -NoProfile -ExecutionPolicy Bypass -File stop_webui.ps1   :: 停止并释放两个端口
```

> **源码版和打包版不能同时跑**：`webui.py` 和 `dist\D2Query\D2Query.exe` 都要绑 8900 / 8901，
> 同时开会报 `[Errno 10048] bind ('127.0.0.1', 8901)` 然后 exe 直接退出。
> 跑 exe 之前先执行一次 `stop_webui.ps1`。
> （Windows 下 venv 的 `python.exe` 是个会再拉子进程的 stub，所以一个服务会看到两个 python 进程，停止脚本两个都杀。）

**2026-09-30 更新⑤（DIM 板块对齐官方 DIM：界面/拖拽/标签/配装器）**：把 DIM 板块从「自绘页面」重做成**跟官方 DIM 一致**的五个页面，
配色、稀有度底色、格子、物品弹窗、标签全部照 DIM 源码取色（`#e8a534` 主色、异域 `#ceae33`/传说 `#522f65`、格子 1px 白边 + 大师 `#eade8b`、弹窗 `#0a0a0f`/`#151523`），
新增两个文件层：`dim_ui.py`（主题 CSS + 全站公共 JS：物品弹窗、拖拽、标签、搜索语法、设置抽屉）、`dim_user.py`（标签/备注/自建配装落本地 `dim_user.json`）、`dim_opt.py`（配装器组合搜索）。逐条：
① **背包页** `/dim` 重做：顶栏加「背包/进度/配装/配装器/管理器/主站」页签 + DIM 式搜索框，四列（泰坦/猎人/术士/保险库）按背包桶分区并显示 `已用/上限`（满了变橙），
已装备单列一区（3 武器 + 5 护甲的槽位显示为可放置的空框）；格子显示光等/数量/锁定/标签图标/大师之作金边，不命中的搜索项压暗保留位置（DIM 同款）；
② **拖拽搬运**：拖物品到别的角色/保险库列 = 搬运，拖到装备槽 = 装备（槽位不对会拦下来提示），拖动时目标列高亮；写操作后先在本地即时生效再隔 9s/20s 对账（Bungie 快照有十几秒滞后，这坑之前踩过）；
③ **物品弹窗**：悬停/左键出 DIM 式弹窗（名称/类型/光等/属性条/Perk 描述/插槽模组/来源/备注框 + 标签按钮行），悬停弹窗不可交互（否则会盖住相邻格子），左键点开的才固定；右键出操作菜单（装备/搬运/标签/详情）；
④ **标签与备注**：收藏♥/保留⚑/丢弃✖/注入⚡/归档🗄 五档，快捷键 `shift+1..5`、`shift+0` 清除、`e` 装备当前高亮，可写备注（支持 `#标签`）；实测 **Bungie 官方没有开放标签接口**（`SetTag`/`SetItemTag` 均 404，只有 `SetItemLockState`/`SetQuestTrackedState`，DIM 自己也是本地存），所以标签存 `dim_user.json`（程序目录，原子写、升级不覆盖）；
新增锁定/解锁（`SetItemLockState`）；⑤ **搜索语法**对齐 DIM：`is:weapon|armor|equipped|locked|masterwork|tagged|note`、`tag:favorite…`、`notes:xx`、`not:<词>`、裸词匹配名称/类型/来源，配 13 个快捷筛选 chip；
⑥ **设置抽屉**（对应 DIM 设置页的显示项，存 localStorage）：图标大小、默认/物品排序方式（名称/类型/稀有度/数量/光等/护甲数值）、排序方向、隐藏已标记丢弃、装备优先；⑦ **配装页** `/dim/loadouts` 重做：
「游戏内配装」（每角色 20 套，直接读） + 「DIM 配装」（自建，不占游戏配额） + 「当前装备」，自建配装支持 **创建/编辑/编辑副本/分享/导入/删除/一键应用/两套对比**（按属性总和对差）；
**配装编辑器**按 15 个槽位排列，点任意槽位挑/换装备（候选来自三个角色 + 仓库，支持搜索），可选图标（24 个）+ 底色，分享变成 `/dim/loadout?d=<码>` 链接、别人打开自动导入；其中「槽位号从 dataset 出来是字符串、和物品桶号数字严格比较永远不等」这个坑让候选列表一度恒为空，已补回归断言；
⑧ **新增配装器** `/dim/optimizer`（DIM Loadout Optimizer）：属性优先级（1/2/3 级，先按 1 级属性排）+ 每项最小值/最大值 + 假定大师之作（无/传说/异域，每件 +2）+ 指定异域护甲（一套最多 1 件，照游戏规则）+ 排除已装备/已锁定/跳过「丢弃」，
穷举 5 个护甲槽输出前 24 套并显示合计/档位条，可**保存配装**或**直接装备**、两套**对比**；搜索做了四层优化（Pareto 支配剪枝 → 每槽取前 90 件 → DFS + 分支定界，用「剩余槽位每项属性的最大可能值」推进上界 → 节点数封顶 300 万并如实标注「未搜完」），
实测真实仓库（约 1900 件）里 24 套组合**0.01~0.1 秒**出结果、200 多万种组合不再截断；搜索逻辑用暴力枚举交叉验证过（含异域上限/大师之作/优先级排序三种条件，6 组随机数据结果完全一致）；⑨ **新增管理器** `/dim/manage`：表格式整理（名称/类型/稀有度/光等/标签/位置，点表头排序，位置筛选），勾选后**批量打标签 / 批量搬到仓库或角色**；⑩ **进度页**沿用并换肤；⑪ 顺手修掉一个既有 bug：`perks.json` 里的图标路径本身是绝对地址（个别还写成 `https//` 少个冒号），`_icon_url` 会再拼一次域名导致 `https://www.bungie.nethttps//...` 直接 DNS 失败、Perk 图标裂图；
⑫ 新增浏览器自检脚本 `_rtest/dim_ui_check.py`：跑 Edge 无头浏览器逐页断言（格子数/分区/筛选生效/悬停弹窗/标签写请求/右键菜单/拖拽搬运与拖拽装备发出的请求体/编辑器候选非空/配装器出结果/管理器勾选），**写操作全部用 route 拦截，不打到 Bungie、不改账号**；去掉重复的 `#pop`/`#menu` 元素、`shellInit()` 未被调用（导致悬停/拖拽/右键全都没绑定）等启动期问题后，五个页面 46 项断言全部通过、控制台零报错。

**2026-09-30 更新④（DIM 板块：背包仓库 / 成就 / 配装）**：新增顶部导航「DIM背包」，进去是三个独立页面——
`/dim` 背包仓库（四列：泰坦/猎人/术士/保险库，按背包桶分组，格子带赛季水印竖条、光等、数量、锁定、装备中描边；
点格子出搬运菜单「装备/移到某角色/移到仓库/从邮政官取回」，鼠标悬停出详情面板：属性条/Perk/插槽模组）、
`/dim/triumphs` 成就（凯旋分 + 分类树 484 节点 + 4160 条记录，支持搜索与「只看未完成」）、
`/dim/loadouts` 配装（每角色 20 套游戏内配装，显示名称/图标/装备清单，可「一键应用」——不在身上的自动先从仓库搬过来）。
**实现要点（都是实测踩出来的，别照直觉改）**：
① **分量编号**——Bungie 的 `DestinyComponentType` 极易记错：`304=ItemStats`、`305=ItemSockets`、`302=ItemPerks`、
`103=ProfileCurrencies`（不是 104，104 是 ProfileProgression，写错会静默拿不到货币）。
② **仓库分组不能用上报的桶**——`profileInventory` 里武器/护甲上报的 `bucketHash` 一律是「一般」(138197802)，
要按**物品定义里的真实桶**分组（所以在 `manifest_index/dim_items.json` 里存了 `bucketTypeHash`）。
③ **邮政官不是独立分量**，它混在 `characterInventories` 里，桶是「遗失物品」(215593132)；
消耗品/模组/任务属账号级清单，也挂在 `profileInventory` 里。
④ **写接口必须带 `membershipType`**（读接口只靠 URL 就够）：不带会回 `DestinyInvalidMembershipType`，
报文里的 @membershipType 还是没替换的宏。`EquipItems` 收的是 `itemIds`（实例 id 列表），不是「实例+hash」。
⑤ **写完立刻回读拿到的是旧快照**——Bungie 档案接口是快照式的，搬完 4 秒还读不回新位置，最久一次约 40 秒才对上
（实测：搬运调用返回 Success 但连读 10 秒都不见变化，很容易误判成「功能没做对」）。所以前端**先在本地把物品挪过去、立即重绘，
再隔 4 秒 / 16 秒各对账一次**，而不是写完马上重读。
⑥ **单件详情走 `/Profile/{mid}/Item/{iid}/`**，它的返回是「扁平」的（`instance`/`perks`/`stats`/`sockets` 直接挂顶层、
各自带一层 `data`），跟档案接口的 `itemComponents.xxx.data[实例]` 完全不是一个形状。
⑦ 图片水印沿用「裁左侧竖条」的做法（格子只有 46px，所以竖条按 36% 宽裁）。
新增文件：`dim_data.py`（数据层）、`dim_web.py`（路由+三个页面）、`build_dim_index.py`（建索引）、
四个索引 `dim_items.json`(8.3MB，30253 条物品瘦身表) / `dim_buckets.json` / `dim_categories.json` / `dim_loadouts.json`。
**单账号自用**：全程用「Bot 面板」里已授权的那个 Bungie 账号（实测现有 token 的读写权限都够：
仓库 102 / 角色背包 201 / 装备 205 / **配装 206** / 成就 900 都能读，`TransferItem` 也能写）。
现有页面（玩家查询/图鉴/Perk/光尘商店/轮换/Bot面板）一行未改，只在导航加了一个入口。
（改的是 `webui.py`(2 行) / `D2Query.spec`（新增 4 个索引 + 2 个模块） / 新增 `dim_data.py`、`dim_web.py`、`build_dim_index.py`。
重建索引：`.venv/Scripts/python.exe build_dim_index.py`（`--items` 只重建物品表，`--defs` 只重建三张小表）。）

**2026-09-30 更新③（图鉴排版修正）**：① **武器预览图被赛季水印压住的问题**：Manifest 的 `iconWatermark` 是 96×96、78% 全透明的 PNG，真实有效像素只有**左侧一条竖带 + 左上角一个赛季徽记**；此前照 `/weapons` 的做法整张盖在图标**上面**（`z-index:2`），于是武器图标左侧被压掉一块。现在图鉴卡片改成**裁左边缘那条竖带单独做一条 20×56 的「赛季竖条」**（`.wmslice`：`overflow:hidden` + 水印发 56×56 贴左），武器图标 100% 可见，赛季徽记也照旧看得见。图标/竖条都加了深色底，图片没加载完时是"暗色方块"而不是空洞。② **卡片重排**：由「竖版居中卡（92px 大图 + 三行居中文字，177px 高）」改成**横版卡（赛季竖条 20px + 图标 56px + 右侧三行文字，74px 高）**——同样一屏能看的武器从 4×4 变成 5×7；网格改用 `repeat(auto-fill,minmax(254px,1fr))` 铺满宽度（不再右边留空），长名字/长框架用省略号截断，卡片高度统一。③ 筛选面板整体压缩（chip 内边距/字号/行距收小），标题与搜索栏宽度从固定 1180 放开到 `max-width:1440px`，宽窗口能多排一列。④ **同样的水印压图问题在 `/weapons` 武器查询页也存在**（同一份「整张盖在图标上」写法），一并改成左侧 32×96 的赛季竖条 + 96px 图标完整显示。（只改 `webui.py` 的图鉴页与武器查询页 CSS/卡片模板 → 已重新打包部署。**未动**：搜索联想下拉里 32px 的小缩略图仍是整张覆盖——图太小，裁竖条会把图标挤成一条，要改说一声。）

**2026-09-30 更新②（全武器图鉴 /catalog）**：① 新增**武器图鉴**页（顶部导航「武器图鉴」，武器查询页也加了「全武器图鉴」按钮），一次拉全量 2208 把武器在浏览器里做筛选/搜索/计数，不再往返接口。筛选维度与游戏/参照站一致：**类型 / 框架 / 元素 / 弹药 / 槽位 / 品质**，每个取值带**动态计数**（随其它维度联动、0 命中的压暗），组内多选是并集、跨维度是交集，「全部」一键清该维度，另有「可锻造（有图案）」与「重置筛选」；筛选面板可「收起筛选」。搜索框对**武器名 / 类型 / 框架 / 元素 / 来源 / Perk 名**做子串匹配，并复用 `/武器筛选` 的社区叫法表（喷子→霰弹枪、绿弹→特殊、主手→主武器、微冲、筒子…），多个词之间是与。排序支持 默认/名称/类型/品质/射速，首屏渲染 160 张、其余「加载更多」增量出图（图片 lazy）。② **点击卡片在图鉴页内弹出武器详情层**（iframe 套 `/weapon?hash=..&embed=1`，Esc 或按钮关闭），筛选状态与滚动位置不丢；`embed=1` 只去掉顶部导航和「返回搜索结果」，详情页的 **Perk 分列单选 / 大师杰作 / 模组 / 异域催化 / 属性条实时联动 / 悬停参数浮层** 全部照旧可用（已用真实浏览器逐项验证：换枪管 2 条属性变化、选大师杰作 4 条变化、选模组 1 条变化、悬停浮层出参数）。③ **同名版本合并**：同一把武器在 Manifest 里按 perk 池分多个 hash（2208 条只对应 1318 个名字），默认按名字合并成一张卡，可用「合并同名版本」开关切回逐条列。合并**不是丢掉其它版本**——筛选与计数仍在每条版本上算，**同名不同元素/不同 perk 池的版本照样能被筛出来**（例：「猝死」有电弧/烈日/虚空三版，搜「猝死」+元素=虚空 仍命中，卡片显示命中的那一版）。④ 新增索引文件 `manifest_index/weapon_catalog.json`（= `weapon_filter_index.json` + 品质 tierType），由**新脚本 `build_weapon_catalog.py`** 生成（读现成的 2208 条筛选索引 + `raw_items.json` 取 tierType，约 2 秒），`/api/catalog/index` 直接 FileResponse 这份文件（已加进 `D2Query.spec` 的 datas）。（改的是 `webui.py` / `D2Query.spec` + 新数据文件 → 已重新打包并部署到 `dist\D2Query\`；重跑 `build_weapon_filter_index.py` 后要再跑一次 `build_weapon_catalog.py`。）

**2026-09-30 更新**：① **修复「本周轮换获取失败 / Event object is bound to a different event loop」**：exe 里同时在跑三个事件循环（8900 主界面、8902 Bungie 授权回跳用的 HTTPS 口、8901 QQ bot），而 httpx 的连接池会在建连时把 `asyncio.Event` 这类原语绑到**当时那个循环**上——模块级共用一个 `AsyncClient`，第二个循环再取用池里的连接就整页报错（`/rotation` 只是最先被撞上的页面；`destiny_data.CLIENT` 已改成**按事件循环各持一个客户端**，见 `client()`，weakref 键）。同理 Playwright 的浏览器实例只能被创建它的循环驱动，`card_render` 的浏览器与串行锁也改成按循环各一份，否则 QQ 侧（8901）渲染卡片会报同样的错。② **金枪「异域催化」重做**：催化改为按**武器自己的催化插槽**定位（初始插件是空催化插槽 / 插件类别含 `catalyst`），不再依赖「武器名+催化」的中文名匹配——此前 全面爆发、蠕虫低语、泰拉巴、真相、D.A.R.C.I、爱莲娜之誓、焚天者誓约 等一批金枪因中文名对不上（如「低语催化」≠「蠕虫低语催化」）或插件是 `[PLACEHOLDER]` 占位符而**整块不显示**；带催化的武器 130 → **145 把**，Clarity 社区数据里有催化的 140 把已 100% 覆盖。③ **催化补上数值与效果**：Manifest 里很多金枪的催化 `investmentStats` 是空的（流明、真相、全面爆发…），现在同时接入 Clarity 社区英文原文（`Grants 30 Reload Speed`、`Nanite Damage is increased by 25%`）与 Starside 中文催化说明（新增 `scrape_starside_exotic.py` 抓 starside.work/exotic-weapon → `manifest_index/exotic_catalysts_zh.json`，如 劲弩「被动提供 +20 操控性、+1 弹匣容量（5 → 6）与「创世」Perk」）；网页详情页与 QQ 卡片都按「属性增减 → 中文说明 → 沙盒 Perk 效果 → 社区英文原文 → 解锁条件」展示，中文优先、英文兜底。同名催化的两个 hash 版本（原版/重制版各挂一个插件）自动合并去重，引力子尖刺那类「XX改装」四选一的多催化保留。④ `test_sweep.py` 断言 54 → 57 项：新增 `/rotation` 不得出现「获取失败 / Event loop」、`/eververse`、以及全面爆发的催化数值（走的就是这次修的插槽定位路径）。（改的是 `destiny_data.py` / `webui.py` / `card_render.py` / `bot_cards.py` + `manifest_index/weapons_full.json` → 需重新打包 exe；`bot_cards.py` / `card_render.py` 同时是外置模块，由 `deploy_exe.ps1` 一起同步。）

**2026-09-28 更新**：① 修复玩家搜索只认 Steam 的 bug（此前 PSN/Xbox/Epic 玩家一律报"没找到"，现按跨存档主平台解析）；② 武器/Perk 功能查询按钮改为始终显示，无需先查玩家；③ 首页缓存跨查询保留（最多 150 页，同玩家/换玩家切回已看过的标签秒开）；④ PVP 生涯武器界面重做为排名表格：前三名奖牌色、击杀占比条、出场/场均/占比列，且统计范围从"最近 90 场"改为**本赛季**对局（按 Manifest 赛季起止过滤，上限 250 场）；⑤ 锻造界面重做为按武器类型分组的网格（共 20 类 + 末尾"异域催化"组），过滤 Bungie 占位符脏数据；⑥ 热力图重做为**按赛季分组的全历史月历**（小日向式）：每月一张迷你日历、同一赛季同一色相（黄金角取色、亮度按时长）、赛季标题栏带名称，数据走后台任务全量翻页（每角色上限 60 页×250 场，2019-06 前自动停止），赛季定义缓存在 manifest_index/seasons.json；⑦ 称号页修复分组交错 bug（上轮排序表误伤称号分组）并改用与锻造一致的分组网格布局（称号/传承称号两组，镀金金色标签），全标签页排版已逐项检查；⑧ 武器查询/详情页：赛季徽标（watermark）修正为全尺寸图标覆盖层（此前缩成角落小图标导致几乎不可见）；详情页 Perk 改为 DIM 式多列布局（框架|枪管|弹匣|特性各成一列），模组/大师/公约收进默认折叠区，Perk 悬停即显示浮动参数卡（名称+属性增减+说明），不再依赖点击；⑨ Perk 选择改为**每列单选**（枪管/弹匣此前可无限多选，已修复；点击已选中项可取消），选中状态按 hash 比对，属性条实时联动，已用真实浏览器验证；⑩ 特性 Perk 补充**数字参数**：接入 Clarity 社区数据库（DIM 社区见解同源，manifest_index/community_dim.json），插件悬停浮层新增"社区数据 EN"分区，显示带精确数值的效果说明（如热力四射 1|2 层：15|30 稳定性、20|40 后坐方向），覆盖 2172/2208 把武器；enrich_weapons_ci.py 负责注入（同时产出 perk_ci.json 供 Perk 查询页用），重跑 build_weapon_details.py 后需再跑一次；⑪ 详情页插件分类按 DIM 逻辑重写：着色器/皮肤/战斗特效/空插槽/1-9阶过渡插件全部过滤（模组区不再有几百个着色器），大师组只保留"大师杰作：XX"每属性一项+击杀记录器，金枪新增**固定配件列**（枪管/弹匣/枪托不可选）与**催化剂列**（名称=武器名+"催化"匹配，带属性参数与社区数据），传奇武器新增枪托列；⑫ Perk 查询页接入 perk_ci 索引，结果卡片直接显示属性增减标签与社区数值说明；⑬ **Perk 数值全面中文化**：接入 Starside《武器 PERK 详解》全中文数据（scrape_starside_perks.py 抓取 starside.work/weapon-perks，395 条含精确数值与增强 perk 数值，manifest_index/perk_zh.json），悬停浮层与 Perk 查询页优先显示中文数值说明（覆盖 2207/2208 武器），Starside 未收录的（如催化剂）回退 Clarity 英文；⑭ 玩家联想**徽标裂图修复**：此前把空的 `userInfo.iconPath` 拼成 `https://www.bungie.net` 造成裂图，现改用**最近上线角色的角色徽章**（components=100,200），依次回退到 Bungie 头像、平台图标（steam/psn/xbox）；武器联想的赛季徽标改为整张覆盖在图标上，不再错位成第二张图；⑮ 锻造分类改为**按槽位**（主武器 / 特殊武器 / 重武器 / 异域催化，取自 Manifest「模式和催化」节点结构），不再是按武器种类分 20 组；卡片副标题显示武器种类，组标题保留 x/y 进度；⑯ 称号显示**每个称号的完成进度 x/y**（如 梦魇根源 13/20、克洛塔的末日 14/18）并带进度条：记录状态改为合并 profileRecords + characterRecords（components 200,900），此前只看 profileRecords 会漏掉突袭/地牢类凯旋导致进度偏小；已获得但子记录被赛季重置的称号（如 冠军勇士）回退用完成记录自带目标 progress/completionValue；⑰ 修复**点地牢副本出现 0/0/0 与「副本 <哈希>」**：详情链接此前不带活动模式，地牢点进去仍拿突袭历史（mode 4）去筛，必然为空；现在详情透传 `amode`（4/82）与 `base`（副本名，合并普通/大师/永恒等难度变体）；⑱ Raid/地牢标记**按 raid.report 补全**：每行显示 通关 / 无暇 / 大师 / 单人 / 双人 / 三人 / 单人无暇 / 双人无暇 / 三人无暇（计数为 0 的徽章压暗），并标注该副本打过的难度（标准/普通/大师/永恒/传说/宗师…）；详情页展开同样维度 + 按月筛选；⑲ **PvE 不再被标成"失败"**：对局标签改为按模式判定——只有 PvP/智谋这类竞技活动才用 胜利/失败，PvE（打击/突袭/地牢/剧情…）一律用 通关/未通关；"做完任务却显示失败"与"副本显示负"都是这个原因（此前只看 `standing==0` 判胜负，PvE 的 standing 恒为 1）；⑳ **对局模式正确化**：删掉了手写的模式表（其中 19 被错标成"梦魇"、10 被错标成"智谋"、48 被错标成"倾诉"），改为从 Manifest `DestinyActivityModeDefinition` 生成 `manifest_index/modes.json`（75 条，含中文名/类别/父子关系），再按父子关系从 `activityDetails.modes` 里挑**最具体**的那个模式——于是 试炼(84)、铁旗占领模式(43)、铁旗死斗/霸权、混战、生存、灭绝、占领模式：快速游戏 都能正确显示；PvE 侧显示 突袭任务/地牢/打击/剧情/探索/遗失区域/攻势/无序边界 等；㉑ **PVP / PVE / 智谋 页重做**：PVP 与 PVE 页现在同时给**生涯统计（Bungie 官方 allTime：击杀/死亡/K-D/场次/胜率/**精准击杀**/协助）**与**近期战绩（跨角色，每角色最近 100 场）**，近期块新增 胜率 / 胜-负 / 当前连胜连败 / KDA / 场均击杀 / 平均效率 / 对局时长；㉒ 新增**模式细分表**：按具体玩法汇总场次、胜率（绿≥50%/红<50%）、K/D、KDA、场均击杀；㉓ PVE 的"通关率"排除**探索/巡逻**（本身没有完成概念，此前会把通关率拖到 ~53%）；㉔ 智谋页补上胜率（官方已下线智谋聚合接口，改为跨角色对局历史聚合 + 胜负）；㉕ 总览页的三模式块补上胜率，并把 PVP/PVE/智谋 拆成独立标签页承担细分统计；㉖ **称号镀金区分**：称号里只有一部分可镀金（本例 14/42，取自 Manifest `titleInfo.gildingTrackingRecordHash`，传承称号节点不带该字段故不可镀金）——可镀金标金色描边「可镀金」徽章 + 图标金色细环，已镀金标金色实心「已镀金」徽章 + 金色图标环 + 卡片金色描边/渐变；组标题加「镀金 G/N」，顶部加「可镀金 / 已镀金」汇总；㉗ 本页共 49 项回归断言（test_sweep.py），含"PVE/地牢详情不得出现失败标签"与称号「可镀金」标记。

**2026-09-29 更新**：① **`生涯武器` / `pvp生涯武器` 成为独立指令**——此前 `/pvp生涯武器` 会被 `/pvp` 前缀吃掉、多出来的「生涯武器」被当成玩家名，于是回「没找到玩家 生涯武器」（NoneBot 命令走 **最长前缀匹配**且命令与参数之间不要求空格，`TrieRule.longest_prefix`）；现在把 `生涯武器`、`pvp生涯武器`、`pvp武器` 注册成 `常用武器` 的别名，带不带斜杠都能识别，`/pvp 玩家名` 不受影响。② 生涯武器的统计范围由"本赛季"改为**默认全生涯**，并支持**只查单个赛季**：网页版顶部新增「PVP生涯武器统计范围」下拉（全生涯 + S8～S28），QQ 里在参数末尾写 `s27` / `赛季27` / `全生涯`（例 `/生涯武器 Wj#8984 s27`），不写＝全生涯。③ 表格新增**精准击杀 / 爆头率**两列（爆头率 ≥40% 标金色），并在顶部加 6 个汇总块：总击杀 / 武器击杀(占击杀比) / 精准击杀(爆头率) / 近战 / 手雷 / 大招——后三项取自 PGCR `extended.values.weaponKillsMelee/Grenade/Super`，武器精准击杀取自 `values.uniqueWeaponPrecisionKills`，均按玩家本人累计（`get_pgcr` 的裁剪结果里没有这些字段，故新开 `pvp_match_contribution` 拉原始 extended 并只留需要的部分）。④ **性能**：PGCR 单发延迟约 2 秒（Bungie 服务端慢，实测并发 1/5/10/20 分别 0.7/4.2/11.7/14.0 req/s、全程无 429），并发从 1 提到 16；同时**每场结果按 instance 落盘缓存**（`pvp_weapon_cache.json`，存 exe / 项目同目录，和 user_bindings.json 一起），因此全生涯首查约 2～3 分钟、之后换赛季或再查同一个人基本秒开；逐场统计上限 2000 场（十年老号防跑太久，到上限会在卡片上标注），单场失败只计数不中断。⑤ 顺手修 `except LookupError` 后**缺 `return`** 的老 bug（玩家/生涯/raid/地牢/pvp/pve/历史/锻造/称号 共 7 处，另加后台任务失败分支）：此前"没找到玩家"的卡片发完后会继续用未赋值的 `data`/`rep`，抛 NameError 被 NoneBot 吞掉，日志里每次都有异常堆栈。⑥ `/常用武器` 等待任务超时 300s → 420s（全生涯首次要 2～3 分钟）；`/帮助` 补上生涯武器的别名与范围写法。（改的是 `destiny_data.py` / `webui.py`，属打包内模块 → 需重新打包 exe；`nonebot_plugins/destiny2.py` 是外置模块，同步复制即可。）⑦ **新增 PVE 生涯武器使用统计**：实测 PVE 对局的 PGCR 同样带逐武器击杀明细（`extended.weapons[].values.uniqueWeaponKills` / `uniqueWeaponPrecisionKills`，与 PVP 同结构），且 `mode=7`（AllPvE）本身就含突袭与地牢，所以直接复用 PVP 那条管线：把 `_collect_matches(mode, skip_modes)` 与 `_run_weapon_job(kind, cap, skip_modes)` 参数化，`start_pve_weapons()` 走 mode 7 并跳过探索/巡逻（mode 6：没有实质击杀，且实测偶发没有武器明细）。PVP／PVE 共用同一份 PGCR 缓存（同一场比赛的明细只拉一次），缓存上限 6000 → 20000。⑧ **范围默认值按模式区分**：PVE 场次比 PVP 多一个数量级（实测 Wj 一个赛季 1036 场 ≈ 120 秒、0 场失败），所以 PVE 默认**当前赛季**、下拉里「当前赛季」排第一、全生涯排最后并标注代价；PVP 仍默认全生涯。网页端新增 `PVE生涯武器` 标签页，两个标签页各有独立赛季下拉（`#scope` / `#scope_pve`，按标签显示）。QQ 端新增指令 `/pve生涯武器`（别名 `pve武器`、`pve常用武器`），默认当前赛季，末尾可写 `s27` / `赛季27` / `全生涯`。⑨ 卡片沿用 `render_wpvp`，按 `rep['kind']` 切换 PVP／PVE 标题与空态文案；PVE 页脚注明技能与灼烧/电击这类伤害 Bungie 不逐把归属，武器击杀占比通常 50%~65%（实测各玩法占比 50%~69%：打击 51% / 突袭 61% / 地牢 64% / 剧情 55%）。⑩ 回归断言 49 → 54 项（test_sweep.py）：新增 PVE 生涯武器任务流、首页两个下拉与默认赛季。（改的是 `destiny_data.py` / `webui.py` → 需重新打包 exe；`nonebot_plugins/destiny2.py`、`bot_cards.py` 是外置模块，已同步复制到 `dist\D2Query\`。）⑪ **指令识别收紧**：群里闲聊「pve是顺手写的，主要是glk刚才问了」曾被当成 `/pve` 查询、回了一张「没找到玩家」。原因是 NoneBot 的命令匹配是 **TrieRule 前缀匹配**、且 `command_start` 里带了空串，任何以命令词开头的中文句子都会命中。现在 ① `command_start` 只留 `{"/"}`（`bot.py` / `bot_runtime.py`）——**不带 `/` 一律不响应**；② 所有 `on_command` 加 `force_whitespace=True`——命令词后必须跟空白或直接结束，`/pve是顺手写的` 这种连写也不再命中；③ 面板消息日志改为记录**所有以 `/` 开头的消息**（含拼错的），方便排查「发了却没回」。⑫ **修复「引用消息/@机器人 后发指令无响应」**：`TrieRule.get_value` 在**所有响应器之前**就被调用一次并把结果存进 state（`nonebot/message.py:556`），只看 `message[0]` 且必须是文本段；用户「引用某条消息再发 `/pve`」时第 0 段是 `reply`，前缀直接解析为空 → 所有指令静默不匹配。`@机器人` 之所以正常，是适配器自带 `_check_at_me` 预处理。现在插件注册了一个 `@event_preprocessor`（`nonebot_plugins/destiny2.py` 的 `_as_command`），在 Trie 解析前把开头的 引用/表情 等非文本段摘掉。⑬ **面板新增「后台任务进度」**：生涯武器（PVP/PVE）与热力图这类长任务此前只有 QQ 里一张「统计中」卡片，卡住还是跑着看不出来。现在 `JOBS` 带上了 `who`（谁发起的：群号+昵称 / 私聊 / 网页面板）、`label`、`done/total`，新增 `GET /api/bot/jobs`（`destiny_data.job_snapshot()`），面板每 2 秒刷新一条进度条（谁查的谁 + 百分比）；`JOBS` 超过 80 条会优先清理已完成/失败的，避免长时间运行越攒越多。⑭ **回复丢失可见化**：图片发送失败（协议端掉线/重连中）现在会在面板日志里记一行 `[发送失败]`，否则这种「回复丢了」在面板上完全看不出来。⑮ **生涯任务排队**：生涯武器（PVP/PVE）与热力图都要逐场拉 PGCR，多个一起跑会被 Bungie 限流、整体反而更慢，所以 `destiny_data` 里加了一条**串行队列**（`_JOB_QUEUE` / `_pump_jobs` / `queue_position`）：同一时间只跑一个，后来的任务状态是 `queued` 并带上位次。QQ 端「统计中」卡片会写「前面还有 N 位在统计，已排队」；网页端进度条显示「排队中 · 第 N 位」；面板进度条同理。`/job/{jid}` 返回里加了 `queue` 字段。等待上限从 420s 提到 1200s（排队要等前面的跑完）。
浏览器打开 http://127.0.0.1:8900 ，输入 ID 一次后用顶部标签切换：
- **总览**：时长/光能/角色 + 三模式**生涯**统计（官方 allTime，含胜率）
- **PVP**：生涯统计（官方）+ 近期战绩（胜率/连胜/KD/KDA/平均效率）+ **模式细分**（试炼/铁旗/混战/生存…）+ 最近对局
- **PVE**：生涯统计（官方）+ 近期战绩（通关率，排除巡逻）+ 模式细分（突袭任务/地牢/打击/剧情…）+ 最近对局
- **智谋**：近期战绩（胜率/连胜/KD）+ 最近对局
- **战绩**：全模式对局流（每条带模式标签，PvE 显示通关/未通关，小日向风格）
- **Raid突袭 / 地牢**：跨角色聚合，按副本（合并普通/大师/永恒等难度）一行显示 通关/无暇/大师/单人/双人/三人/单人无暇/双人无暇/三人无暇 徽章；点副本 → 同维度细分 + 按月筛选 + 总击杀
- **PVP生涯武器 / 热力图**：生涯武器走后台任务（真实进度条，**默认全生涯**、顶部下拉可只查单个赛季；表格给 击杀/出场/场均/占比/**精准击杀/爆头率**，顶部汇总 总击杀/武器击杀/精准击杀+爆头率/近战/手雷/大招，首次逐场拉 PGCR 约 2～3 分钟、之后走缓存秒开）；热力图为按赛季分组的全历史月历
- **称号 / 锻造**：称号按 称号/传承称号 分组，每张卡片显示该称号完成进度 x/y + 进度条；**镀金状态单独区分**——可镀金的称号标「可镀金」（金色描边徽章 + 图标金色细环），已镀金的标「已镀金」（金色实心徽章 + 金色图标环 + 卡片金色描边/渐变底），组标题显示「镀金 G/N」，顶部汇总「可镀金 / 已镀金」；锻造按 主武器/特殊武器/重武器/异域催化 分组（与游戏内「模式和催化」一致），卡片显示武器种类与解锁状态；进度基于 Bungie 记录状态位（含 characterRecords 合并）
- **加载反馈**：标签切换带进度条；已看过的视图本地缓存秒开；玩家名大小写错了也能模糊搜索兜底
- **任意对局可点击** → PGCR 详情：团队汇总、MVP、玩家徽章横幅卡片（胜负标签、击杀占比、武器明细）；PVP 对局按胜/负标注并附效率与队伍得分
- **Perk 独立页面**（/perks）：联想搜索、结果卡片带赛季水印角标、居中排版；
  武器详情可交互——点击 perk/枪管/弹匣/模组/大师即选中，属性条实时联动（加成绿条、削减红条），
  改装项默认收起，perk 点击显示说明
- **武器图鉴**（/catalog）：全量 2208 把武器网格，6 个维度（类型/框架/元素/弹药/槽位/品质）动态计数筛选 +
  跨字段搜索（含「喷子/绿弹」这类社区叫法）+ 排序/加载更多；默认合并同名版本（可关）；
  **点卡片在图鉴页内弹出详情层**，Perk 选择 / 大师杰作 / 模组 / 催化 全套交互照旧，Esc 关闭
- **加载反馈**：标签切换带进度条；已看过的视图本地缓存秒开；玩家名大小写错了也能解析兜底；
  首页输入完整 名字#编号 会弹出带徽章的联想确认（Bungie 已关闭免鉴权的模糊搜索，故只能精确联想）

索引数据来自 Bungie Manifest（zh-chs），更新索引跑：
```
.venv\Scripts\python build_manifest.py && .venv\Scripts\python build_weapon_details.py && .venv\Scripts\python enrich_weapons_ci.py
```
（只重建活动模式索引：`.venv\Scripts\python build_modes.py` → manifest_index/modes.json）
（只重建图鉴/筛选索引：`.venv\Scripts\python build_weapon_filter_index.py && .venv\Scripts\python build_weapon_catalog.py`
→ manifest_index/weapon_filter_index.json（QQ `/武器筛选` 用）+ weapon_catalog.json（网页图鉴 `/catalog` 用）；
图鉴索引是在筛选索引上补品质，所以**必须先跑前者**）

**与小日向的差距**：触发词命名、「QQ 用户绑定账号 + 快捷指令」、**图片卡片输出**、
以及 `raid`/`地牢`/`pvp`/`pve`/`智谋`/`历史`/`热力图`/`称号`/`锻造`/`常用武器` 等战绩类指令**均已对齐**；
尚未实现的只剩勋章/击杀纪念板块、仓库搜索、周常专项、日报周报；
对局历史每模式最多取最近 100 场/角色（生涯累计用官方聚合接口），故模式细分里的场均/胜率是"近期"口径而非全生涯。

## 已实现指令（群聊/私聊均可）

> **所有回复都是图片卡片**（小日向式：HTML 排版 + 无头浏览器截图，见 `bot_cards.py` / `card_render.py`），
> 不再回纯文本；只有渲染器完全起不来时才退回文字。
> 触发词对齐**小日向**（`/中文` 形式），**指令必须带 `/` 前缀**（`command_start = {"/"}`），
> 且命令词后要紧跟空白或直接结束——群里闲聊「pve是顺手写的…」不会再被当成查询。
> 前置的 `@机器人` 与「引用某条消息」都会自动忽略，照常识别后面的指令。
> 旧 `d2` 系列保留为别名（同样要带 `/`）。
>
> 战绩类卡片（raid / 称号 / 热力图 / 常用武器 …）**直接复用查询站 `webui.py` 的 `render_*` 排版**
> （`bot_cards.py` 里做委托），改网页端的排版，机器人出图会跟着变，不用维护两套。

- `/绑定 玩家名#编号` — 绑定自己的命运2账号；**绑定后玩家类指令可省去名字**
- `/解绑` — 解除绑定 ｜ `/我的`（别名 `/账号`）— 查看当前绑定
- `/玩家 [玩家名#编号]`（别名 `/d2`）— 图片卡：徽章横幅三角色（职业/光能/时长/上线）+ 最高光能 + 三模式速览
- `/生涯 [玩家名#编号]`（别名 `/周报`、`/d2周报`）— 图片卡：PVP/PVE/智谋生涯表 + 总计（击杀/死亡/KD/场次）
- `/raid [玩家名#编号]`（别名 `/突袭`、`/d2raid`）— 图片卡：分「标准难度」「大师难度」两栏，
  每行 通关/无暇/单人/双人/三人/单人无暇/双人无暇/三人无暇 八枚徽章（**0 压暗**）+ 难度标注 + 最快/最近
- `/地牢 [玩家名#编号]`（别名 `/dungeon`、`/d2地牢`）— 同上口径，地牢（mode 82）
- `/pvp [玩家名#编号]`（别名 `/熔炉`、`/d2pvp`）— 图片卡：生涯统计（击杀/死亡/KD/胜率/精准击杀/协助）+ 近期战绩 + 胜率/连胜连败/KDA/平均效率 + 模式细分表
- `/pve [玩家名#编号]`（别名 `/d2pve`）— PVE 口径同上，用通关率/通关场次代替胜率（排除探索巡逻）
- `/智谋 [玩家名#编号]`（别名 `/gambit`、`/d2智谋`）— 智谋战绩（官方聚合接口已下线，走对局历史聚合 + 胜负）
- `/历史 [玩家名#编号]`（别名 `/战绩`、`/最近对局`、`/d2历史`）— 图片卡：全模式最近对局流（模式标签 + 通关/胜负 + K/D/KDA）
- `/热力图 [玩家名#编号]`（别名 `/活跃`、`/d2热力图`）— 图片卡：按赛季分组的全历史月历（同赛季同色，越亮玩得越久）；后台任务全量翻页，先回一张"统计中"
- `/称号 [玩家名#编号]`（别名 `/d2称号`）— 图片卡：称号/传承称号分组网格，每条带 x/y 进度条，可镀金/已镀金标记 + 镀金汇总
- `/锻造 [玩家名#编号]`（别名 `/图案`、`/d2锻造`）— 图片卡：锻造图案**按掉落来源分组**（小日向式 24 组，
  如「梦魇根源 5/6」「救赎花园 2/8」），**组按「出的顺序」排（旧→新），已全部集齐的组整组往后排**——
  顶部永远留没集齐的组；每张卡显示 武器类型 + **x/y 进度条**
- `/常用武器 [玩家名#编号]`（别名 `/武器统计`、`/mvp`）— 图片卡：本赛季 PVP 武器排名（前三奖牌色 + 击杀条 + 出场/场均/占比）；后台任务，先回一张"统计中"
- `/武器查询 武器名`（别名 `/d2武器`）— 图片卡：图标+赛季徽标、属性条、Perk 分列（框架/特性/枪管/弹匣/枪托/固定配件/催化）+ 精确数值
- `/perk查询 perk名`（别名 `/特性查询`、`/d2perk`、`/d2特性`）— 图片卡：图标 + 官方说明 + 属性增减标签 + 中文精确数值
- `/武器筛选 关键词…`（别名 `/d2武器筛选`、`/d2筛选`、`/筛选武器`）— 图片卡：小日向式三列网格，从
  全部 2208 把武器里按条件筛列表。关键词空格分隔，多词之间是**与**（全部满足）；
  支持 类型 `手炮/微冲/喷子/机枪/榴弹/弓/偃月/线性…`（含 `AR SG SMG LFR` 等缩写）、
  弹药 `主手·白弹 / 副手·绿弹 / 重弹·紫弹`、槽位 `动能 / 能量 / 威能`、元素 `电/火/冰/虚空/缚丝`、
  射速 `140/900`、框架 `速射/波形/适配/精密/轻质/高冲…`、特性名 `爆破专家/雪上加霜/事不过四`、
  `锻造`（可锻造）、`异域`（金枪）；
  词库按社区叫法归一（白弹=主武器、喷子=霰弹枪、自适应=适配框架、精准=精密框架、
  威力=威能、高冲=高冲击力…），再统一走子串匹配；
  **匹配分两级**：词若能在「类型/弹药/槽位/元素/框架」里命中就用这几个字段，
  否则才去武器名/来源/特性名里找——不然「轻质」会从 222 条轻质框架涨到 627 条（轻质弹匣）；
  词与词矛盾时（如「副手 白弹」）自动忽略其中一个并在卡片上注明，同名武器按名字合并展示
- **群里 `@机器人 武器名` / `@机器人 perk名`** — 不用打指令，直接出对应的武器 / perk 卡片（自动先当武器、再当 perk 匹配；**只认真 @，引用机器人消息不算**）
- `/帮助`（别名 `/help`、`/菜单`）— 图片卡：指令一览

> 群内回复会自动 `@` 发起人（小日向同款），私聊不加。

绑定关系存 `user_bindings.json`（`{QQ号: "显示名#编号"}`），按 QQ 号区分，群聊/私聊共用；
玩家类指令（`/玩家`、`/生涯`、`/raid`、`/pvp` …）带名字时优先用参数，不带则回退到绑定账号。


## 打包为本地 exe（可选）

```
build_exe.bat
```
双击运行（首次需联网装 pyinstaller，脚本已配置清华镜像 fallback）。
**脚本走的是 `D2Query.spec`**（图片卡片要的 playwright、模式索引 `modes.json`、锻造来源索引
`pattern_sources.json` 都写在 spec 里；早期那版手写命令行参数漏了这些，构建出来没有图片卡片）。产物在
`dist_build\D2Query\`，把 **`D2Query.exe` 和 `_internal` 一起**覆盖到 `dist\D2Query\`（`_internal` 用 `cp -r`，
robocopy `/MIR` 在中文路径下会卡住），再同步三个外置模块（见下），把 `.env` 放在同目录后双击 `D2Query.exe`
—— 弹出**原生程序窗口**（pywebview，无 cmd 窗口），端口被占用时自动顺延。
网页版和 exe 同时开时，后启动的实例 QQ bot 会不可用（8901 被占），建议只开一个。

> **图片卡片依赖**：卡片渲染用 Playwright 驱动无头浏览器，`D2Query.spec` 已用
> `collect_all('playwright')` 把 `python 包 + node driver` 打进 `_internal`（体积 +100MB）。
> 浏览器本体用**系统 Edge**（`channel="msedge"`，无需 `playwright install`），取不到时依次回退
> 自带 chromium、Chrome。
> **改动需同步的三处**：`nonebot_plugins/destiny2.py`、`card_render.py`、`bot_cards.py` 是
> exe 旁加载的（改完不用重打包，但要各复制一份到 `dist\D2Query\` 并重启程序）；
> 改动 `webui.py` / `destiny_data.py` / `D2Query.spec` 才需要重新打包。
> 打包/同步与已运行实例的端口冲突见记忆「D2 查询站环境要点」：源码版与 exe 不能同跑，
> 复制 `_internal` 时用 `cp -r`（robocopy 在中文路径下会卡住）。

## QQ bot 接入（程序内管理）

1. 程序启动时内置 NoneBot（OneBot v11 反向 WS 服务端，`ws://127.0.0.1:8901/onebot/v11/ws`）；
2. **面板内置 NapCat 一键登录**：打开 Bot面板（/panel）→ 点「启动并扫码登录」→
   二维码直接显示在面板里，手机 QQ 扫码即可；登录成功后自动写入反向 WS 配置
   （`napcat_shell/config/onebot11_<QQ>.json`）并重启 NapCat 连入 Bot，全程无需打开 NapCat WebUI；
3. 面板：查看连接状态与 QQ 账号、勾选生效群聊（不勾 = 所有群响应）、
   保存后写入 bot_config.json 立即生效；右侧独立侧栏显示**后台任务日志**
   （谁发起的 / 查的谁 / 跑到第几场 / 发起时间，可滚动 + 分页）与**账号绑定**（QQ → 玩家名#编号）；
4. 群指令（小日向式）：`/绑定 玩家#编号` → 之后 `/生涯`、`/玩家` 直接出结果；
   武器类 `/武器查询 武器名`、`/perk查询 perk名`；也可以直接 **`@机器人 武器名/perk名`** 出卡片。
   旧 `d2*` 写法仍可用。

> **QQ 版本要求**：内置 NapCat（4.3.x）需要**新版 QQNT**。实测 QQ `9.7.18`（2023 旧版）注入静默失败
> （napcat.log 为空、WebUI 不启动）；QQ `9.9.36` 正常。首次点「启动并扫码登录」扫码一次，
> 之后点启动会走快速登录（`-q` 由 launcher 自动补，uin 裸传）。外部 NapCat / LLOneBot 手动接入仍然可用。

### 更新日志

- 2026-09-30（二十四）：**修打包漏文件导致 DIM 页面整页报错** ——
  exe 里开 DIM 板块报 `读取失败：[Errno 2] No such file or directory: 'manifest_index\stats.json'`。
  根因不在 DIM 代码，而在 `D2Query.spec` 的 `datas` 是**手写清单**：DIM 数据层新加的
  `manifest_index/stats.json`（属性名表，`dim_data._load("stats.json")`）当时没补进去，
  所以源码跑得好、打包出来就少这一个文件。`_idx_file()` 的三级查找
  （源码目录 → `_MEIPASS` → 相对 cwd）全都落空，最后抛的就是那个相对路径。
  - 对比 `_idx_file()` / `_load()` 的全部调用点，运行时真正需要的 21 个索引里
    只缺 `stats.json` 一个；`community_clarity.json`、`community_dim.json` 只在构建脚本
    （`build_weapon_details.py` / `enrich_weapons_ci.py`）里读，不进包不影响运行。
  - spec 改成**用 glob 自动收集** `manifest_index/*.json`，只排除构建中间产物
    `raw_items.json`(220MB) / `raw_plugsets.json`，以后再加索引文件不用记得改 spec。
  - 本轮没有重打包：直接把 `stats.json` 补进已部署的
    `dist\D2Query\_internal\manifest_index\`、`dist\D2Query\manifest_index\` 和
    `dist_build\D2Query\_internal\manifest_index\`，重启 exe 即生效；
    下次照常跑 `build_exe.bat` + `deploy_exe.ps1` 也不会再丢。
- 2026-09-30（二十三）：**热力图结果落盘 + 没有新数据直接出缓存** ——
  去重（上一条）只挡住「同时/紧接着」的重复，隔几分钟再查还是会从最新页一路翻到 2019-06。
  这次让热力图也像生涯武器那样有结果缓存，落在 `heatmap_cache.json`（`_writable_path()`，
  和别的缓存一起放 exe 同目录，`deploy_exe.ps1` 不碰它所以部署不丢）。
  - **判断有没有新数据**：`activity_history` 的 `period` 和 profile 里每个角色的 `dateLastPlayed`
    都是 Bungie 的 UTC 时间戳、都截到分钟，所以直接按字符串比大小就是对的。
    比的是**完整时间戳而不是日期** —— 当天完全可能又打了几场，只比日期会把它们漏掉。
    只要有角色拿不到 `dateLastPlayed` 就不吃缓存（保守重跑）。
  - **闸门在 `start_heatmap` 里、入队之前**：命中就直接把任务标成 `done` 返回，**连队列都不排**。
    否则一个 10 分钟的生涯武器任务在跑时，明明有缓存的热力图也得干等 —— 这跟缓存的目的正好相反。
  - **增量补拉**：缓存过期时（有新数据）不再全量重来，从最新页往下翻，遇到
    `period <= 已统计到的那一场` 就收工，只把新增的场次并进 `days`。所以「打了两把再查」
    只翻一两页。`cutoff`（缓存边界）和 `newest_full`（本次跑完的最新）是两个变量：
    一开始共用一个，结果第二轮拿刚统计的第一场当边界、只计一场就停 —— 测试直接抓出来了。
  - 卡片顶部的汇总条会标明数据来源：「直接复用缓存（已统计到 …，没有新数据）」/
    「上次统计到 …，本次补拉新增 N 场」/「全历史重新统计，数据截至 …」，
    免得看到秒出图以为没跑。bot 的提示语也分了三种（缓存直出 / 复用正在跑的任务 / 正常排队），
    缓存直出那句**不**承诺「过会儿再发能强制重跑」—— 他确实没打新的，再发多少次都是这份。
  - `_HEAT_CACHE_MAX = 60`：最多留 60 个玩家的结果（一天一条，单人是 100KB 量级），
    超了丢最久没更新的四分之一。
  - 已知取舍：角色**被删掉**时缓存里仍留着他的历史（不主动修），而且角色集合一变就退回全量
    —— 这两点合起来意味着「删角色」之后第一次会重算并丢掉那个角色的日子，之后再也不会回来。
    单机自用够；要改成保留需要另存一份 per-character 的 days。
  - 回归测试 `_rtest/heat_cache_test.py`（8 组共 30 条断言，把
    `resolve_member`/`get_profile`/`activity_history` 全换成假实现、落盘指到临时目录），
    关键数字：全量 1200 场要翻 8 页 → 无新数据时翻 **0 页**、新增 5 场时翻 **2 页**（不是 8 页），
    边界日的旧场次没有被重复计入，老日子一天没丢。与 `_rtest/job_dedup_test.py` 一起全绿。
- 2026-09-30（二十二）：**后台长任务去重：同一个查询不再重复全量跑** ——
  起因是群里和私聊在 90 秒内各发了一次 `/热力图 Wj#8984`，面板上就出现两条任务，第二条把
  11 页活动历史原封不动又走了一遍。根因是 `jid = f"{mid}_heat_{len(JOBS)}"` 每次都生成新 id，
  而且原来没有任何「同一个查询」的概念，所以照单全收排进 `_JOB_QUEUE`（队列是串行的，第二个
  还得干等第一个跑完）。现在 `destiny_data.py` 加了一层去重映射 `_JOB_DEDUP`（去重键 → jid）：
  - 键 = `mtype:mid:heat`（热力图）或 `mtype:mid:wp:kind:since:until`（生涯武器）。
    生涯武器用的是**解析后的时间窗**而不是 scope 原文，因为 `27` / `s27` / `赛季27` 是同一个窗口，
    不该因为写法不同就跑两遍；`all` 与某个赛季、PVP 与 PVE 各自独立，不会互相复用。
  - 命中条件：任务 `queued` / `running` **无条件复用**；`done` 的给 `_JOB_REUSE_SEC = 120` 秒窗口
    （刚出完图再来一次不重跑），过窗口才允许重跑，这样「再发一次」仍然是可用的强制刷新手段。
    `error` 不复用 —— 不能把失败的任务当成缓存发出去。
  - 命中的请求**连 `get_profile` 都不用拉**（`chars` 只有真建新任务时才需要），只花一次名字解析，
    直接拿已有 jid 回去等结果，调用方（bot 的 `_jobs_card` / 网页的 `/job/{jid}` 轮询）完全无感。
  - `_run_queued` 收尾时补 `ended` 时间戳；`_reuse_job` 里 `ended` 缺失时兜底取 `ts` ——
    盖住「工厂函数已把 status 置成 done、收尾还没跑到」那一瞬。长任务按 `ts` 算会得出更久的耗时
    → 不复用 → 重跑，方向偏保守，宁可多跑一次也不发旧数据（这条是测试第 8 例逼出来的）。
  - `_prune_jobs` 顺带清理指向已删任务的映射，否则 `_JOB_DEDUP` 会随「一共查过多少人」一直涨。
  提示语跟着改：`_queue_line` 改成返回 `list[str]`，命中去重时不再说「要翻几百页对局历史」，
  而是「这份数据刚跑过，直接给你上次的结果（2 分钟后再发可以强制重跑）」或「已经在统计了，
  跑完直接出图」；面板任务行也会标「已复用于 群…/私聊…」，免得看着像指令没反应。
  回归测试 `_rtest/job_dedup_test.py`（8 组共 20 条断言，不碰 Bungie，把
  `resolve_member`/`get_profile` 换成假实现直接驱动两个入口），全绿。
- 2026-09-30（二十一）：**热力图加日均** —— `render_heat` 里两处新增。
  ① 顶部加一条汇总条（`.hstat`）：全历史场次、总时长、活跃天数与记录跨度，
  以及两个日均 —— `日均`（全历史场次 ÷ 跨度自然日）和 `活跃日日均`（÷ 有活动的天数）。
  两个都给是因为前者会被「中间 AFK 的几个月」拉低，后者才反映真上号时的强度，只给一个容易误导。
  ② 每张月卡副标题下面加一行蓝字 `日均 X 场 · Y分`。
  这里的天数分母**不是**当月自然日，而是「该月落在记录区间内的天数」——
  拿首月（比如 11 月 15 号才开始有记录）和当月（还没过完）来说，用整月天数会把日均压低，
  所以按 `max(月初, 首次记录) → min(月末, 最近记录)` 夹一下再取天数（实测 2018-11 判 16 天、
  2026-09 判 25 天）。`dmin`/`dmax` 本来就在上面算好了，没加额外的遍历。
- 2026-09-30（二十）：**热力图改四列满宽 + 对局胜负方格缩小** ——
  两个都是排版问题，改的只有 `webui.py` 里两段 CSS（`render_heat` 的内联样式和 `CARD_CSS`）。
  ① **热力图**：`.mrow` 原先是 `flex-wrap` + 固定 `158px` 的月卡，但 `.mcal` 没写
  `box-sizing`，浏览器默认 `content-box`，实际占位是 `158+左右内边距20+边框2=180px`，
  于是 676px 的内容宽只能塞下 3 张，右边白出 180 多像素。现在 `.mrow` 改成
  `grid-template-columns:repeat(4,1fr)`、`.mcal` 加 `box-sizing:border-box`，
  正好四张铺满整行、不再有右侧留白；月卡标题/副标题居中，同一行的月卡高度自动对齐。
  ② **胜负方格**：`.gridline` 原来是 `repeat(20,1fr)`，每格按 1fr 撑到约 31px，
  一场一格显得又大又厚（PVP 跨角色 300 场能堆 15 行、近 500px 高）。现在改成
  `repeat(auto-fill,19px)`、`.cell` 定死 19×19，一行约 28 格，同样的 300 场只占 11 行；
  顺带给方格加了 hover 放大，PvE 的「未通关」灰从 `#666` 调深成 `#555f70` 免得比胜负色更抢眼。
  `/热力图` 卡片（`bot_cards.heat_card`）和 PVP/智谋/副本卡片（`bot_cards.mode_card` /
  `raid_card`）都转发到这两处，所以一处改完全站生效。
- 2026-09-30（十九）：**玩家类指令支持「`@某人` 查 TA」** ——
  以前 `/生涯 @小明` 是查不了的，两处各挡了一道：① `_as_command` 里那条「消息 @ 了别人就整条
  丢掉」的防抢答规则（为的是用户对着群里小日向发指令时我们不跟着回）会把 `/生涯 @某人` 一起吞掉，
  连日志都只留一行 `skip: @了别人`；② 即使放行，取名字用的是 `args.extract_plain_text()`，
  而 at 段不是文本段会被丢成空串，于是退回「发起人自己的绑定」——查出来是**自己**的数据。
  现在**按 @ 的位置区分**：@ 在指令**之前**（`@小日向 /raid`）仍然丢弃，那是对着别的 bot 说话；
  @ 在指令**之后**（`/raid @某人`）才当成查询目标。新增 `_at_target()` 从 `CommandArg` 里取
  at 段的 QQ，去 `user_bindings.json` 查绑定名——命中就查 TA，没绑定时明确回一句
  「TA 还没绑定，让他发 `/绑定 玩家名#1234`」，**不会**悄悄退回发起人自己的账号。
  显式写名字（`/生涯 小明#1234`）优先级最高，其次 @ 目标，最后才轮到自己的绑定；范围参数不受影响
  （`/pve生涯武器 @某人 s27` 照旧）。覆盖 `/玩家 /生涯 /raid /地牢 /pvp /pve /智谋 /历史 /热力图
  /锻造 /称号 /生涯武器 /pve生涯武器`，`/帮助` 加了一行说明。
  回归测试见 `_rtest/at_target_test.py`（7 条段序判定 + 6 条 CommandArg 解析）与
  `_rtest/at_bind_test.py`（4 条绑定解析，跑的是部署用的真实 `user_bindings.json`），全绿。
  （只改 `nonebot_plugins/destiny2.py` 这一个外置模块——`deploy_exe.ps1` 就会同步它，无需重新打包。）
- 2026-09-30（十八）：**属性条口径修正 + 页面合并与排版统一** ——
  ① **射速/弹匣这类绝对数量不再画成属性条**：属性条原本一律按「值/100」当宽度，而射速 257、
  充能时间 800ms 根本不在 0–100 量程上，一律被截成**满格条**（看着像顶配），弹匣 18 也被当成
  「18 分的属性」。现在 `每分钟发射数 / 射击速度 / 弹匣 / 弹药容量 / 弹头速度 / 充能时间 / 蓄力时间`
  单独一行写数字（对齐 DIM：这些项只有数值、没有条），其余 0–100 属性照旧画条；
  卡片（`bot_cards.py`）与网页面板（`webui.py`）两处一起改，面板里点弹匣类 perk 时这行数字仍实时
  联动（显示 `48 +30` 这种增减）。顺手修掉 `STAT_ORDER` 里三个和数据对不上的名字
  （精准度→**精度**、挥砍速度→**挥舞速度**、蓄能时间→**蓄力时间**），此前它们匹配不上只能排到末尾。
  ② **「武器查询」页撤掉，功能并入「武器图鉴」**：`/weapons` 路由、`WEAPONS_PAGE` 模板、
  `render_weapon_grid()` 与导航项一并删除（图鉴页右上那个「名称搜索」按钮也去掉了，图鉴自带搜索框）；
  旧的按名搜入口 `/weapon?q=` 改为 **307 跳 `/catalog`**，老书签不会撞 404。
  ③ **首页右上角那个「Bot面板」角落按钮删掉**：Bot 面板早已并进顶部导航（六个同级按钮），
  角落按钮纯属重复；`.corner` 的 CSS 一起清掉。
  ④ **排版统一**：光尘商店 / 本周轮换两页此前直接用 `bot_cards` 的卡片 HTML，而那张卡片是按
  760px 定宽出图的（`body{width:760px}`），当网页打开就贴在左上角、导航挤成两行——现在补一层网页
  外壳（`card_page()`：顶部导航 + 居中 760px 内容列，导航移到卡片**外**），武器详情页
  `DETAIL_PAGE` 的 body 加 `margin:0 auto` 一起居中。六个页面现在都是**同一排 6 项导航、单行居中、
  内容列水平居中**（已用真实浏览器逐页量过：导航行数=1、中心偏差 0）。
  ⑤ `test_sweep.py` 同步：去掉两条 `/weapons` 断言，换成武器图鉴（含「页内不得再有 /weapons 链接」）、
  老入口 404/跳转、首页不得再出现 `class="corner"`、光尘商店与本周轮换必须带 `d2nav` + 居中外壳。
  （改的是 `webui.py` / `bot_cards.py` / `test_sweep.py` → 六处改动一起重新打包部署。）
- 2026-09-30（十七）：**武器筛选的词库换成社区叫法，并修掉「子串误伤」** ——
  ① 槽位值从 Manifest 的「威力」改成玩家真在说的**「威能」**（输入「威力」仍自动归一）。
  ② `SYNONYM` 按三类说法扩到 ~90 条：官方译名、社区口语（白弹/绿弹/紫弹、喷子、微冲、筒子、
  机炮、战弓、枪刃、高冲、自适应、精准…）、英文缩写（AR SG SMG LFR RL LMG HC PR FR GL SR TR）。
  ③ **匹配分两级**：词先看「类型/弹药/槽位/元素/框架」这些结构性字段，能命中就只用它们；
  命不中才去武器名/来源/特性名里找。之前一律全字段子串，「轻质」被「轻质弹匣」撑到 627 条
  （实际轻质框架只有 222 条），「速射」391 条里混了一堆名字/特性带「速射」的；
  现在分别收敛到 128 / 160 条，而纯特性词（爆破专家、雪上加霜）照常走内容字段。
  ④ `/武器筛选` 的帮助文案改成社区说法（弹药·槽位·缩写）。
- 2026-09-30（十六）：**异域催化说明改用 Starside 中文，不再贴 Clarity 英文原文** ——
  ① `scrape_starside_exotic.py` 原先只认 `<span class="exotic">↑催化剂</span>` 这一种写法，
  所以 141 把金枪只抓到 28 把，其余全部退到 Clarity 的英文段（伊邪那岐的重担就是这种）。
  实际上**升金/催化文案在页面里都是「含 `span.exotic` 的段落」**，改成按这个抓，拿到 128 把。
  ② `bot_cards.py` 渲染「异域催化」时先查 `_cata_zh(武器名)`（懒加载新表），
  有中文就用中文（`↑` 与数值自动高亮成金色，`+155%[218%]` 这种精确写法直接可读），
  **有中文就不再输出 Clarity 英文段**；确实没有中文的武器才退回英文。
  ③ `manifest_index/exotic_catalysts_zh.json` 补进 spec datas（此前只在构建期被读，渲染期读不到）。
- 2026-09-30（十五）：**新增 `/武器筛选`（小日向式三列网格武器列表）** ——
  ① `build_weapon_filter_index.py` 从现成缓存离线生成 `manifest_index/weapon_filter_index.json`
  （2208 把，每把只留筛选要的字段：名称/类型/弹药/槽位/元素/框架/可选特性名/射速/可锻造/异域/图标）。
  ② `weapon_filter.py` 是筛选引擎：词的解析 = **同义词归一 → 子串匹配**（`喷子→霰弹枪`、`白弹→主武器`、
  `自适应→适配`、`精准→精密`…），`锻造`/`异域` 是布尔标记，纯数字当射速。匹配覆盖
  类型/弹药/槽位/元素/框架/名称/来源/**特性名**七个维度，多词之间 **与**；全空时先试「去掉一个词」
  （`副手 白弹` 这种矛盾输入会忽略其一并在卡片注明），再无解才放宽为「或」。
  ③ `bot_cards.weapon_filter_card()` 出三列网格（图标带水印 + 名称 + 类型·弹药 + 框架），
  同名武器的多个 perk 池版本按名字合并，默认最多 120 格。
  ④ **顺带修了一个老数据 bug**：`cat`（动能/能量/威力）原先按 `itemCategoryHashes` 取 `1/2/3`，
  而这三个数并不是三个槽位（结果「威力」1005 条 > 重武器总数 441）。槽位得看
  `equippingBlock.equipmentSlotTypeHash`（1498876634/2465295065/953998645）。筛选索引里已用正确值；
  `weapons_full.json` 的 `cat` 字段仍是旧值（只影响列表卡上的文字，未改动）。
- 2026-09-30（十四）：**新增 `/轮换`（本周突袭 & 地牢）+ 网页 `/rotation`；帮助去掉「小日向式」字样** ——
  ① **官方没有「本周轮换是哪两个」的字段**。`Destiny2.GetPublicMilestones`（**只要 API Key，不需要 OAuth**）
  会给每个突袭一个周常里程碑，9 个突袭带同样的周区间一起返回，实测**只有 `activities[].challengeObjectiveHashes`
  非空的那个才是当周轮换**（本周 = 克洛塔的末日 / 深岩墓室）；「永恒沙漠」也带挑战但它 `activityModeTypes` 为空
  （不是突袭），跟配对表的突袭列取交集即滤掉。**地牢完全不在里程碑里**（带 OAuth 也没有）。
  ② 地牢靠 Starside 的固定配对表（`https://starside.work/rotation/index.html`，静态 10 行）：
  新增 `scrape_starside_rotation.py` 抓成 `manifest_index/rotation_pairs.json`（已进 spec datas；
  Bungie 加新副本时要重跑）。**周期规律**：表每周整体后移一格，知道突袭①的行号 k 就能推：
  突袭② = 突袭① 在「有突袭的 9 行」里往后数 4 个、地牢① = 第 k-1 行的地牢、地牢② = 第 k+3 行的地牢
  （用 09-09 / 09-16 / 09-23 / 09-30 四周公开排期逐一验过）。
  ③ `destiny_data.rotation_week()`：里程碑 + 配对表 → 突袭①②/地牢①② + 周区间（北京时间周三凌晨 1 点换），
  周缓存 `rotation_cache.json`（带 `ver`）；配对表对不上时退化为「只列官方查到的周常突袭」而不是报错。
  ④ `bot_cards.rotation_card()` 用活动索引里的 `pgcr` 横图当配图；网页版 `/rotation` 复用同一份排版，
  顶部导航加「本周轮换」。指令注册在 `nonebot_plugins/destiny2.py`，别名 `/本周轮换` `/d2轮换` `/突袭轮换` `/raid轮换`。
  ⑤ 帮助卡片里 `（不用打指令，小日向式）` 按用户要求删掉，并补上 `/轮换`。
- 2026-09-30（十三）：**光尘商店改查轮换 vendor（三职业齐）+ 每日缓存；武器 Perk 列改按 socket 顺序** ——
  ① **光尘商店只出一把枪皮的根因**：游戏里「主要光尘优惠 / 其他光尘优惠 / 银币优惠」三行是**三组独立 vendor**
  （`EVERVERSE_BRIGHT_DUST_ROTATOR_EXOTIC_*` / `..._LEGENDARY_*` / `EVERVERSE_FEATURED_SLOTS`），
  旧实现只查总店 `3361454721` —— 那里是 223 件 700 银币的常驻旧货 + 当天恰好 1 件光尘商品。改成一次
  `GetVendors`（角色级，`components=400,401,402`，约 780KB）拿全部 vendor，再按 vendor 分组出商品。
  ② **护甲装饰是按角色职业发的**（同一天泰坦/猎人/术士各一件不同皮肤），所以三个角色各查一次、按物品
  hash 合并，否则永远只有 1 个职业的皮肤。
  ③ **索引补大图**：`build_eververse_index.py` 多存一项 `screenshot`（游戏里点开物品那张竖版图，注意是
  物品定义的**顶层**字段，不在 `displayProperties` 里），顺带把「合成纤维模板」这类货币/空类型物品也收进
  索引；武器皮肤 / 三职业皮肤 / 飞船 / 载具用大图当背景（`EV_BIG_TYPES` 的顺序即卡片里的排列），其余
  （机灵、表情、投影、着色器、传送特效）只出方形缩略图；银币那栏不出。
  ④ **每日缓存**：商店北京时间**凌晨 1 点**刷新（`sale.overrideNextRefreshDate` = 次日 `17:00Z` 可印证），
  缓存键 = 「现在 - 1 小时」的日期，当天复用内存 + 落盘 `eververse_cache.json`（带 `ver` 字段，以后改结构
  把它 +1 即可自动作废旧缓存），跨过 1 点自动重拉一轮。实测 09-29 那一轮与 09-30 那一轮能自动切换。
  ⑤ **武器 Perk 池列顺序**：旧写法把插件按类别塞进各个桶，输出时硬编码「枪管 → 弹匣 → 特性 → 起源 → 枪托」，
  于是剑类（护手在特性之前）、异域（固定配件在特性之前）全乱序，剑还会多出一列叫「枪托」。现在
  `build_weapon_details.py` **保留 `socketEntries` 原始顺序**输出 `plugs.cols`（`bot_cards` 与 `webui` 都照搬），
  并补齐原先没人处理的类别：`blades` 剑刃、`guards` 护手、`scopes` 瞄具、`batteries` 电池、`bowstrings` 弓弦、
  `arrows` 箭矢、`hafts` 弓柄、`grips` 握把、`rails` 导轨、`bolts` 弩箭 —— 之前这些全掉进 `mods` 不显示
  （344 把枪缺「瞄具」列、剑缺「剑刃」列）。列内上限也从 8 提到 12，避免特性被截断。
  ⑥ **武器卡片去掉「精确数值」区块**（图片里没法悬停，把社区数值直接铺出来既长又乱）；**金枪改为显示催化
  数值 + 具体效果**：催化物品自己的描述只是通用的「升级为大师杰作」说明，真实效果挂在它
  `perks[].perkHash` 指向的 sandbox perk 上，构建时用 `perks.json` 解析出来（例：枯萎囤积 → 无声报警
  「操控性提高。武器收回枪套一小段时间后会自动填装弹药。」+ 操控性 +40）。同时**修掉一批伪催化**：
  `masterworks.*` 下非 stat 的插件（先锋/熔炉大师杰作、重铸武器…）原先也被算成催化，带催化的武器从
  408 把 / 981 条收敛到 130 把 / 152 条；同名催化改用整名匹配，避免「旅行者」撞上「旅行者的选择催化」。
  ⑦ 重建索引：`manifest_index/eververse_items.json`（9164 条，4859 条带大图）、`weapons_full.json`（2208 把）。
- 2026-09-29（十二）：**武器卡片排版对齐游戏内 + 面板并进程序 + Bungie 回跳改 https 口** ——
  ① **武器卡片（`bot_cards.weapon_card`）四处按游戏内视图修正**：**框架**不再占 Perk 池的一列，改到
  武器名旁的副标题（`速射框架 · 能量 · 脉冲步枪 · 主武器`）；属性条从"只取前 8 条"改为**全部**并按
  游戏内顺序排（`每分钟发射数 → 伤害 → 射程 → 稳定性 → 操控性 → 填装速度 → 弹匣 → 辅助瞄准 → 变焦 →
  后坐方向 → 空中效率 → 弹药生成`），**射速（每分钟发射数）不再被截掉**；两列网格排布；Perk 池列顺序改为
  **枪管 / 发射（1 号位）→ 弹匣 / 电池（2 号位）→ 特性 N → 起源特性 → 枪托 / 固定 / 催化**。
  ② **网页版武器详情页同口径调整**（`webui.py` DETAIL_PAGE）：框架进副标题、属性同序排序、Perk 分列同序，
  并**去掉我之前加在 Perk 卡上的属性增减标签**（悬停浮层里的数值仍保留）。
  ③ **Bot 面板并进程序**：顶部导航新增 `Bot面板`（`/panel` 现在挂着和查询站同一排导航），
  顺手解决"面板进去出不来"——以前面板是独立页，pywebview 没有后退键，现在点导航一步回查询站。
  ④ **Bungie 授权回跳改到带证书的 https 口**：Bungie 只认 https 重定向，而 `https://127.0.0.1:8900`
  没证书、浏览器直接 `ERR_SSL_PROTOCOL_ERROR`（这就是那个"此站点的连接不安全"）。现在程序内部另起一个
  **自签证书的 https 口 `127.0.0.1:8902`**（证书 `certs/localhost.pem`，CN/SAN 都是 127.0.0.1，已打进
  exe），默认 `BUNGIE_REDIRECT_URI=https://127.0.0.1:8902/bungie/callback`；点完"同意"浏览器落到我们的
  「授权成功」页即自动完成，**不用再复制粘贴**（首次会提示证书不受信任，点"高级 → 继续前往"即可，
  之后浏览器会记住）。手动粘贴的通道仍然保留。
  ⑤ **光尘商店解析 bug 修复**：`GetVendor` 的 `categories.data` 其实是 `{"categories":[...]}` 的嵌套结构
  （之前当 dict 遍历，报 `'list' object has no attribute 'get'`）；现在干脆不依赖分类，直接取 sale 里
  `costs` 含**光尘货币**（hash `2817410917`，银币是 `3147280338`）的商品，再按物品类型归类。实测当前
  游戏内当天的光尘商品就是 `镶嵌传导 1250 光尘`，与截图一致。

- 2026-09-29（十一）：**光尘商店（/每日光尘）+ Bungie 账号授权 + 面板二维码刷新修复 + 武器详情页重排** ——
  ① **面板二维码刷新真正生效**（`napcat_runtime.py`）：NapCat 的 `RefreshQRcode` 在登录服务重启时只返回
  `{restarting:true}`（没有 `qrcodeurl`），旧代码直接忽略、再去 `GetQQLoginQrcode` 拿到的还是旧码，
  于是"点刷新没反应"。现在按官方 WebUI 的做法：优先用 `RefreshQRcode` 返回的新 URL；若是 `restarting`
  就轮询 `GetQQLoginQrcode` / `CheckLoginStatus`（实测后者也带 `qrcodeurl`）直到出现**新 URL**，并清掉
  本地渲染缓存强制重新出图；两个接口改成同步函数走线程池（刷新最长等 20s，不能再阻塞事件循环）。
  前端刷新期间暂停 5 秒轮询、失败给重试按钮。② **武器详情页重排（小日向式）**（`webui.py` DETAIL_PAGE）：
  去掉独占整屏的截图（改成 190px 高横幅），内容收进四个卡片区块——**武器素体**（属性条两列，含增删数值）、
  **Perk 池**（框架 / 枪管·发射 / 弹匣·电池 / 特性 N / 起源特性 / 枪托 / 催化 分列）、**大师杰作**（网格）、
  **武器模组**（chips）；每个 Perk 卡**直接显示属性增减**（不再只能悬停才看得到数字），悬停浮层保留说明与
  社区精确数值；每列仍是单选。③ **新增 `/每日光尘`（光尘商店）**：读取游戏内**当前上架**的光尘商品
  （护甲装饰 / 武器装饰 / 机灵 / 机灵投影 / 飞船 / 快雀 每行两个、带真实物品图与光尘价；表情 / 着色器 /
  传送特效不出缩略图，只列名称与价格）。数据来自 **Bungie 官方 `Destiny2.GetVendor`**（泰丝·埃弗里斯
  `3361454721`，components 400/401/402），中文名/类型/稀有度/图标回本地裁剪索引
  `manifest_index/eververse_items.json`（`build_eververse_index.py` 从 220MB 的 `raw_items.json` 流式裁出，
  9088 条 / 1.1MB；含护甲·武器皮肤、机灵外壳·投影、飞船、载具、动作、着色器、传送效果等外观类）。
  ④ **新增 Bungie 账号授权（OAuth）**（`bungie_auth.py`）：读商店这类接口只有 API Key 会返回
  `InsufficientPrivileges`，必须用账号授权。面板新增「**Bungie 账号授权**」卡片（`/api/bungie/status`、
  `/bungie/authorize`、`/bungie/callback`、`/api/bungie/manual`、`/api/bungie/logout`），点一次授权登录 Bungie
  并同意，refresh_token 落盘 `bungie_token.json`（exe 同目录）、access_token 过期自动续。**使用前需要在 `.env`
  填 `BUNGIE_CLIENT_ID` / `BUNGIE_CLIENT_SECRET`**，并在 Bungie 应用里把「开放授权客户端类型」设为**机密**、
  Redirect URL 设为 `https://127.0.0.1:8900/bungie/callback`（**Bungie 只收 https，填 http 会报
  「必须使用 http 以外的通信架构」**）。本机没有证书，跳回来时浏览器会提示"不安全"——不影响授权，
  把**地址栏那一整条带 `code=` 的地址**粘回面板的「粘贴回调地址」框点完成即可（`/api/bungie/manual`）。
  未配置/未授权时，
  `/每日光尘` 与网页 `/eververse` 会给出人话提示而不是报错。网页端新增「光尘商店」标签页。
  ⑤ 说明：第三方站 TodayInDestiny 只有"当季全部光尘清单"（291 件，不含游戏内当日实际上架），
  light.gg 被 Cloudflare 挡，所以实时数据只能走官方授权接口——这也是小日向的做法。
  （改动含 `webui.py` / `destiny_data.py`（打包内）→ 需重新打包 exe；`bot_cards.py`、
  `nonebot_plugins/destiny2.py` 为外置模块，已同步复制到 `dist\D2Query\`；新数据文件
  `manifest_index/eververse_items.json` 已加进 `D2Query.spec` 的 datas 并同步 `_internal`。）

- 2026-09-29（十）：**「串指令」真凶找到并修掉：@ 别人的指令不再抢答** ——
  群里同时挂着别的查询 bot（小日向 `3889001007`），用户是对着**它**发的
  `@小日向Bot /raid`、`/pvp`、`/锻造`，我们的机器人（`3104813702`）也跟着回了一遍：
  NapCat 日志 `17:51:20`、`18:03:05` 各一次（原文：`接收 <- … @小日向Bot (3889001007)  /raid`，
  1 秒后 `小日向Bot` 回图，5 秒后我们也回图）。群里看到的就是「一条指令两个 bot 同时回」。
  根因：`on_command` 在 nonebot 2.5 里**不带 `to_me()` 规则**（裸写 `/raid` 也要响应，
  这是我们要的行为），所以「这条指令是不是给我的」得自己判。现在在事件预处理
  `_as_command` 里加 `_at_other_only()`：**群聊里 @ 了别的 QQ、且没 @ 我 → 整条不处理**
  （`raise IgnoredException`），@ 了我、裸写指令、`qq=0` 的未解析 @ 一律照旧响应。
  踩过的坑：不能扫消息里的 at 段找自己的 qq —— 适配器 `_check_at_me` 在消息预处理阶段
  就把开头的 `@我` 段**摘掉**并只留 `event.to_me`，所以判定必须用 `to_me`。
  被丢掉的消息仍写进面板消息日志（多一个 `skip: "@了别人"` 字段，面板显示成灰色标签），
  免得下次又是「发了却没回」查不出来。
  改的是 `nonebot_plugins/destiny2.py`（外置模块，**复制到 `dist\D2Query\nonebot_plugins\` 即可，
  不用重新打包**）+ `webui.py` 的日志标签（这半条要下次打包才生效）。

- 2026-09-29（九）：**首页「Bot面板」入口挪到右上角** —— 它此前和查询输入框挤在同一列、
  紧贴「查询」按钮下方，看起来像"查询项的子菜单"，实际是跳 `/panel` 的独立后台入口。
  改为 `position:fixed` 固定在右上角（`top:16px;right:16px`，小号描边按钮，悬停变蓝），
  与中间的查询表单在视觉上彻底分开；同时删掉只剩这一个链接的 `#tools` 容器与其样式。
  只改了 `webui.py`，**需重新打包 exe**。

- 2026-09-29（八）：**面板重排 + 生涯武器增量缓存 + 编号补零统一 + 群内 @机器人 直查** ——
  ① **面板任务日志挪到右侧独立侧栏**：此前「后台任务进度」夹在中间列、任务越多越往下长，
  把消息日志和群开关全挤没了。现在主区（状态 / 登录 / 消息日志 / 群开关）在左、**后台任务日志**
  与**账号绑定**在右侧固定侧栏（`position:sticky`），任务列表**定高可滚动 + 分页**（每页 6 条，
  上一页 / 下一页），每条按**发起时间**（倒序，最新在前）显示 `🕒 MM-DD HH:MM:SS`，跑动中再附
  「已跑 N 分 M 秒」，不再无限占页面。窄屏（<1020px）自动落回单列。
  ② **新增「账号绑定」卡片**：右侧列出 `QQ → 玩家名#编号`（`GET /api/bot/bindings` →
  `destiny_data.bindings_snapshot()`），并**统一编号补零**：接口返回的名称编号是整数，
  此前直接插进 f-string 会把 `木白#0202` 显示成 `木白#202`——绑定卡片写 0202、面板进度条写 202，
  两处对不上（截图里的"统一显示问题"就是这个）。新增 `destiny_data.fmt_code()` 补零到 4 位，
  卡片 / 进度条 / 绑定文件 / 首页联想框（`/api/suggest`）全部改走它；绑定写入也改用解析后的规范编号
  （用户写 `#202` 也存成 `#0202`）。绑定文件路径收敛到 `destiny_data.bind_path()`，程序与面板读写同一份。
  ③ **生涯武器（PVP/PVE）改增量缓存**：此前逐场 PGCR 已按 instance 缓存，但每次仍要**重新枚举整段
  对局历史**（翻很多页活动历史，是全生涯任务的大头）。新增**汇总结果缓存** `weapon_agg_cache.json`
  （`membership_id|kind|scope` → 排名 + 覆盖日期）：范围未变时，只枚举/统计**上次覆盖日期之后的新对局**，
  累加进缓存排名；范围变了（如"当前赛季"滚动）才整段重算。二次查询基本只花"翻最近几页"的时间，
  卡片副标题会写「缓存复用，本次只补 N 场」。缓存文件与 `pvp_weapon_cache.json` / `user_bindings.json` 同目录。
  ④ **群里回复自动 @ 发起人**（对齐小日向，避免对方不知道是回给自己的）：所有图片卡 / 文本兜底在群内
  统一前置 `@用户`（`_at_sender`），私聊不加。⑤ **群内 @机器人 + 名字 = 直接出卡片**：新增
  `at_lookup`（`on_message(rule=Rule(_at_me_only))`，priority 12、不阻塞命令），
  `@机器人 秋风` → 武器卡、`@机器人 热力四射` → perk 卡，不用打 `/武器查询`、`/perk查询`。
  **只认真 @**：`_at_me_only` 排除了「只是引用/回复机器人的消息」——适配器 `_check_reply` 在
  回复机器人自己消息时也会把 `to_me` 置真，不改会把"引用一下机器人再发句话"误当成查询乱回卡片；
  私聊同样不做直查。带 `/` 的消息仍交给命令响应器，命中不了才给一句"没找到"。
  改的是 `destiny_data.py` / `webui.py` → **需重新打包 exe**；`nonebot_plugins/destiny2.py` 是外置模块，同步复制即可。

- 2026-09-29（七）：**三个查询页加同一排顶部导航，不再只能靠"返回"退出** ——
  pywebview 原生窗口没有浏览器的后退键，此前从首页点进 `/weapons`、`/perks` 就只能
  `history.back()` 或武器详情页那句「← 返回搜索结果」往回退，很别扭。
  现新增 `webui.navbar(active)`，把 **玩家查询(`/`) | 武器查询(`/weapons`) | Perk查询(`/perks`)**
  做成一排同级切换按钮，挂到首页、武器查询页、Perk 查询页、武器详情页（`DETAIL_PAGE`）四处，
  当前页高亮；武器详情页的「← 返回搜索结果」保留（回列表用），但不再是唯一出路。
  首页原来那行「功能查询：武器查询 / Perk查询」按钮随之删掉（已由导航承担），只留 Bot面板。
  改的是 `webui.py`，**改完必须重新打包 exe 并重启 8900 才生效**。
- 2026-09-29（六）：**Perk 查询去重 + 升金（强化特性）数值金色高亮** ——
  ① **同名条目合并成一张卡**：Manifest 的 `DestinySandboxPerkDefinition` 里同名不同 hash 的条目很多
  （3871 条里 654 个名字有重复，多为同一 perk 的普通版与强化版、或同武器不同赛季版本），
  而中文精确数值是按 **名字** 索引的（`perk_zh.json`），此前逐条渲染会把整段精确数值原样重复一遍
  （如 `混沌重塑` 出现两张卡、数值一字不差）。现改为按名字分组，普通版说明作正文、其余同名版本作
  「同名变体」一行附在下面，精确数值只输出一次。机器人卡（`bot_cards.perk_card`）与查询站
  `/perks` 页（`webui.perks_page`）同步改法。
  ② **升金数值高亮**：Starside 中文文本里 `↑数值` 表示"该 perk 升金（强化特性）后的数值"
  （行首裸 `↑` 是强化版补充说明，共 323/393 条含此标记）。新增 `bot_cards.hl_enh_text()`，
  先 HTML 转义再套 `_ENH` 正则，把 `↑` 与其后的数值/`?`/`%`/`×` 包成金色 `.enh` 高亮块，
  卡片末尾加「金色 ↑ 为升金（强化特性）后的数值」图例；查询站同款样式。
  ③ 精确数值截断从 400 字放宽到 800 字并**保留换行**（`.note` 本就是 `pre-wrap`）——
  此前 `混沌重塑`（499 字）最后那句强化版说明正好被截掉，而那正是升金信息。
  改完已重新打包 exe 并覆盖到 `dist\D2Query`（`bot_cards.py` 外置副本同步）。
- 2026-09-29（六）：**锻造查询智能排序（对齐小日向）** ——
  锻造卡的组不再按静态表顺序铺开，改成两级排序：**① 先按「出的顺序」排（内容上线时间，旧→新）；
  ② 已经全部集齐的组整组往后排**，所以没集齐的组永远在顶部。
  实现：`build_pattern_groups.py` 新增 `RELEASE_ORDER` 常量并写进 `pattern_groups.json` 的 `release` 数组
  （24 组按上线时间：最后一愿 2018-09 → 救赎花园 2019-10 → 深岩墓室 → 玻璃拱顶 → 30周年 → 邪姬魅影/苏生 →
  门徒誓约 → 宿怨 → 侠盗/国王的陨落 → 炽天使 → 光陨之秋/抗战 → 梦魇根源 → 深渊 → 克洛塔/奇巫 → 终愿 →
  终焉之形/救赎的边缘/篇章：回响，跨赛季的 异域任务、世界掉落系列 垫底）；
  `destiny_data.node_report(patterns)` 按 `(整组是否集齐, release 序号)` 排序后再铺开。
  想改成「新的在前」，把 `RELEASE_ORDER` 倒过来重跑脚本即可。静态表 `order` 原样保留作参照。
  改完必须重新打包 exe 并重启 8900 才生效（`pattern_groups.json` 与 `destiny_data.py` 都进包）。
- 2026-09-29（五）：**锻造分组 1:1 对齐小日向 + 异域催化不再混进锻造列表** ——
  ① **锻造分组换成固定表**：新增 `build_pattern_groups.py`（产出 `manifest_index/pattern_groups.json`），
  组名/顺序/每把武器的归属完全对照小日向的锻造页，共 24 组：
  侠盗赛季｜救赎花园｜前兆 | 宿怨赛季｜光陨之秋DLC｜邪姬魅影DLC｜抗战赛季｜玻璃拱顶｜国王的陨落｜异域任务｜
  救赎的边缘｜克洛塔的末日｜最后一愿｜梦魇根源｜门徒誓约｜深岩墓室｜篇章：回响｜终焉之形DLC｜终愿赛季｜
  奇巫赛季｜深渊赛季｜30周年纪念DLC｜炽天使之盾 | 炽天使赛季｜暗屋之声 | 苏生赛季｜世界掉落系列，共 187 条。
  规则 = 藏品来源串查表（`探索内欧姆那`→光陨之秋DLC、`泉源首领`→邪姬魅影DLC、`仄的永恒宝藏窖藏`→30周年…）
  + 无信息量来源串逐把点名（`季票奖励` 的 10 把按所属赛季归组，如 烈火惊骇→宿怨赛季、心灵碎片→苏生赛季；
  空来源串的 目标修订/不同时代/远方吸引/纤薄断崖→深渊赛季；利维坦奇珍的 帝国法令→宿怨赛季、三把刀剑→抗战赛季）
  + 5 条跨组重复（枯骨鳞片/青龙协同之刃/玄武行动之刃/朱雀意图之刃 在 邪姬魅影DLC 与 异域任务 各列一次，零号修订在 炽天使之盾 与 异域任务 各列一次），
  与小日向页每组条数逐个对上（6/7/13/5/14/9/6/6/14/6/6/8/6/6/6/10/8/11/6/6/6/11/6/5）。
  唯一例外：`引力子尖刺`（来源"探索开普勒"的遗留图案）小日向页里没有，`NAME_OVERRIDE` 里置 `OMIT` 跳过；
  想让它出现，把该行改成目标组名即可。
  ② **异域催化不再进锻造卡**：`node_report(patterns)` 跳过「异域催化」槽位（那 141 条是武器催化进度，
  不是锻造图案，之前卡片里会出现"洛伦兹驱动器 能量武器 0/400"这种），组名也不再拿来源串现推
  （此前会冒出"季票奖励""升级过程中获得""高塔异域档案"这种来源串当分组名）。
  没有 `pattern_groups.json` 时退回按槽位分组，页面不会空。
  ③ `D2Query.spec` 增加 `manifest_index/pattern_groups.json` 打包项 —— **改完必须重新打包 exe 并重启 8900 才生效**。
- 2026-09-29（四）：**锻造按副本分类 + 图案显示 x/y 进度 + 副本记录不再只算通关** ——
  ① **锻造分类改为按掉落来源**（小日向式）：新增 `build_pattern_sources.py`，
  拉 Bungie Manifest 的藏品定义（`DestinyCollectibleDefinition.sourceString`，就是游戏内收藏品页那句"来源：…"），
  按武器名建立来源索引 `manifest_index/pattern_sources.json`（1282 条，含 `dungeon` 标记）。
  `node_report(patterns)` 的 group 从槽位（主武器/特殊武器/重武器/异域催化）改成来源名——
  副本排在卡片最前（救赎花园／最后一愿／克洛塔的末日／国王的陨落／救赎的边缘／梦魇根源／深岩墓室／玻璃拱顶／门徒誓约／遗落利坦／地牢二象性／晚星之主／深渊机灵…），
  其后是赛季/活动来源，异域催化与未收录的垫底；查不到来源的落「其他来源（未收录）」，不再拿槽位名当来源。
  ② **未完成的图案显示进度**：图案的"深视共振萃取"进度存在记录目标里，新增 `_obj_progress()` 读出 `progress/completionValue`
  （此前只看 state 位，未完成一律写"未获得"）；现在每张卡显示"武器类型 · x/y"+进度条，如 累积救赎 3/5、命运终结者 4/5；
  已完成与未完成都有值（5/5、2/2），计数器型目标超过需求值时截到目标值（千语 3571/350 → 350/350）。
  ③ **副本记录不再只算全程通关**：`raid_report()` 每副本每组新增 `plays`（参与次数，含中途散团的），
  徽章行加「参与 N」，卡片顶部加「总参与次数（含未通关）」；只打过没通关的副本组现在也会列出来（此前 `clears == 0` 直接跳过，
  所以"大师打了一半的团"根本看不见）。④ **历史翻页加深**：此前每角色只取最近 100 场（`count=100` 单页），
  打得多的人"去年的大师"压根不在窗口里，看着就像记录缺失；现在按 count=250 翻到 3 页（750 场/角色）。
  实测 `Wj#8984`：突袭总参与 1150 / 总通关 405；大师栏 玻璃拱顶 参与 27 通关 15、克洛塔的末日 参与 29 通关 4、
  梦魇根源 参与 8 通关 2、门徒誓约 参与 1 通关 0（后两组旧口径下直接不显示）；锻造 53 个来源分组、322 条图案全部带 x/y。
  ⑤ `build_exe.bat` 改为调用 `D2Query.spec`：旧版手写命令行漏了 playwright、modes.json 等，照它构建出来的 exe 没有图片卡片、模式判定也退化成空表。
- 2026-09-29（三）：**Raid/地牢卡片去掉历史战绩 + 大师模式独立成栏** ——
  ① `/raid`、`/地牢` 卡片不再附「最近对局」列表（此前卡片 35KB，去掉后 11～13KB），要查对局流用 `/历史`；
  ② `raid_report()` 的副本分组从「按副本名合并所有难度」改为**按 (副本, 是否大师) 分组**，
  返回 `raids`（标准/普通/永恒/自定义…）与 `raids_master`（大师）两份，
  卡片据此分成「标准难度」「大师难度」两栏——此前的无暇/单人/双人/三人是把大师和普通混在一起算的，
  分栏后口径才正确；大师徽章不再单独出现（整栏就是大师），普通栏里的「大师 0」这种误导性徽章一并去掉。
  ③ 徽章补齐 raid.report 口径：每行固定 通关/无暇/单人/双人/三人/单人无暇/双人无暇/三人无暇 八枚，
  **计数为 0 的整枚压暗**（此前 0 的直接不显示，与文档描述不符）；
  ④ 详情页 `/card?mode=raidg` 新增 `diff` 参数：点大师栏的副本只列大师对局，按月筛选也按该难度口径。
  已实测 `/raid Wj#8984`：标准 18 组 / 大师 4 组（玻璃拱顶大师 7 次、救赎的边缘 1、克洛塔的末日 1），
  `/地牢`：标准 13 组 / 大师 4 组（晚星之主大师 2 次含 1 次无暇）。
- 2026-09-29（二）：**补齐小日向的战绩类指令** —— 机器人此前只有 `/玩家` `/生涯` `/武器查询` `/perk查询`，
  网页端已经做好的副本/熔炉/称号等页面对应的指令一个都没有，现全部接进插件 `nonebot_plugins/destiny2.py`：
  新增 `/raid`（突袭，mode 4）、`/地牢`（mode 82）、`/pvp`（mode 5，带官方生涯统计）、`/pve`（mode 7）、
  `/智谋`（mode 63）、`/历史`（全模式最近对局流）、`/热力图`（全历史活跃日历）、`/称号`（含镀金进度）、
  `/锻造`（图案按槽位分组）、`/常用武器`（本赛季 PVP 武器排名）、`/帮助`（指令一览），
  并各带英文/中文别名（`突袭`/`dungeon`/`熔炉`/`gambit`/`战绩`/`活跃`/`图案`/`mvp`…）。
  **卡片排版不重写**：`bot_cards.py` 新增 `_webui()` 委托层，直接调用查询站 `webui.py` 的
  `render_raid_card` / `render_nodes` / `render_history_card` / `render_match_card` / `render_wpvp` / `render_heat`
  出 HTML（同进程取模块，兼容 exe 的 `webui` 与源码直跑的 `__main__` 两种加载名），
  因此 QQ 出图与网页端逐像素同源，网页端改排版机器人自动跟着变。
  `/热力图` 与 `/常用武器` 是后台翻页任务，先回一张「正在统计…」卡片，跑完再发结果图（超时/失败给提示卡）。
  已用 `test_ws.py` 实测 `/帮助`、`/raid Wj#8984`（99 次通关/18 个副本/269 场对局）、
  `/称号`（17/84，可镀金 14）、`/历史`（150 场）、`/pvp`（282 场，胜率 49.2%，14 个模式细分）
  全部出图正常；`test_ws.py` 的 WS 客户端加了 `max_size=None`（卡片 base64 常超默认 1MB 会被测试端掐断）。
  **未做**：勋章/击杀纪念、仓库搜索、周常专项、日报周报（数据层暂无对应接口）。
- 2026-09-29：**机器人回复全面改为图片卡片（小日向式）**——不再回纯文本，所有指令都发图：
  新增 `card_render.py`（Playwright 驱动无头浏览器截图，`full_page` 高度自适应；浏览器按
  自带 chromium → 系统 Edge → Chrome 依次回退，本机实测走 Edge，无需下载浏览器）与
  `bot_cards.py`（卡片排版，配色/圆角/字体对齐网页查询站的 `CARD_CSS`，保证机器人图片与
  查询站同一套视觉）。卡片类型：`/玩家`（徽章横幅三角色 + 最高光能 + 三模式速览）、
  `/生涯`（PVP/PVE/智谋生涯表 + 总计）、`/武器查询`（图标 + 赛季徽标整张覆盖、属性条、
  Perk 分列含固定配件/催化、精确数值；查询过宽时出候选列表卡）、`/perk查询`（图标 + 官方说明 +
  属性增减标签 + 中文精确数值，同名强化版标「同名变体」）、以及绑定/解绑/未找到等**提示卡**。
  同名武器版本、同名 perk 变体按 key 去重，不再重复列「秋风、秋风、秋风」。
  渲染失败才回退纯文本。`D2Query.spec` 用 `collect_all('playwright')` 打进 exe（+100MB），
  exe 已实测能出图；`test_ws.py` 升级为可看图片回复（存 `_ws_out.png`）并支持指定指令/端口。
- 2026-09-28：**触发词改为小日向式 + QQ 账号绑定**——`d2`→`/玩家`、`d2周报`→`/生涯`、
  `d2武器`→`/武器查询`、`d2perk`→`/perk查询`（旧 `d2*` 全部保留为别名）；新增
  `/绑定 玩家名#编号`、`/解绑`、`/我的`，绑定后 `/玩家`、`/生涯` 等玩家类指令**可省去名字**
  （带参数优先，否则回退绑定账号，未绑定则提示绑定）。绑定存 `user_bindings.json`，
  按 QQ 号区分，群聊/私聊共用。仅重命名与新增指令，未改查询逻辑。
- 2026-09-28：修复**指令无响应**——`on_command` 只认 `command_start` 里的前缀（NoneBot 默认
  仅 `/`），用户/群友裸写 `d2 玩家#编号` 时面板日志会记录该消息、但机器人**完全不回**。
  现把 `command_start` 设为 `{"/", ""}`（`bot_runtime.py` / `bot.py`），裸写与带斜杠都可触发，
  `d2周报`/`d2武器`/`d2perk` 仍按最长前缀优先匹配。
  （**2026-09-29 已调整**：裸写会命中群里以命令词开头的中文句子，现改回只认 `/` 前缀 +
  `force_whitespace`，见 2026-09-29 更新 ⑪。）
- 2026-09-28：修复面板二维码**一直显示过期**——NapCat 只在启动时写一次
  `cache/qrcode.png`，之后刷新二维码只更新内部 URL、不再落盘，面板读该文件永远是旧码。
  改为从 `/QQLogin/GetQQLoginQrcode` 取**实时登录 URL**后用新增的 `qr_png.py`
  （纯标准库 QR 编码器，无第三方依赖）本地出图；同一张码展示超 110 秒会自动调
  `/QQLogin/RefreshQRcode` 续期；面板新增「刷新二维码」按钮
  （`POST /api/napcat/qr/refresh`）。
- 2026-09-28：Bot 面板内置 NapCat 登录——新增 `/api/napcat/start|status|qr` 三个接口；
  `napcat_runtime.py` 新增 WebUI API 调用（登录态 / 二维码）、`onebot11_<QQ>.json`
  自动生成与登录后自动下发配置；面板改为按钮启动 + 内嵌二维码扫码。
- 2026-09-28：新增**消息日志**（面板可见哪个群发了什么、机器人回了什么）：
  `bot_log.py` 内存环形缓冲 500 条；插件记录收到的 d2 指令（群内非指令不记）与私聊消息、
  并统一经 `_reply()` 记录回复；新增 `/api/bot/logs`、`/api/bot/logs/clear`，面板支持按群筛选/清空。
- 2026-09-28：修复 exe 版 bot 线程启动失败——PyInstaller 补 hidden-import
  （`nonebot.drivers.fastapi/http/websockets`、onebot v11 适配器）；`bot_runtime.py`
  打包时从 exe 旁加载 `nonebot_plugins` 并把父目录加入 `sys.path`；构建后需把
  `napcat_shell`（含 node_modules）、`nonebot_plugins`、`.env` 复制到 `dist\D2Query\`。
- 2026-09-28：登录链路修复与风控防护——WebUI 凭证改为缓存复用（原每次轮询都登录，
  触发 WebUI 限流导致“扫码后没反应”）；改配置走 `/OB11Config/SetConfig` 运行时下发
  （原写文件被 NapCat 退出回写覆盖）；快速登录 `NapCatWinBootMain.exe <QQ.exe> <Hook.dll> <uin>`
  的 uin 需**裸传**；新增 **5 分钟启动冷却**，且 watcher **不再自动重启** NapCat
  （短时间内反复登录会触发腾讯风控导致账号无法登录）。

> ⚠️ **风控提醒**：NapCat 属第三方协议端，频繁登录（含反复重启、反复扫码）会触发腾讯风控。
> 被风控后按手机官方 QQ 的安全验证提示处理，或静置数小时自动解除，期间不要再重试登录。

## 全功能回归测试

```
.venv\Scripts\python -X utf8 test_sweep.py [base_url]
```
对运行中的站点逐项断言：2 个玩家 × 9 个标签页内容、异域/传奇/锻造武器详情、
武器搜索赛季徽标、5 个 perk 的中文数值、首页/面板/bot API、PGCR 对局详情、
PVP 生涯武器与热力图两个后台任务。当前 43/43 通过（网页版与 exe 均测）。
注意：Bungie 接口有限流，连续大量查询会临时返回"没找到玩家"，稍等即可恢复。

### 指令识别回归（不需要 QQ）

```
# 先启动 bot（.venv\Scripts\python bot.py，8081）或已装好的 exe（8901），再：
.venv\Scripts\python -X utf8 _rtest\match_test.py 8901
```
7 条用例断言：不带 `/` 的纯文本 / `pve是顺手写的` 连写 / 裸写指令 / 英文词开头 **都不响应**；
`/帮助`、`@机器人后跟指令`、`引用消息后跟指令` **都要响应**。每个用例用独立 `group_id` 区分回复。

## 启动步骤

1. 启动本端（NoneBot，监听 127.0.0.1:8081）：
   ```
   .venv\Scripts\python bot.py
   ```
2. 安装并启动 [NapCat](https://napneko.github.io/)（NapCat.Shell 即可），在其 WebUI 里配置
   **反向 WebSocket** 连接，地址填：
   ```
   ws://127.0.0.1:8081/onebot/v11/ws
   ```
3. 用要当机器人的 QQ 号登录 NapCat，之后在群里发 `/d2 Wj#8984` 即可。

## 配置

- `.env` — 端口、Bungie API Key（泄露后去 bungie.net 应用管理页重置）

## 目录结构

- `bot.py` — 入口
- `nonebot_plugins/destiny2.py` — 查询插件（指令→取数据→发图片卡），加新指令改这里
- `bot_cards.py` — 卡片排版（把数据拼成 HTML；配色对齐网页查询站）
- `card_render.py` — HTML → PNG（Playwright 无头浏览器；Edge/chromium/Chrome 自动回退）
- `test_ws.py` — 无 QQ 环境的模拟测试（直连 WS 发假消息，图片存 `_ws_out.png`）
- `test_cards.py` — 只渲染卡片出图自查（`python test_cards.py 武器名 perk名 [玩家名#编号]`）
- `dim_web.py` — DIM 板块的五个页面与接口（`/dim` 背包仓库、`/dim/triumphs` 进度、`/dim/loadouts` 配装、`/dim/optimizer` 配装器、`/dim/manage` 管理器）
- `dim_ui.py` — DIM 板块的前端公共层（DIM 官方配色/格子/弹窗样式 + 物品弹窗、拖拽、标签、搜索语法、设置抽屉等公共 JS）
- `dim_data.py` — DIM 板块数据层（一次 GetProfile 拉全量；TransferItem/EquipItem/SetItemLockState 写操作；成就树）
- `dim_user.py` — DIM 板块本地数据（物品标签与备注、自建配装，落 `dim_user.json`；Bungie 没有开放标签接口，DIM 官方也是本地存）
- `dim_opt.py` — 配装器的护甲组合搜索（Pareto 剪枝 + DFS 分支定界）

## 后续可扩展

- 把查询站已有、机器人还没有的板块也做成卡片：勋章/击杀纪念、赛季生涯、日报周报、仓库搜索
- 突袭/地牢「本周轮换」：官方 `Destiny2.GetPublicMilestones`（**只要 API Key，不需要 OAuth**）实测可查，
  12 个里程碑带周区间（北京时间周三凌晨 1 点换）；但它把 9 个突袭一起返回，**不标「本周轮换的那两个」**，
  凯旋纪念碑那种未来排期表是社区自维护的固定顺序。要做的话按周缓存即可（写法同光尘商店）
- Manifest 物品库：翻译装备 hash → 名称/图标
- 查询缓存：避免触发 Bungie 每秒 25 次/每天 25 万次限流
- 卡片渲染进程内复用浏览器（已做），若并发量大再考虑渲染队列/降采样
