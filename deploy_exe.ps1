# Deploy dist_build\D2Query\ to dist\D2Query\ and restart the exe.
#
# Why a script: dist\D2Query\D2Query.exe is locked while running, and
# dist\D2Query\ also holds data that must NOT be deleted (napcat_shell login
# config, .env, user_bindings.json, pvp_weapon_cache.json) - so no robocopy /MIR
# (README notes /MIR hangs on these paths).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File deploy_exe.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$src  = Join-Path $root "dist_build\D2Query"
$dst  = Join-Path $root "dist\D2Query"

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

# 2) overwrite exe + _internal (delete old _internal first so no stale modules remain)
Copy-Item (Join-Path $src "D2Query.exe") (Join-Path $dst "D2Query.exe") -Force
$dstInt = Join-Path $dst "_internal"
if (Test-Path $dstInt) { Remove-Item $dstInt -Recurse -Force }
Copy-Item (Join-Path $src "_internal") $dstInt -Recurse -Force

# 3) sync external modules (these live outside the exe)
$ext = @("bot_cards.py", "card_render.py", "weapon_filter.py", "nonebot_plugins\destiny2.py")
foreach ($f in $ext) {
  $s = Join-Path $root $f
  $d = Join-Path $dst  $f
  if (Test-Path $s) { Copy-Item $s $d -Force; Write-Output "sync $f" }
}

# 3.5) sync the bundled DIM build (dim_app lives outside the exe, ~100MB static site)
$dimSrc = Join-Path $root "dim_app"
$dimDst = Join-Path $dst "dim_app"
if (Test-Path (Join-Path $dimSrc "index.html")) {
  if (-not (Test-Path $dimDst)) { New-Item -ItemType Directory -Path $dimDst | Out-Null }
  Copy-Item (Join-Path $dimSrc "*") $dimDst -Recurse -Force
  Write-Output "sync dim_app"
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
