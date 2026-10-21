# Parity paper account: settlement, compounding and Telegram

## Scope
This PR adds a **paper-only** SQL accounting engine to Obsidian-Prediction. No exchange orders are placed.

## Storage
Default SQLite file: `runtime/parity_paper.sqlite3` (WAL mode).
- `paper_accounts`: initial deposit, cash, realized P/L.
- `paper_ledger`: append-only initial deposit, entry debits, settlement credits.
- `paper_fills`: snapshot-simulated YES and NO legs (not evidence of executable fills).
- `paper_settlements`: market evidence, USD and AUD payout, realized USD/AUD P/L, return % and FX.
- `telegram_outbox`: durable notification queue with retry attempts and delivery status.
- Existing `signals`, `baskets`, `events` remain; baskets gain question and entry FX.

On first launch, pre-existing **open** baskets are imported as reserved funds at historical cost; historical already-closed baskets require manual reconciliation and startup refuses rather than guessing. Back up the SQLite file before first launch.

## Sizing and risk
- Initial fictional account: **A$5,000**.
- Target basket: **2% of current equity**, initially A$100.
- Concurrent slot ceiling: `min(50, max(1, floor(equity / 1000)))`, initially 5.
- Total reserved exposure: initially 10%, increases with slots, hard-capped at 30% of equity.
- Entries require available cash, exposure capacity, unused slot, unique market and sufficient simulated YES/NO liquidity. Basket sizing uses the latest account equity and is recalculated each scan.
- Example: A$7,500 -> target A$150, 7 slots, 14% aggregate exposure cap. A$10,000 -> A$200, 10 slots, 20% cap. A$20,000 -> A$400, 20 slots but 30% aggregate exposure cap means ~15 fully sized baskets maximum.
- Equity = available cash + original reserved cost of open baskets (cost basis, not mark-to-market). **Unrealized P/L is not included.**

## Resolution
Each scan cycle checks open baskets individually using the public Gamma market-by-ID endpoint, including markets no longer returned in active listings.
Only records settlement when Gamma reports `closed=true` and the YES/NO `outcomePrices` are exactly `[1,0]` or `[0,1]`. Anything ambiguous, voided, partially priced or unresolved remains open for investigation. This is a **conservative public-API settlement heuristic**, not a cryptographic or on-chain finality verification. Market closure alone is insufficient.
For each valid resolved basket, USD payout = paired units; AUD payout = USD payout × latest available USD/AUD reference rate. USD P/L = USD payout − simulated USD entry cost; AUD P/L = AUD payout − simulated AUD entry cost; percentage = AUD P/L / entry AUD cost × 100. Payout and ledger updates occur in one SQLite transaction. Duplicate settlement is blocked by basket status and unique settlement record.

## Telegram
Set these in repository-root `.env` on your VPS (do not commit credentials):
```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```
Notification after settlement:
```text
Position Closed
Market: Example market?
Amount wagered: A$100.00
P/L: A$+1.50 (+1.50%)
Current balance: A$5,001.50
```
Outbox is retried on later cycles; delivery is **at least once**, so a crash between Telegram acceptance and local acknowledgement can cause a duplicate notification. A missing token/chat does not block accounting.

## Local Windows run
```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\platform.ps1 start
.\scripts\platform.ps1 status
.\scripts\platform.ps1 logs
.\scripts\platform.ps1 stop
```
Report: `.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper report`.
Export: `.\.venv\Scripts\python.exe -m obsidian_prediction.parity_paper export`.

## Deployment readiness / limitations
**Not approved for unattended deployment until Windows smoke tests and live public Gamma resolution verification pass.** This PR does not introduce an automatic Windows Service/reboot restart mechanism. Existing optimistic snapshot fills remain an approximation; fees default to zero and should be configured realistically. Reference FX is daily, not executable. Closed markets without an exact one-hot Gamma payout remain open; manual exception review is necessary. Back up SQLite regularly and use only one running platform instance per DB.

## Verification checklist
- [ ] Run pytest on Windows and verify no regressions
- [ ] Launch platform manager, verify status and background logs
- [ ] Verify A$5,000 opening cash and A$100 basket budget
- [ ] Insert deterministic synthetic YES/NO resolution; verify payout and exactly one ledger credit
- [ ] Confirm next basket scales to updated equity
- [ ] Confirm ambiguous/closed-only market does not settle
- [ ] Configure Telegram test chat and verify one closed-position notification
- [ ] Restart process; verify unresolved positions resume and settled positions are not re-credited
- [ ] Validate live Gamma resolved market schema and actual market ID lookup
- [ ] Confirm VPS boot/restart and backup strategy before unattended operation

## Version history
- 2026-10-21: Add durable account ledger, resolution reconciliation, equity scaling and Telegram settlement notifications.
