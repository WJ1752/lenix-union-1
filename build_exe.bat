@echo off
chcp 65001 >nul
rem 构建 D2 查询站 exe —— 走 D2Query.spec（图片卡片要的 playwright、模式索引 modes.json、
rem 锻造来源索引 pattern_sources.json 都在 spec 里；早期那版手写命令行参数漏了这些，别再用）。
rem 首次构建需联网装依赖：.venv\Scripts\python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
cd /d %~dp0

rem 输出到 dist_build，别直接 --distpath dist：那会删掉 dist\D2Query\napcat_shell（NapCat 的登录配置）
.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist_build --workpath build D2Query.spec
if errorlevel 1 (
  echo.
  echo 构建失败，看上面的报错。
  pause
  exit /b 1
)

echo.
echo 构建完成：dist_build\D2Query\D2Query.exe
echo 部署到 dist\D2Query\ ：把 D2Query.exe 和 _internal 一起覆盖过去
echo   （_internal 用 cp -r 复制；robocopy /MIR 在中文路径下会卡住）
echo 外置模块还要单独同步：nonebot_plugins\destiny2.py、card_render.py、bot_cards.py
echo 最后把 .env 放在 exe 同目录，双击运行。
pause
