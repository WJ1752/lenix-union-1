@echo off
chcp 65001 >nul
rem ==================================================================
rem  start_edge_debug.bat —— 以远程调试端口启动专用 Edge（武器使用率 CDP 通道）
rem
rem  双击运行：先关闭当前 Edge，再用独立调试配置目录带端口 9222 重新启动。
rem    - 会关闭当前 Edge 再带调试端口重开（taskkill /f）
rem    - 用独立 user-data-dir（F:\edge_debug_profile）：新版 Edge 对【默认】
rem      配置目录会忽略 --remote-debugging-port，必须独立目录端口才生效
rem    - cookies/登录态已从默认配置复制到该调试目录，cf_clearance 有效，
rem      light.gg 的 Cloudflare 人机验证状态保留，无需重新验证
rem    - --restore-last-session 会恢复上次会话的标签页
rem
rem  重启后武器使用率管线（weapon_usage.py 的 CDP 提供者）会自动通过
rem  127.0.0.1:9222 借用这个已验证的浏览器会话抓 light.gg 数据。
rem  注意：之后别把这个 Edge 全关了，关了 CDP 通道就断。
rem ==================================================================
title Edge 调试模式启动器（light.gg 已验证会话复用）

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=msedge.exe"
set "EDGE_PROFILE=F:\edge_debug_profile"

echo.
echo  [1/2] 正在关闭 Edge……
echo        （独立调试 profile：cookies/登录态/light.gg 验证全部保留）
taskkill /f /im msedge.exe >nul 2>&1
timeout /t 1 /nobreak >nul

echo  [2/2] 正在以调试模式重启 Edge（端口 9222，profile=%EDGE_PROFILE%）……
start "" "%EDGE%" --user-data-dir="%EDGE_PROFILE%" --remote-debugging-port=9222 --restore-last-session
timeout /t 4 /nobreak >nul

curl -s --max-time 3 http://127.0.0.1:9222/json/version | findstr /i "webSocketDebuggerUrl" >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo  √ 成功！Edge 已带调试端口运行。看到本提示后可直接关闭本窗口。
) else (
    echo.
    echo  × 端口未就绪，可能 Edge 还没启动完。稍等几秒后刷新再试，或重新双击本脚本。
)
echo.
pause >nul
