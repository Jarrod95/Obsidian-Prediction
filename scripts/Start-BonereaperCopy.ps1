param([switch]$Once)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$envFile = Join-Path $root 'bonereaper.env'
if (!(Test-Path $python)) { throw 'Install package into .venv first.' }
if (!(Test-Path $envFile)) { throw 'Copy bonereaper.env.example to bonereaper.env and configure.' }
Get-Content $envFile | ForEach-Object {
  $s = $_.Trim()
  if ($s -and !$s.StartsWith('#')) {
    $parts = $s.Split('=',2)
    if ($parts.Count -eq 2) { [Environment]::SetEnvironmentVariable($parts[0].Trim(),$parts[1].Trim(),'Process') }
  }
}
$runtime = Join-Path $root 'data\bonereaper'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$pidFile = Join-Path $runtime 'worker.pid'
if (Test-Path $pidFile) {
  $savedPid = [int](Get-Content $pidFile -Raw)
  $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
  if ($existing -and $existing.CommandLine -match 'obsidian_prediction.bonereaper_worker') {
    throw "Bonereaper already running with PID $savedPid"
  }
  Remove-Item $pidFile -Force
}
if ($Once) {
  & $python -m obsidian_prediction.bonereaper_worker run --once
  exit $LASTEXITCODE
}
$process = Start-Process -FilePath $python -ArgumentList @('-m','obsidian_prediction.bonereaper_worker','run') -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'stdout.log') -RedirectStandardError (Join-Path $runtime 'stderr.log')
Set-Content -Path $pidFile -Value $process.Id
Write-Output "Started Bonereaper paper worker PID $($process.Id)."
