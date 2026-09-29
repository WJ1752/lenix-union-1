foreach ($n in @('NapCatWinBootMain.exe', 'QQ.exe')) {
  Get-CimInstance Win32_Process -Filter "Name='$n'" | ForEach-Object {
    if ($n -eq 'QQ.exe') {
      $root = $_
      $qqIds = @(Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Select-Object -ExpandProperty ProcessId)
      while ($qqIds -contains $root.ParentProcessId) {
        $root = Get-CimInstance Win32_Process -Filter "ProcessId=$($root.ParentProcessId)"
      }
      $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($root.ParentProcessId)" -ErrorAction SilentlyContinue
      if ($par) { Write-Output ("keep user qq: " + $_.ProcessId) }
      else { Write-Output ("kill headless qq: " + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } else {
      Write-Output ("kill boot: " + $_.ProcessId)
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}
