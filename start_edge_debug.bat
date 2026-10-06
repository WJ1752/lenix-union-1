@echo off
chcp 65001 >nul
rem ==================================================================
rem  start_edge_debug.bat —— 以远程调试端口启动专用 Edge（武器使用率 CDP 通道）
rem
rem  【正常情况下用不着这个脚本】面板「全库刷新 / 启动通道」现在会自己把调试 Edge
rem  拉起来（weapon_usage.ensure_channel：独立 profile 再开一个 Edge 实例，与用户正在
rem  用的 Edge 并存，不关人家窗口）。只有面板提示「找不到 msedge.exe」或端口怎么都
rem  起不来时，才双击本脚本兜底。
rem
rem  做的事：
rem    1) 先探 127.0.0.1:9222：已经在线就直接退出，什么都不动
rem    2) 只关掉占用【调试 profile】的 Edge 进程（不动你正在用的 Edge）
rem    3) 用独立 profile + 端口 9222 起 Edge：新版 Edge 对【默认】配置目录会忽略
rem       --remote-debugging-port，必须独立 user-data-dir 端口才生效；该目录里已有
rem       cookies/登录态，light.gg 的 cf_clearance 保留，通常无需重新过人机验证
rem    4) 端口仍未就绪才提示，并问你要不要强制重启（会 taskkill 所有 Edge）
rem
rem  之后武器使用率管线（weapon_usage.py 的 CDP 提供者）会通过 127.0.0.1:9222 借用这个
rem  已验证的浏览器会话抓 light.gg 数据。注意：别把这个调试 Edge 全关了，关了通道就断
rem  （下次面板刷新还会自动拉起来）。
rem ==================================================================
title Edge 调试模式启动器（light.gg 已验证会话复用）

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=msedge.exe"
set "EDGE_PROFILE=F:\edge_debug_profile"

echo.
curl -s --max-time 3 http://127.0.0.1:9222/json/version | findstr /i "webSocketDebuggerUrl" >nul 2>&1
if %errorlevel%==0 (
    echo  √ 调试通道已在线（端口 9222 可访问），无需重启。可直接关闭本窗口。
    pause >nul
    exit /b 0
)

echo  [1/3] 关闭占用调试 profile 的旧 Edge（%EDGE_PROFILE%，不动你正在用的 Edge）……
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedge.exe' -and $_.CommandLine -like '*%EDGE_PROFILE%*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 1 /nobreak >nul

echo  [2/3] 以调试模式启动 Edge（端口 9222）……
start "" "%EDGE%" --user-data-dir="%EDGE_PROFILE%" --remote-debugging-port=9222 --restore-last-session --no-first-run --no-default-browser-check
timeout /t 4 /nobreak >nul

echo  [3/3] 等待端口就绪……
curl -s --max-time 3 http://127.0.0.1:9222/json/version | findstr /i "webSocketDebuggerUrl" >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo  √ 成功！Edge 已带调试端口运行。看到本提示后可直接关闭本窗口。
    pause >nul
    exit /b 0
)

echo.
echo  × 端口仍未就绪：多半是该 profile 已被别的 Edge 进程占着（调试窗口开着但端口没生效）。
echo    按任意键 = 强制重启（taskkill 所有 Edge 再重开，你的浏览窗口会被关掉）；
echo    直接关窗口 = 放弃（可过几秒重新双击本脚本再试）。
pause >nul
taskkill /f /im msedge.exe >nul 2>&1
timeout /t 1 /nobreak >nul
start "" "%EDGE%" --user-data-dir="%EDGE_PROFILE%" --remote-debugging-port=9222 --restore-last-session --no-first-run --no-default-browser-check
timeout /t 5 /nobreak >nul
curl -s --max-time 3 http://127.0.0.1:9222/json/version | findstr /i "webSocketDebuggerUrl" >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo  √ 强制重启后端口就绪，可以用了。
) else (
    echo.
    echo  × 还是不行：确认 Edge 能正常打开、安全软件没拦调试端口，或看任务管理器里有没有
    echo    卡死的 msedge.exe 残留进程。
)
echo.
pause >nul
