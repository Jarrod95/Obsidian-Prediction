$ErrorActionPreference = 'Stop'
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
& .\.venv\Scripts\python.exe -m obsidian_prediction.cli doctor
