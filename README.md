<div align="center">

# 雷尼克斯联合-1

**命运 2 本地查询站 + QQ 机器人**　·　一个 exe 同时搞定「网页战绩查询」和「群里丢指令出图」

原生窗口界面 · 全指令图片回复 · 内置 DIM 板块 · 内置 NapCat 扫码登录 · 无需外网服务器

[![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![NoneBot2](https://img.shields.io/badge/NoneBot2-2.5.0-EE4C2C)](https://nonebot.dev/)
[![OneBot V11](https://img.shields.io/badge/OneBot-v11-1AAD19)](https://github.com/botuniverse/onebot-11)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows)](https://www.microsoft.com/windows)
[![Data](https://img.shields.io/badge/Data-Bungie%20API-1E90FF)](https://bungie-net.github.io/)

</div>

---

## 它是什么

一个 **单机运行** 的命运 2 玩家数据工具，把三件事合到了一起：

| 模块 | 说明 |
| :--- | :--- |
| **网页查询站** | 本地 8900 端口，原生窗口（pywebview）打开，无需浏览器插件、不依赖任何公网服务 |
| **QQ 机器人** | 内置 NoneBot2 + OneBot V11 + NapCat，面板里扫码登录，群里发 `/生涯` 直接出图片卡片 |
| **DIM 板块** | 对着官方 DIM 复刻的背包 / 进度 / 配装 / 配装器 / 管理器五个页面，标签与自建配装存本地 |

数据全部来自 **Bungie 官方 API + Manifest（简体中文）**，本地索引 2208 把武器、三万多条物品定义，
查询走本地缓存，除了 Bungie 本身的请求外不碰任何第三方站点。

<div align="center">
<img src="docs/screenshots/card-raid.png" width="820" alt="突袭战绩卡片">
<br><sub>▲ Raid 战绩卡：跨角色聚合，通关 / 无暇 / 大师 / 单人 / 双人 / 三人 / 单人无暇 / 双人无暇 / 三人无暇 全维度徽章，0 值自动压暗，标注该副本打过的难度与最快用时</sub>
</div>

---

## 功能一览

<table>
<tr><td width="50%" valign="top">

### 📊 玩家战绩
- **总览**　光能 / 时长 / 三角色 + 三模式生涯速览
- **PVP / PVE / 智谋**　官方生涯统计（含胜率、精准击杀）+ 近期战绩（连胜连败 / KDA / 平均效率）+ **模式细分表**
- **Raid / 地牢**　跨角色聚合，八枚徽章逐副本一行，点击展开细分 + 按月筛选
- **战绩流**　全模式对局流，PvE 显示「通关 / 未通关」，PvP 显示「胜利 / 失败」
- **任意对局可点开** PGCR 详情：团队汇总、MVP、玩家横幅卡、武器明细

</td><td width="50%" valign="top">

### 📈 深度统计
- **生涯武器**　PVP / PVE 双口径，击杀 / 出场 / 场均 / 占比 / **精准击杀 / 爆头率**七列，顶部六块汇总（武器击杀 / 近战 / 手雷 / 大招）
- 可选**全生涯**或**指定赛季**；逐场 PGCR 首查 2～3 分钟，之后走缓存秒开
- **热力图**　按赛季分组的全历史月历，同赛季同色相、越亮玩得越久
- **称号**　x/y 进度条 + **可镀金 / 已镀金**单独区分（金色描边与实心徽章）
- **锻造**　按掉落来源分组，顶部永远留没集齐的组

</td></tr>
<tr><td valign="top">

### 🎒 DIM 板块
对齐官方 DIM 的五个页面，取色与布局照 DIM 源码：
- **背包** `/dim`　四列（泰坦 / 猎人 / 术士 / 保险库），**拖拽搬运**、拖到装备槽即装备
- **物品弹窗**　悬停出详情，左键固定，右键出操作菜单；属性条 / Perk / 插槽模组 / 来源
- **标签与备注**　收藏 ♥ / 保留 ⚑ / 丢弃 ✖ / 注入 ⚡ / 归档 🗄，快捷键 `shift+1..5`
- **搜索语法**　`is:weapon`、`tag:favorite`、`notes:xx`、`not:<词>` + 13 个快捷 chip
- **配装** `/dim/loadouts`　游戏内 20 套 + 自建配装，支持分享链接 / 导入 / 一键应用 / 两套对比
- **配装器** `/dim/optimizer`　属性优先级 + 最小最大值 + 假定大师之作，穷举 5 护甲槽出前 24 套
- **管理器** `/dim/manage`　表格式整理，勾选后批量打标签 / 批量搬运

</td><td valign="top">

### 🔫 图鉴与查询
- **武器图鉴** `/catalog`　全量 **2208 把**武器网格，六维度（类型 / 框架 / 元素 / 弹药 / 槽位 / 品质）**动态联动计数**筛选
- 跨字段搜索，认社区叫法（`喷子`→霰弹枪、`绿弹`→特殊弹药、`主手`→主武器…）
- 默认**合并同名版本**（2208 条 → 1318 个名字），点卡片就地弹出详情层
- **武器详情**　Perk 分列**单选**，属性条实时联动（加成绿 / 削减红），Perk 悬停出精确数值浮层
- **异域催化**　145 把金枪，中文说明优先、社区英文兜底
- **Perk 查询** `/perks`　395 条中文数值说明，覆盖 2207/2208 把武器
- **光尘商店 / 本周轮换**　走 OAuth 读当日 vendor，轮换按官方里程碑 + Starside 配对表推算

</td></tr>
<tr><td valign="top">

### 🤖 QQ 机器人
- 群里所有回复都是**图片卡片**（HTML 排版 + 无头浏览器截图，小日向式）
- 战绩类卡片**直接复用网页端排版**——改网页样式，机器人出图跟着变，不用维护两套
- 群内回复自动 `@` 发起人，私聊不加
- 支持「引用某条消息 / @机器人 后再发指令」，也支持直接 `@机器人 武器名` 出卡
- **面板内置 NapCat 一键登录**：二维码直接显示在面板里，手机扫码即可，全程不用打开 NapCat WebUI
- 面板带**后台任务进度条**（谁发起的 / 查到第几场 / 排队位次）与**消息日志**

</td><td valign="top">

### ⚙️ 工程细节
- **单文件 exe**　PyInstaller 打包，原生窗口，无 cmd 黑框，端口被占用自动顺延
- **事件循环隔离**　同时跑三个 loop（主界面 / Bungie 回跳 HTTPS / QQ bot），httpx 客户端与 Playwright 浏览器**按 loop 各持一份**
- **本地缓存**　PGCR 逐场落盘（上限 20000 场），生涯武器 / 热力图结果级缓存，再查秒开
- **任务串行队列**　长任务排队执行，避免被 Bungie 限流
- **离线自检**　`test_sweep.py` 57 项接口断言；`_rtest/dim_ui_check.py` 用无头 Edge 逐页 DOM 断言，**写请求全部 route 拦截，不打真实账号**

</td></tr>
</table>

---

## 界面预览

**DIM 背包仓库** —— 四列布局、拖拽搬运、赛季竖条水印、大师之作金边

<div align="center"><img src="docs/screenshots/dim-inv.png" width="860" alt="DIM 背包"></div>

<table>
<tr>
<td width="50%"><div align="center"><img src="docs/screenshots/catalog.png" alt="武器图鉴"><br><sub><b>武器图鉴</b>　2208 把武器 · 六维联动筛选</sub></div></td>
<td width="50%"><div align="center"><img src="docs/screenshots/dim-opt.png" alt="配装器"><br><sub><b>配装器</b>　属性优先级 + 假定大师之作穷举</sub></div></td>
</tr>
<tr>
<td><div align="center"><img src="docs/screenshots/eververse.png" alt="光尘商店"><br><sub><b>光尘商店</b>　当日轮换商品</sub></div></td>
<td><div align="center"><img src="docs/screenshots/rotation.png" alt="本周轮换"><br><sub><b>本周轮换</b>　突袭 + 固定配对推算</sub></div></td>
</tr>
<tr>
<td><div align="center"><img src="docs/screenshots/card-all.png" alt="总览卡片"><br><sub><b>总览卡片</b>　群指令出图效果</sub></div></td>
<td><div align="center"><img src="docs/screenshots/card-titles.png" alt="称号卡片"><br><sub><b>称号卡片</b>　x/y 进度 + 可镀金 / 已镀金区分</sub></div></td>
</tr>
<tr>
<td><div align="center"><img src="docs/screenshots/perks.png" alt="Perk 查询"><br><sub><b>Perk 查询</b>　中文精确数值 + 属性增减标签</sub></div></td>
<td><div align="center"><img src="docs/screenshots/home.png" alt="首页"><br><sub><b>首页</b>　七个功能标签 · 原生窗口</sub></div></td>
</tr>
</table>

---

## 快速开始

### 方式一：直接用 release 包（推荐）

1. 到 [Releases](../../releases) 下载 `D2Query-v1.0.0-win64.zip`，解压到**任意非中文敏感路径**（含中文也可以，但别放太深）；
2. 把 `.env.example` 复制成 `.env`，填入 Bungie 凭据（见下方[配置](#配置)）；
3. 双击 `D2Query.exe` —— 弹出原生窗口，网页查询站立即可用；
4. 要连 QQ：窗口内进 **Bot面板** → 「启动并扫码登录」→ 手机 QQ 扫码。

> release 包是**免 Python 环境**的绿色包，`_internal/` 里已经带好 Python 运行时、Playwright 驱动和全部离线索引。
> 图片卡片用**系统自带 Edge** 渲染，不需要 `playwright install`。

### 方式二：从源码运行

```bash
git clone https://github.com/WJ1752/lenix-union-1.git
cd lenix-union-1

python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 建索引（首次必须，从 Bungie Manifest 拉取，约需几分钟）
.venv\Scripts\python build_manifest.py
.venv\Scripts\python build_weapon_details.py
.venv\Scripts\python enrich_weapons_ci.py
.venv\Scripts\python build_dim_index.py
.venv\Scripts\python build_weapon_filter_index.py
.venv\Scripts\python build_weapon_catalog.py
.venv\Scripts\python build_modes.py

copy .env.example .env    # 然后填 Bungie 凭据
.venv\Scripts\python webui.py     # → http://127.0.0.1:8900
```

停止并释放 8900 / 8901 端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File stop_webui.ps1
```

> ⚠️ **源码版和 exe 不能同时跑**，两者都要绑 8900 / 8901。跑 exe 前先执行一次 `stop_webui.ps1`。

---

## 配置

`.env`（放在 exe / 源码同目录）：

```ini
BUNGIE_API_KEY=          # 必填，https://www.bungie.net/en/Application
BUNGIE_CLIENT_ID=        # 光尘商店 / DIM 板块需要 OAuth
BUNGIE_CLIENT_SECRET=
BUNGIE_REDIRECT_URI=https://127.0.0.1:8902/bungie/callback
HOST=127.0.0.1
PORT=8900
```

申请 API Key 时，**重定向 URL 必须填 `https://127.0.0.1:8902/bungie/callback`**
（Bungie 只接受 https，所以程序另起一个带自签证书的 8902 口收授权回跳，证书在 `certs/`）。

<details>
<summary><b>哪些功能需要 OAuth、哪些只要 API Key？</b></summary>

| 功能 | 只需要 API Key | 需要 OAuth 授权 |
| :--- | :---: | :---: |
| 玩家战绩 / Raid / 地牢 / PVP / PVE / 历史 / 热力图 | ✅ | |
| 武器图鉴 / 武器查询 / Perk 查询 / 武器筛选 / 轮换 | ✅ | |
| 光尘商店 | | ✅ |
| DIM 板块（背包 / 配装 / 配装器 / 管理器） | | ✅ |
| QQ 群里查别人战绩 | ✅ | |
| QQ 里 `/我的` 相关绑定账号的资产操作 | | ✅ |

</details>

---

## QQ 指令

> **所有指令必须带 `/` 前缀**，命令词后要紧跟空白或直接结束。
> 群里闲聊「pve是顺手写的…」不会被当成查询；前置的 `@机器人` 与「引用某条消息」会自动忽略。
> **所有回复都是图片卡片。** 旧 `d2` 系列全部保留为别名（同样要带 `/`）。

### 账号

| 指令 | 别名 | 说明 |
| :--- | :--- | :--- |
| `/绑定 玩家名#编号` | `/bind` | 绑定自己的命运 2 账号，绑定后玩家类指令可省名字 |
| `/解绑` | `/unbind` | 解除绑定 |
| `/我的` | `/账号` `/me` | 查看当前绑定 |

### 战绩

| 指令 | 别名 | 说明 |
| :--- | :--- | :--- |
| `/玩家 [玩家名#编号]` | `/d2` | 徽章横幅三角色 + 最高光能 + 三模式速览 |
| `/生涯` | `/周报` `/d2周报` | PVP / PVE / 智谋生涯表 + 总计 |
| `/raid` | `/突袭` `/d2raid` | 分标准 / 大师两栏，每副本八枚徽章 |
| `/地牢` | `/dungeon` `/d2地牢` | 口径同 raid（mode 82） |
| `/pvp` | `/熔炉` `/d2pvp` | 生涯统计 + 近期战绩 + 模式细分表 |
| `/pve` | `/d2pve` | 用通关率代替胜率（排除探索 / 巡逻） |
| `/智谋` | `/gambit` `/d2智谋` | 官方聚合接口已下线，走对局历史聚合 |
| `/历史` | `/战绩` `/最近对局` `/d2历史` | 全模式最近对局流 |
| `/常用武器` | `/武器统计` `/mvp` `/生涯武器` `/pvp生涯武器` | PVP 武器排名，前三奖牌色 + 击杀条 |
| `/pve生涯武器` | `/pve武器` `/pve常用武器` | PVE 口径，默认当前赛季 |
| `/热力图` | `/活跃` `/d2热力图` | 按赛季分组的全历史月历 |

> 生涯武器 / 热力图是**后台任务**，先回一张「统计中」卡片。
> 参数末尾可写 `s27` / `赛季27` / `全生涯` 指定统计范围。

### 数据 & 资产

| 指令 | 别名 | 说明 |
| :--- | :--- | :--- |
| `/武器查询 武器名` | `/d2武器` | 图标 + 属性条 + Perk 分列 + 精确数值 |
| `/perk查询 perk名` | `/特性查询` `/d2perk` `/d2特性` | 图标 + 官方说明 + 数值增减标签 |
| `/武器筛选 关键词…` | `/d2武器筛选` `/d2筛选` `/筛选武器` | 从 2208 把里按条件筛列表，多词之间是与 |
| `/每日光尘` | `/光尘商店` `/光尘` `/eververse` | 当日光尘商店商品 |
| `/轮换` | `/本周轮换` `/突袭轮换` `/raid轮换` | 本周轮换突袭 + 推算的另三个 |
| `/称号` | `/d2称号` | 称号 / 传承称号分组，x/y 进度 + 镀金标记 |
| `/锻造` | `/图案` `/锻造图案` `/d2锻造` | 锻造图案按掉落来源分组 |
| `/帮助` | `/help` `/菜单` `/指令` | 指令一览 |

**免指令**：`@机器人 武器名` 或 `@机器人 perk名` 直接出对应卡片（只认真 @，引用机器人消息不算）。

<details>
<summary><b><code>/武器筛选</code> 支持的词库</b></summary>

| 维度 | 可用词 |
| :--- | :--- |
| 类型 | `手炮` `微冲` `喷子` `机枪` `榴弹` `弓` `偃月` `线性` …（含 `AR` `SG` `SMG` `LFR` 缩写） |
| 弹药 | `主手` `白弹` / `副手` `绿弹` / `重弹` `紫弹` |
| 槽位 | `动能` `能量` `威能` |
| 元素 | `电` `火` `冰` `虚空` `缚丝` |
| 射速 | `140` `900` … |
| 框架 | `速射` `波形` `适配` `精密` `轻质` `高冲` … |
| 特性 | 任意 Perk 名，如 `爆破专家` `雪上加霜` `事不过四` |
| 其他 | `锻造`（可锻造）`异域`（金枪） |

匹配分两级：词若能在「类型 / 弹药 / 槽位 / 元素 / 框架」命中就只用这几个字段，否则才去武器名 / 来源 / 特性名里找
—— 否则「轻质」会从 222 条轻质框架涨到 627 条（轻质弹匣）。词与词矛盾时自动忽略其中一个并在卡片上注明。

</details>

---

## 项目结构

```
lenix-union-1/
├── d2query_launcher.py     # exe 入口：原生窗口 + 起 uvicorn + 起 bot 线程
├── webui.py                # 网页查询站（路由 + 全部页面 CSS/JS + render_* 排版）
├── destiny_data.py         # Bungie API 数据层（含 PGCR 缓存与任务队列）
├── bungie_auth.py          # OAuth + 自签 https 回跳口
├── bot*.py                 # QQ 侧：nonebot 运行时 / 图片卡片 / 消息日志
├── card_render.py          # Playwright 卡片渲染（按事件循环各持一个浏览器）
├── dim_*.py                # DIM 板块：数据层 / 路由 / 主题与公共 JS / 标签 / 配装器
├── weapon_filter.py        # /武器筛选 词库与匹配
├── napcat_runtime.py       # 内置 NapCat 的启动与扫码登录桥接
├── build_*.py              # 索引构建脚本（Manifest → manifest_index/*.json）
├── scrape_starside_*.py    # 中文 Perk / 催化说明抓取
├── enrich_weapons_ci.py    # 注入社区数值
├── test_sweep.py           # 全接口回归断言（57 项，对运行中的查询站跑）
├── _rtest/                 # 浏览器自检与调试脚本
├── nonebot_plugins/
│   └── destiny2.py         # QQ 指令实现（22 条指令，exe 外置加载）
├── certs/                  # 自签 https 证书（供 Bungie 回跳）
├── manifest_index/         # 离线索引（构建产物，不入库，见下）
├── D2Query.spec            # PyInstaller 打包配置
├── build_exe.bat           # 一键打包
├── deploy_exe.ps1          # 部署（覆盖 exe + 同步外置模块）
└── stop_webui.ps1          # 停止服务并释放端口
```

> `manifest_index/` 和 `napcat_shell/` **不进版本库**：前者由 `build_*.py` 从 Bungie Manifest 生成，
> 后者是第三方 QQ 协议端。release 包和 exe 里都已带好。

---

## 构建与打包

```bat
build_exe.bat
```

走 `D2Query.spec`，产物在 `dist_build\D2Query\`。**`_internal` 里会自动收录 `manifest_index/*.json`**
（只排除构建中间产物 `raw_items.json` 220MB / `raw_plugsets.json`）与 Playwright 完整驱动（体积 +100MB）。

部署：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy_exe.ps1
```

<details>
<summary><b>哪些文件改动需要重新打包？</b></summary>

| 改了什么 | 要做什么 |
| :--- | :--- |
| `webui.py` / `destiny_data.py` / `dim_*.py` / `D2Query.spec` | **重新打包** `build_exe.bat` + `deploy_exe.ps1` |
| `nonebot_plugins/destiny2.py` / `card_render.py` / `bot_cards.py` / `weapon_filter.py` | 外置加载，**复制到 exe 同目录**重启即可（不用重打包） |
| 新增 `manifest_index/*.json` | spec 用 glob 自动收集，但 exe 已生成的话需重打包才会进 `_internal` |
| 网页样式 / 卡片排版 | 重新打包（`webui.py` 内嵌） |

> `_internal` 复制务必用 `cp -r`；`robocopy /MIR` 在中文路径下会卡住。

</details>

---

## 常见问题

<details>
<summary><b>启动报 <code>[Errno 10048] bind ('127.0.0.1', 8901)</code></b></summary>

源码版和 exe 抢同一个端口。先跑 `stop_webui.ps1`（注意 Windows 下 venv 的 `python.exe` 是个会再拉子进程的 stub，
所以脚本会杀掉两个进程），再启动 exe。

</details>

<details>
<summary><b>DIM 页面报 <code>No such file or directory: manifest_index\stats.json</code></b></summary>

exe 里少了索引文件。`D2Query.spec` 已改成 glob 自动收集，重跑 `build_exe.bat` 即可；
临时救急可以把缺的 json 直接补进 `_internal\manifest_index\`。

</details>

<details>
<summary><b>搬运 / 装备物品后数据没变</b></summary>

Bungie 档案接口是**快照式**的，写完立刻回读拿到的是旧数据，最久约 40 秒才对上。
前端是「先在本地挪过去立即重绘，再隔 4s / 16s 各对账一次」，属于预期行为。

</details>

<details>
<summary><b>卡片发出去是空白 / 渲染失败</b></summary>

图片卡片依赖无头浏览器。优先用**系统 Edge**（`channel="msedge"`），依次回退自带 chromium、Chrome。
如果三者都没有会退回纯文字。exe 包已内置 Playwright 驱动，无需 `playwright install`。

</details>

<details>
<summary><b>QQ 登录扫码没反应 / napcat.log 为空</b></summary>

内置 NapCat 需要**新版 QQNT**。实测 QQ `9.7.18`（2023 旧版）注入会静默失败，`9.9.36` 正常。

</details>

---

## 说明

- 本项目为**单账号自用**工具，DIM 板块的标签 / 自建配装存在本地 `dim_user.json`。
  Bungie **没有开放标签接口**（`SetTag` 404），官方 DIM 也是本地存，故这里一致。
- `.env`、`bungie_token.json`、`user_bindings.json`、各类缓存均已在 `.gitignore` 中排除，**不要提交**。
- 详细的功能演进记录与「实测踩出来的」实现约束见 **[CHANGELOG.md](CHANGELOG.md)** —— 改代码前建议先读。

<div align="center"><sub>Built for the Chinese Destiny 2 community · 数据来源 Bungie.net</sub></div>
