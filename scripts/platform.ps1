param(
    [Parameter(Position=0)]
    [ValidateSet("start","stop","restart","status","logs")]
    [string]$Action = "status",
    [string]$Bot = "all",
    [int]$Tail = 50
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Registry = Join-Path $Root "bots\registry.json"
$Runtime = Join-Path $Root "runtime\supervisor"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

if (!(Test-Path $Python)) { throw "Python venv missing: $Python. Run: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'" }
$Entries = @(Get-Content $Registry -Raw | ConvertFrom-Json)
function Targets {
    if ($Bot -eq "all") { return @($Entries | Where-Object { $_.enabled -ne $false }) }
    $matches = @($Entries | Where-Object { $_.id -eq $Bot })
    if (!$matches.Count) { throw "Unknown bot '$Bot'. Check bots/registry.json" }
    return $matches
}
function PidFile($entry) { Join-Path $Runtime "$($entry.id).json" }
function ProcessInfo($entry) {
    $path = PidFile $entry
    if (!(Test-Path $path)) { return $null }
    try {
        $record = Get-Content $path -Raw | ConvertFrom-Json
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
        if (!$proc) { return $null }
        if ($proc.CreationDate -and $record.started) {
            $started = [datetime]$record.started
            $actual = [datetime]$proc.CreationDate
            if ([math]::Abs(($actual.ToUniversalTime() - $started.ToUniversalTime()).TotalSeconds) -gt 10) { return $null }
        }
        if ($proc.ExecutablePath -and [IO.Path]::GetFullPath($proc.ExecutablePath) -ine [IO.Path]::GetFullPath($Python)) { return $null }
        return $proc
    } catch { return $null }
}
function StartBot($entry) {
    $existing = ProcessInfo $entry
    if ($existing) { Write-Host "$($entry.id): already running PID $($existing.ProcessId)"; return }
    $module = [string]$entry.module
    if (!$module -or $module -notmatch '^[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)*$') { throw "Invalid module for $($entry.id)" }
    $args = @("-m", $module) + @($entry.args | ForEach-Object { [string]$_ })
    $stdout = Join-Path $Runtime "$($entry.id).stdout.log"
    $stderr = Join-Path $Runtime "$($entry.id).stderr.log"
    $proc = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $proc.Refresh()
    $started = (Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)").CreationDate
    @{ pid=$proc.Id; started=([datetime]$started).ToUniversalTime().ToString("o"); module=$module } |
        ConvertTo-Json | Set-Content -Encoding UTF8 (PidFile $entry)
    Write-Host "$($entry.id): started PID $($proc.Id) (logs: runtime/supervisor/)"
}
function StopBot($entry) {
    $proc = ProcessInfo $entry
    if (!$proc) { Write-Host "$($entry.id): not running"; Remove-Item (PidFile $entry) -ErrorAction SilentlyContinue; return }
    Stop-Process -Id $proc.ProcessId -ErrorAction Stop
    Start-Sleep -Milliseconds 300
    Remove-Item (PidFile $entry) -ErrorAction SilentlyContinue
    Write-Host "$($entry.id): stopped"
}
foreach ($entry in (Targets)) {
    switch ($Action) {
        "start" { StartBot $entry }
        "stop" { StopBot $entry }
        "restart" { StopBot $entry; StartBot $entry }
        "status" {
            $proc = ProcessInfo $entry
            if ($proc) { Write-Host "$($entry.id): RUNNING PID $($proc.ProcessId)" }
            else { Write-Host "$($entry.id): STOPPED" }
        }
        "logs" {
            $path = Join-Path $Runtime "$($entry.id).stdout.log"
            if (Test-Path $path) { Get-Content $path -Tail $Tail }
            $errors = Join-Path $Runtime "$($entry.id).stderr.log"
            if (Test-Path $errors) { Get-Content $errors -Tail $Tail }
        }
    }
}
