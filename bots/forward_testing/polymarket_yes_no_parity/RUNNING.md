# Running the parity paper bot

## Why the bot was hard to find
The original PR put executable logic in `src/obsidian_prediction/parity_paper.py`, while the bot folder contained only documentation. This PR adds `bot.py` to the expected forward-testing folder. Shared code remains in the installable package.

## Local installation
```powershell
git fetch origin
git switch feat/parity-expiry-priority-bot-layout
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
```

## Fast, near-closing scan
```powershell
.\.venv\Scripts\python.exe bots/forward_testing/polymarket_yes_no_parity/bot.py run --mode paper --discovery-limit 2000 --max-markets 500 --pages 30 --closing-within-hours 48 --strict-closing --concurrency 16 --interval 30
```
FX now defaults to Frankfurter's free, no-key USD/AUD daily reference rate, cached for an hour. The observed date and source are printed each cycle. This is **not an intraday executable FX quote**. The scanner skips a cycle if FX is unavailable or older than 7 days. For research only, a manual `--fx-aud-per-usd` override remains available. Strict closing means the bot scans only eligible markets whose advertised close time is within the next 48 hours. Remove `--strict-closing` to fill the remaining scan slots with other markets. Gamma pagination may limit how many markets are discovered; increasing `--pages` does not guarantee 2000 markets. Closing is not the same as final resolution.

## Inspect the local database
```powershell
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper report
.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper export
```
Stop with Ctrl+C. The database defaults to `runtime/parity_paper.sqlite3` relative to the working directory.

## Windows server
Install dependencies in a venv, use the same bot entrypoint, pass an absolute --db path and configure one Task Scheduler/NSSM instance with restart-on-failure. No mandatory multi-day wait for simulated mode.

## Notebook versus observed signals
The research notebook compares YES/NO ask-depth, expected payout and hypothetical edge. The running bot records actual public snapshots and optimistic simulated entries; expected edge is not realized P/L. Use exported `signals.csv` to compare market IDs, quotes, depth, basket cost and expected return. No authoritative settlement/realized-P&L engine exists yet.

## Version history
- v0.1.0: public-data polling and optimistic virtual entries.
- v0.2.0: near-closing priority, configurable discovery horizon and bot-folder executable.

## Remaining implementation
Verified FX, venue fees, synchronized book timestamps, realistic paired execution, settlement and realized P/L, idempotent reconciliation, structured monitoring.

## FX source (v0.2.1)
Default: https://api.frankfurter.dev/v2/rate/USD/AUD (free, no API key). Rates are daily reference mid-rates, not tick-level FX. Cached for 3600 seconds, maximum observation age 7 calendar days; failure skips the scan rather than using a fabricated rate. `--fx-refresh-seconds` and `--fx-max-age-days` customize the policy. The FX rate is stored on each signal; source/date appear in console output. A future migration should store FX source/date in dedicated database fields.
