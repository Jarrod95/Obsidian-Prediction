$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root 'data\bonereaper\worker.pid'
if (!(Test-Path $pidFile)) { Write-Output 'No Bonereaper PID file.'; exit 0 }
$savedPid = [int](Get-Content $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
if (!$process) { Remove-Item $pidFile; Write-Output 'Stale PID cleared.'; exit 0 }
if ($process.CommandLine -notmatch 'obsidian_prediction.bonereaper_worker') {
  throw 'PID identity mismatch. No process killed.'
}
# Stop-Process is forceful on Windows. WAL recovery must handle abrupt termination.
Stop-Process -Id $savedPid -ErrorAction Stop
Remove-Item $pidFile -Force
Write-Output "Stopped Bonereaper PID $savedPid."
