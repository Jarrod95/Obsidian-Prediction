param([switch]$Once)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (!(Test-Path ".venv\Scripts\python.exe")) { throw "Missing .venv. Run: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ." }
if (!(Test-Path "copy-paper.env")) { throw "Missing copy-paper.env. Copy copy-paper.env.example and fill in the values." }
Get-Content "copy-paper.env" | ForEach-Object {
  $line = $_.Trim()
  if ($line -and !$line.StartsWith("#")) {
    $parts = $line.Split("=",2)
    if ($parts.Count -eq 2) { [Environment]::SetEnvironmentVariable($parts[0].Trim(),$parts[1].Trim(),"Process") }
  }
}
if ($Once) { & ".venv\Scripts\python.exe" -m obsidian_prediction.copy_paper --once }
else { & ".venv\Scripts\python.exe" -m obsidian_prediction.copy_paper }
if ($LASTEXITCODE -ne 0) { throw "Copy-paper exited with code $LASTEXITCODE" }
