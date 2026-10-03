# Deploy dist_build\D2Query\ to dist\D2Query\ and restart the exe.
#
# Why a script: dist\D2Query\D2Query.exe is locked while running, and
# dist\D2Query\ also holds data that must NOT be deleted (napcat_shell login
# config, .env, user_bindings.json, pvp_weapon_cache.json) - so no robocopy /MIR
# (README notes /MIR hangs on these paths).
#
# 注意：本文件必须存成 **UTF-8 with BOM**。Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 GBK 解码，
# 字符串里的中文一旦解码错位就会把收尾引号吞掉，直接报「字符串缺少终止符」。
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File deploy_exe.ps1
#        默认部署到 dist_new\D2Query（2026-10-03 起**只允许工作区内目录**，
#        工作区外的落点已按用户要求废弃删除，不要再往 F:\D2Query 之类的地方部署）
param(
  [string]$Dst = "dist_new\D2Query"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$src  = Join-Path $root "dist_build\D2Query"
$dst  = Join-Path $root $Dst

if (-not (Test-Path (Join-Path $src "D2Query.exe"))) {
  Write-Output "missing $src\D2Query.exe - run build_exe.bat first"
  exit 1
}

# 1) stop the running exe (it holds a lock on the file we must overwrite)
Get-Process D2Query -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Output ("stop D2Query.exe pid=" + $_.Id)
  Stop-Process -Id $_.Id -Force
}
Start-Sleep -Milliseconds 800

# 2) overwrite exe + _internal.
#    exe 还在跑就一定覆盖失败，所以第 1 步先把 D2Query 停掉（不影响内嵌 NapCat/QQ，
#    它们是独立进程，新实例起来后自己重连，不用重新扫码）。
#    _internal **绝不能"先删再复制"**：内嵌 NapCat(NapCatWinBootMain.exe) 继承了本进程的
#    DLL 搜索路径、把 _internal 里的 VCRUNTIME140.dll 和各 C 扩展 .pyd 等 17 个文件锁着，
#    Remove-Item 会**删掉大部分文件之后**才报错中断，把安装掏空成"新 exe + 残骸 _internal"
#    （2026-10-02 真踩过：469 个里被删掉 452 个）。改成合并覆盖：逐文件比哈希，只覆盖不同的；
#    锁住的那几个在同工具链构建之间逐字节相同，跳过是安全的。
$dstInt = Join-Path $dst "_internal"
$srcInt = Join-Path $src "_internal"
Copy-Item (Join-Path $src "D2Query.exe") (Join-Path $dst "D2Query.exe") -Force
New-Item -ItemType Directory -Path $dstInt -Force | Out-Null
$skipped = 0
Get-ChildItem $srcInt -Recurse -File | ForEach-Object {
  $rel = $_.FullName.Substring($srcInt.Length + 1)
  $tgt = Join-Path $dstInt $rel
  $dir = Split-Path $tgt -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if ((Test-Path $tgt) -and ((Get-FileHash $_.FullName).Hash -eq (Get-FileHash $tgt).Hash)) { return }
  try { Copy-Item $_.FullName $tgt -Force }
  catch { $skipped++; Write-Output ("  _internal 已跳过（被占用，需逐字节相同才安全）: " + $rel) }
}
if ($skipped -gt 0) { Write-Output ("_internal 有 $skipped 个文件被占用未覆盖，请核对是否只能是被 NapCat 锁住的那 17 个") }

# 3) sync external modules.
#    注意：bot_cards/card_render/bot_platform 等在 spec hiddenimports 里，已被打进 exe
#    归档，运行时 FrozenImporter 优先 —— 这里同步的外置副本**不会生效**，只是留档；
#    改这些模块必须 build_exe.bat 重打包（2026-10-03 实证）。
$ext = @("bot_cards.py", "bot_platform.py", "bot_fireteam.py", "card_render.py", "weapon_filter.py", "weapon_usage.py",
         "raid_loot.py", "nonebot_plugins\destiny2.py")
foreach ($f in $ext) {
  $s = Join-Path $root $f
  $d = Join-Path $dst  $f
  if (Test-Path $s) { Copy-Item $s $d -Force; Write-Output "sync $f" }
}

# 3.6) sync NapCat runtime files into dist's napcat_shell (that dir is created once by
# setup_napcat.py and never rebuilt - if napcat.mjs gets updated there without its
# rollup chunk (conout-*.js), the loader fails silently and the QR never appears)
$napSrc = Join-Path $root "napcat_shell"
$napDst = Join-Path $dst "napcat_shell"
if (Test-Path (Join-Path $napSrc "napcat.mjs")) {
  foreach ($f in @("napcat.mjs") + (Get-ChildItem $napSrc -Filter "conout-*.js" -Name)) {
    Copy-Item (Join-Path $napSrc $f) (Join-Path $napDst $f) -Force
    Write-Output "sync napcat_shell/$f"
  }
}

Write-Output "deployed. launch: $dst\D2Query.exe (cwd must be $dst)"
