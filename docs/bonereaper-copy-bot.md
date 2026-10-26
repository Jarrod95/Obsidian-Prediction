# Bonereaper BTC 5m/15m copy worker — cautious paper implementation

**NOT DEPLOYMENT CERTIFIED.** Read the release status before starting. No real-money functionality exists.

## Scope and isolation

`obsidian_prediction.bonereaper_worker` is a separate worker. The original `copy_paper.py`, `Start-CopyPaper.ps1`, its historical SQLite database, and the parity bot remain unchanged. The new worker has its own `data/bonereaper/paper.sqlite`. Do not point both bots at one database.

Source handle: https://polymarket.com/@bonereaper. Third-party public profile sources associate it with `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`, **but attribution against Polymarket's official profile mapping has not been independently verified by this implementation**. Keep `BONEREAPER_SOURCE_VERIFIED=NO` until an operator compares the full wallet in Polymarket profile data with the public activity feed. The worker refuses to start without an explicit affirmative operator signoff.

The Data API `/v2/activity` uses a `{data,pagination}` envelope, an opaque `next_cursor`, and keyset pages. Each poll overlaps prior timestamp by 120 seconds and writes event dispositions idempotently. It fails closed on schema mismatch or 20-page cap. If the 20-page cap is reached, do **not** advance the watermark.

## Windows local setup (once deployment release gates are met)

```powershell
git checkout main
git pull --ff-only origin main
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item bonereaper.env.example bonereaper.env
notepad bonereaper.env
```

Set `BONEREAPER_AUDUSD` to a checked, positive AUD/USD rate and `BONEREAPER_FX_AT` to the quote's offset-aware UTC ISO-8601 timestamp (for example `2026-10-26T06:00:00Z` **only if it reflects a real independently checked quote**, never copy the example date blindly). Stale FX over 24 hours is rejected. Mark source verified only when identity verification is completed. Default mode `observe` records no fills; `paper` simulates ledger movements.

```powershell
.\scripts\Start-BonereaperCopy.ps1 -Once
.\scripts\Start-BonereaperCopy.ps1
.\scripts\Status-BonereaperCopy.ps1
.\scripts\Stop-BonereaperCopy.ps1
.\scripts\Restart-BonereaperCopy.ps1
```

Run the CLI with the same environment variables (for example from a shell sourcing the env file):

```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker status
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker events
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker positions
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker fills
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker pause
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker resume
.\.venv\Scripts\python.exe -m obsidian_prediction.bonereaper_worker doctor
```

Operational limitations: detached process does **not** auto-restart on crash or after an OS reboot. The stop script terminates the verified process; SQLite WAL durability should preserve complete transactions but the process does not implement a Windows control signal shutdown. PID identity is checked but an OS-wide writer lock is not yet implemented. There is no scheduled task. The scripts are not validated against an actual Windows VPS. Do not rely on them until the lifecycle acceptance gate is tested.

## Model and accounting

Baseline is AUD 500 converted to a USDC-assumed ledger using configured AUD/USD; USD and USDC are distinct and the 1:1 execution assumption is not a verified exchange conversion. BUY expenses (including fees) become full position basis; partial SELL allocates proportional basis and credits net-of-fee proceeds; settlement credits once if Gamma has a unique binary 1/0 price vector and closed state. The local position basis is durable across restart. Equity based on current bids is **not** available: status deliberately labels NAV unmarked instead of reporting stale holdings as current.

Entries are bounded by 5% of initial capital, 20% deployed initial capital, and 3 distinct markets; event max age is 10s on 5m, 20s on 15m; min remaining 60s/120s. Trades with source price deterioration above 3 percentage points are rejected. Market classifier requires strict BTC slug, condition ID, token/outcome match, active/closed fields and expiration agreement. Quotes without a timestamp no older than 3s, validated decimal depth and tick alignment are rejected.

**Limitations requiring further engineering:** daily drawdown stop, exact-once canonical v2 trade IDs, authoritative resolution confirmation beyond final Gamma price vectors, split source fill allocation, per-outcome direction limits, market fees dynamically validated, source-size scaling, burst load testing, mature telemetry, Telegram and dashboard integration, automated incident dedup, export/replay tooling, reliable Windows service supervision, backups and reversible schema migrations. This is not a complete implementation of the uploaded W01–W12 specification.

## Testing

Run locally without GitHub Actions:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m obsidian_prediction.cli doctor
```

Tests must be executed on the actual current branch and exact code revision. Record dates, pass counts, OS version, and diagnostic output. Do not merge on assumed success.

## Rollback

Stop only this Bonereaper process, back up `data/bonereaper/paper.sqlite` with SQLite backup tooling (not a blind copy while the database is live), and revert the Bonereaper-only feature commit(s). Leave original `copy-paper.env`, existing copy bot databases, and parity registry intact. No existing data migration has been performed.
