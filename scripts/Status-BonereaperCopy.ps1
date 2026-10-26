$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$pidFile=Join-Path $root 'data\bonereaper\worker.pid'
if (!(Test-Path $pidFile)) { Write-Output 'STOPPED (no pid file)'; exit }
$processId=[int](Get-Content $pidFile -Raw)
$process=Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
if (!$process) { Write-Output 'STOPPED (stale pid)'; exit }
if ($process.CommandLine -notmatch 'obsidian_prediction.bonereaper_worker') { throw 'PID identity mismatch' }
Write-Output "RUNNING PID $processId"
