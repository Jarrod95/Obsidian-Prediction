# Prediction PR A — Lifecycle deployment and rollback

## Scope and safety

This deployment is **paper/observe only**. It does not add broker authentication,
wallet signing, order placement or live trading. Existing SQLite paper ledgers
remain under `runtime/` and are not migrated or deleted.

The canonical inventory is `bots/registry.json`. Only entries in this file
can be launched. The registry now includes the existing parity runner, existing bigggggggg copy
worker, merged Bonereaper copy worker, and an independently managed optional
Telegram service. Copy worker modules use their actual CLI contracts; no
unsupported `--mode` argument is passed to them. All are paper/observe-only.

## Install — Windows PowerShell 5.1, Python 3.12

```powershell
git pull --ff-only origin main
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\platform.ps1 doctor
.\scripts\platform.ps1 plan
.\scripts\platform.ps1 start
.\scripts\platform.ps1 status
```

The PowerShell entrypoint calls the Python owner; it does not directly launch
processes. `-Bot <registry-id>` scopes operations. `logs -Bot <id>` prints
run-scoped stdout and stderr; filenames include a unique run ID.

## Persistent recovery controller

The optional controller can run independently of the invoking shell. Use a
Windows Task Scheduler task with a *dedicated service account*, "At startup"
trigger, "Run whether user is logged on or not", working directory set to the
repository root, and an action:

```powershell
C:\path\to\repo\.venv\Scripts\python.exe -m obsidian_prediction.fleet_controller run --interval 30
```

Create **only one** controller task for this repository. The worker launch
locks prevent concurrent duplicate owned launches, but a second monitor is
still unnecessary. The controller honors persistent intentional operator stop
state and does not restart that worker until an explicit `start` is issued.
Quarantine prevents unlimited crash-loop restarts:

```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.fleet_controller reset --bot polymarket_yes_no_parity
.\scripts\platform.ps1 start -Bot polymarket_yes_no_parity
```

Restart history and current controller snapshot are in
`runtime/supervisor/<id>.restarts.json` and `controller.json`.
Ownership records contain process PID, creation epoch, interpreter path,
module/argument digest, run ID, release SHA and controller PID.
Do not treat an unverified PID record as proof of a live worker.

## VPS smoke checks (NOT YET VERIFIED)

Run with no tokens or credentials as an initial offline smoke test.
Then enable external public data access for the read-only parity worker.

```powershell
$PSVersionTable.PSVersion
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\platform.ps1 doctor
.\scripts\platform.ps1 plan
.\scripts\platform.ps1 start
.\scripts\platform.ps1 start
.\scripts\platform.ps1 status
Get-ChildItem .\runtime\supervisor\*.json
.\scripts\platform.ps1 logs -Bot polymarket_yes_no_parity
.\scripts\platform.ps1 stop -Bot polymarket_yes_no_parity
.\scripts\platform.ps1 status
```

Confirm duplicate `start` did not create a second process; after an intentional
`stop`, confirm controller does not respawn the worker; verify a deliberately
crashing fake worker is quarantined rather than respawned indefinitely; verify
terminal closure and VPS reboot recovery from the scheduler. These are manual
checks, not claims of verification.

## Rollback

1. Disable/delete the new Task Scheduler controller task.
2. Stop new workers with `platform.ps1 stop`, verifying the owned trees have exited.
3. Checkout the preceding release commit or revert this PR.
4. Reinstall the preceding dependency set in the existing virtualenv.
5. Use the previous manager only after the new owned workers are stopped.
6. **Preserve all files in `runtime/`**, especially `*.sqlite3`, WAL and SHM
   files; do not reset the database or delete strategy research data.

## Verification limitations and remaining acceptance work

- No Windows 5.1 syntax/mock process test has been run in the present environment.
- Full Windows shutdown must verify graceful behavior and orphan child handling.
- Exact Python `-m` unmanaged instance discovery is implemented; verify it on Windows against alternate launch mechanisms.
- Telegram is registered as an independently owned optional service. Verify it against existing unmanaged pollers and with intentionally absent credentials.
- Both merged copy workers are registered and publish scan heartbeats; verify their startup/health on the target VPS with safe paper/observe configuration.
- Controller boot setup requires a manual operator smoke test; no scheduler installer.
- Bootstrap persists exit codes and exception causes; Windows forced-kill exit-code visibility is limited and must be smoke-tested.
- Entire existing suite has not been executed in the target repository environment.

**Do not mark this PR merge-ready without running the offline suite and Windows VPS smoke checks, resolving any discovered failures.**


## Worker configuration (no secrets committed)

The registry is deliberately explicit about optional configuration:

| ID | Mode | Required environment | Expected result without config |
| --- | --- | --- | --- |
| `polymarket_yes_no_parity` | paper | none | started |
| `copy_bigggggggg` | paper | `COPY_WALLET`, `COPY_AUDUSD` | BLOCKED_CONFIG |
| `copy_bonereaper` | observe | `BONEREAPER_WALLET`, `BONEREAPER_AUDUSD`, `BONEREAPER_FX_AT`, `BONEREAPER_SOURCE_VERIFIED=YES` after independent attribution verification | BLOCKED_CONFIG |
| `prediction_telegram` | observe service | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `TELEGRAM_CHAT_ID` | BLOCKED_CONFIG |

Set credentials only in ignored local `.env` and verify Bonereaper source identity before setting YES. Config blockage on one service must not block the other workers.

## Offline verification commands — zero GitHub Actions usage

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pytest tests/test_fleet_registry.py tests/test_fleet_lifecycle.py tests/test_fleet_heartbeat.py tests/test_fleet_process_integration.py tests/test_platform_ps1_contract.py -q
.\.venv\Scripts\python.exe -m pytest -q
powershell -NoProfile -Command '$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\scripts\platform.ps1),[ref]$t,[ref]$e)|Out-Null; if($e.Count){$e|Out-String|Write-Error;exit 1}'
```
