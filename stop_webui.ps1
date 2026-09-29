# 停掉本地 D2 查询站（webui.py）占用的 8900 / 8901 端口
# 用途：跑打包版 dist\D2Query\D2Query.exe 前必须先停掉源码服务，
#      否则 nonebot 驱动绑 8901 会失败（WinError 10048）。
#
# 用法： powershell -NoProfile -ExecutionPolicy Bypass -File stop_webui.ps1
$found = $false

Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like '*webui.py*' } |
  ForEach-Object {
    $script:found = $true
    Write-Output ("kill python webui.py pid=" + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

# 兜底：进程没匹配上命令行的，按端口反查（只处理 LISTENING）
foreach ($port in 8900, 8901) {
  $pids = netstat -ano | Select-String -Pattern ":$port\s.*LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
  foreach ($p in $pids) {
    if ($p -and $p -ne '0') {
      $script:found = $true
      Write-Output ("kill listener on $port pid=$p")
      Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
  }
}

Start-Sleep -Milliseconds 800
$still = netstat -ano | Select-String -Pattern ":(8900|8901)\s.*LISTENING"
if ($still) {
  Write-Output "警告：端口仍被占用："
  $still
} else {
  if ($found) { Write-Output "已停止，8900 / 8901 均已释放" }
  else { Write-Output "没有在运行的服务，8900 / 8901 本来就空闲" }
}
