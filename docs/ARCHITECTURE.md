# Architecture status — 2026-10-21

The current `cli.py` uses root `.env` configuration and `PublicPolymarket` for read-only public market discovery, raw JSONL persistence and book retrieval. `markets.py` offers isolated Decimal two-leg executable-depth parity math. Bot registry display is read-only. No live or paper order submission exists.

## Parent Obsidian observed state

Inspected `Jarrod95/Obsidian` default-branch tree and latest PR summaries. The repository has many forward-testing strategies, PowerShell Start/Stop launchers, reliability GitHub Actions, Telegram commands for positions/orders/P&L/leaderboards (PRs #33–34) and parallel-startup/dotenv fixes (PR #32). Older architecture documents describing these as placeholders must not be trusted without fresh source review.

## Forward contracts

Future strategy emits intent -> central risk -> simulated venue -> durable order/fill/settlement ledger; expose versioned read-only API for parent dashboard; keep exchange integrations and process failure isolated. Supervisor, migrations, FastAPI, dashboards, and tele­gram are not yet implemented.
