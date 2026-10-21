# Prediction platform background runner (v0.3.0)

This Windows PowerShell manager launches **all enabled bots** from `bots/registry.json` as detached Python processes. The calling terminal returns immediately and remains available for other commands. Bots continue running in the background and log to files instead of the terminal.

## Setup
From repository root:
```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

## Commands
```powershell
.\scripts\platform.ps1 start
.\scripts\platform.ps1 status
.\scripts\platform.ps1 logs
.\scripts\platform.ps1 stop
.\scripts\platform.ps1 restart
```
To manage one bot:
```powershell
.\scripts\platform.ps1 status -Bot polymarket_yes_no_parity
.\scripts\platform.ps1 stop -Bot polymarket_yes_no_parity
.\scripts\platform.ps1 start -Bot polymarket_yes_no_parity
```

## Files
- `bots/registry.json`: enabled bots, Python module and CLI arguments.
- `runtime/supervisor/<bot>.json`: PID, process creation timestamp and module for safe lifecycle checks.
- `runtime/supervisor/<bot>.stdout.log` and `*.stderr.log`: detached output. `logs` displays last 50 lines without continuously following them.
- `runtime/parity_paper.sqlite3`: bot's SQLite research data.

## Adding a future bot
Add another object to `bots/registry.json`:
```json
{"id":"another_bot","enabled":true,"module":"obsidian_prediction.another_bot","args":["run","--mode","paper"]}
```
The manager will include it automatically in `start`, `stop`, `restart`, and `status`.

## Notes and limitations
- Designed for **Windows PowerShell and Windows VPS**; requires CIM/WMI and a local venv.
- This is a detached process launcher, **not an auto-restarting Windows Service**. Use Task Scheduler at login/startup or NSSM if automatic boot/recovery is required.
- Stop terminates the process; current parity bot does not implement graceful settlement/shutdown hooks. SQLite commits are transactional.
- Status uses PID, process creation time and executable path to avoid killing unrelated processes.
- Avoid running a second manual copy of a bot alongside the manager. The manager only tracks processes it launched.
- Stdout can be buffered by Python; logs may not update immediately.
- Start commands return a clean prompt; `logs` is opt-in.
- Running `start` repeatedly won't launch duplicate **tracked** processes.
- A machine restart ends the processes. Relaunch with `start`.
- If your PowerShell execution policy blocks local scripts, use `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\platform.ps1 start` for that invocation.

## Version history
- 0.3.0 (2026-10-21): registry-driven detached process manager with start, stop, restart, status and logs; registered first parity bot.
