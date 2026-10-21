# Obsidian-Prediction Telegram (Phase A foundation)

Read-only monitoring service. It is **not** an execution or worker-control API. The strategy's existing settlement notification outbox remains independent of the command polling service. Do not run two pollers with the same Telegram token.

## Setup (Windows PowerShell)

```powershell
git pull --ff-only origin main
python -m pip install -e ".[telegram,dev]"
```

In the root `.env` (do **not** commit credentials):

```dotenv
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_USER_ID=<your numeric Telegram user ID>
TELEGRAM_CHAT_ID=<your private-chat numeric chat ID>
```

Both numeric IDs must be configured, and only private-chat commands from that exact user/chat pair are accepted. No wildcard allowed. Use a different Telegram token for each polling service. The command service needs no exchange private keys.

Run after checkout of this PR branch, from repository root:

```powershell
python -m pytest tests/test_prediction_telegram.py -q
python -m obsidian_prediction.prediction_telegram
```

The process is foreground; Ctrl+C exits. For deployment, supervise it independently from `scripts/platform.ps1` and configure only one polling instance. Do not restart the strategy to deploy this service. To stop the Telegram process, stop its dedicated supervisor/service. To roll back, stop the new poller and restart the previous Telegram deployment at its recorded SHA. No migrations or trading DB writes are performed by this service.

## Commands implemented in this first PR

`/start`, `/help`, `/status`, `/bots`, `/health [bot]`, `/db`, `/version`, `/account [bot]`, `/positions [bot]`, `/position <basket_id>`, `/trades [bot] [count]`, `/pnl [bot]`, `/today`, `/yesterday`, `/week`, `/month`, `/settlements [count]`, `/unsettled`, `/leaderboard [paper|live|all] [today|week|month|all]`, `/signals [bot] [count]`, `/rejections [bot]`.

This is an **initial reporting implementation**, not completion of the full attached implementation plan. The command help lists only registered handlers. Unknown bot names are rejected. Current baskets and settlements do not have proven immutable bot attribution; accordingly per-bot balances, P/L and ranks show NOT AVAILABLE rather than fabricated returns. Shared paper-account P/L may be displayed separately. LIVE displays NOT CONFIGURED. The platform does not yet record trustworthy process ownership/scan heartbeats, so health says PARTIAL/UNKNOWN. Neither Telegram responses nor SQLite queries perform settlement or market writes.

## Remaining required work before production readiness

- Add verified per-bot attribution and accounting migration; independently allocated virtual accounts if percentage return ranking is desired.
- Instrument worker heartbeats, actual PID ownership, sanitized incidents, a persistent deny/allow audit log, and structured scan runs. **Denied commands are rejected but not yet persistently audited.**
- Add explicit per-bot settlement evidence coverage and valid low-sample/eligible leaderboard ranking once denominators exist.
- Add real file-allowlisted sanitized /logs, /errors, /uptime and deployed SHA with signed deployment metadata.
- Add bounded SQL pagination and query timeouts as database size increases; current historical aggregation samples at most 10,000 recent settlements.
- Integrate outbox delivery diagnostics, exception/partial status and error recovery. Existing notification sender is unchanged.
- Add Phase B market/risk reports and Phase C verified analytics in separate PRs.
- Run Windows VPS smoke checks and verify command responses using a private allowlisted Telegram chat prior to deployment.

## Research and accounting caveats

Paper YES/NO fills are snapshot simulated, not actual executable fills. Equity is cash plus original cost of open baskets rather than marked NAV. Fees may be incompletely recorded; no guaranteed edge or live P/L may be inferred. All dates from settlement records are UTC and filtered into Australia/Sydney day boundaries for period reporting.

## Release status

2026-10-22 — v0.1 Telegram foundation proposed for review (not deployed). No storage migration; live trading remains disabled.
