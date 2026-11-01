# Prediction worker control. Python owns locks, identity checks and JSON output.
# Windows PowerShell 5.1-compatible. Does not open a browser or require Telegram.
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("start","stop","restart","status","logs","plan","doctor")]
    [string]$Action = "status",
    [string]$Bot = "all",
    [int]$Tail = 50,
    [switch]$Json
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing Python 3.12 virtualenv: $Python. Run py -3.12 -m venv .venv, then pip install -e '.[dev]'"
}
Push-Location $Root
try {
    if ($Action -eq "doctor") {
        & $Python -m obsidian_prediction.cli doctor
    } else {
        # Argument array avoids interpolation / shell command execution.
        $arguments = @("-m", "obsidian_prediction.fleet_lifecycle",
                       $Action, "--bot", $Bot, "--tail", "$Tail")
        & $Python @arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Prediction lifecycle failed, exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
