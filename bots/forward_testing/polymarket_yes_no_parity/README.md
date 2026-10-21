# Polymarket YES/NO parity bot — v0.1.0

## Purpose
Local and Windows VPS **public-data shadow-paper** bot. It cannot send real orders. This is an operational prototype with **optimistic snapshot fills**, not a conservative executable-fill simulator. It deliberately leaves trades OPEN until verified resolution support is implemented. Do not treat expected P/L as realized.

## Installation
From repository root in PowerShell:
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper doctor
```

## Run locally
Supply an independently checked **AUD per USD** rate. Example 1.49 is illustrative only, NOT a current rate.
```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper run --mode observe --once --fx-aud-per-usd 1.49
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper run --mode paper --fx-aud-per-usd 1.49 --interval 30
```
Stop with Ctrl+C. Database: `runtime/parity_paper.sqlite3`. To see signals and open baskets:
```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper report
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper export
```

## Server
On Windows VPS, clone and install the same package, set a persistent absolute --db path and run under Task Scheduler or NSSM with restart-on-failure and one instance only. Example:
```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper run --mode paper --fx-aud-per-usd 1.49 --db C:\ObsidianPrediction\runtime\parity.sqlite3 --interval 30
```
The hardcoded example FX rate is NOT appropriate for reliable unattended AUD accounting. Replace with a timestamped FX feed before relying on AUD results.

## Notebook comparison
The supplied `polymarket_yes_no_parity_research.ipynb` researches Gamma market listings, CLOB YES/NO ask depth, synthetic parity examples, cost sensitivity, plots and CSV candidate exports. Compare market/token IDs, both ask ladders, executable paired quantity, fees, expected net USD edge and FX-converted AUD edge. The notebook's synthetic examples must not be compared as if they were actual venue fills. CSV export of `signals` includes captured books and estimated economics. `baskets` tracks optimistic virtual entries.

## Data and assumptions
- Virtual capital AUD 5,000; maximum combined basket AUD 100; five open baskets; one per market.
- Binary YES/NO only; raw token mapping checked.
- Price math uses Decimal; venue asks are read from public CLOB.
- Fee rate is configurable and defaults to zero; this is an assumption, not verified venue fee treatment.
- Snapshot receipt time is used for freshness; this does not verify source timestamp or synchronized books.
- Signal snapshots and entries are persisted in SQLite WAL.
- No settlement polling, real FX provider, actual execution latency, partial fills, early exits, realized P/L, migrations or reconciliation yet.
- No authenticated trading endpoints, credentials, wallets or signing code.

## Version history
- 0.1.0 (2026-10-21): standalone public market polling, token validation, depth-budget sizing, signal recording, optimistic virtual basket entries, SQLite, reporting/export, CLI and documentation.

## Roadmap
1. Verified timestamped FX feed and venue fees.
2. Conservative post-latency paired fills and partial-fill treatment.
3. Authoritative resolution and immutable cashflow ledger with realized USD/AUD P/L.
4. Idempotent restart and reconciliation.
5. Extended CSV/Parquet reporting and read-only dashboard API.
6. Windows service wrapper, structured logs and operational diagnostics.

This is a prototype PR, not a claim of complete fulfillment of the original detailed specification.
