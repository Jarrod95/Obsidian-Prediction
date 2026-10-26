# Bonereaper BTC follower — current engineering status

**NOT READY FOR MERGE OR WINDOWS VPS DEPLOYMENT.** This document is intentionally an honest release-gate report rather than a completed signoff.

## Code currently committed

- Standalone `src/obsidian_prediction/bonereaper_worker.py`: separate paper-only database and event journal, v2 keyset activity reader, BTC market filter, execution checks, portfolio cash/cost basis, source proportional sells, unambiguous-price settlement, status/events/positions/fills, pause/resume and SQLite integrity doctor.
- `bonereaper_primitives.py`: conservative BTC 5m/15m market classification and L2 depth VWAP.
- `bonereaper.env.example`: wallet candidate, explicit source-verification flag, FX and observe-only default.
- Windows scripts for detached start/verified PID stop/status/restart. **Not smoke-tested on Windows.** Stop is forceful; crash supervision and reboot autostart are not implemented.
- Offline tests for pure primitives and initial worker boundaries.
- `docs/bonereaper-copy-bot.md`: config, runbook, limitations and rollback.
- Existing bigggggggg worker and parity platform code unchanged.

## Release gates

| Gate | Result | Blocker / status |
|---|---|---|
| G01 | PARTIAL | current main inspected; complete before/after baseline and Windows verification missing |
| G02 | BLOCKED | candidate full wallet from two external profiles; official handle attribution not independently confirmed |
| G03 | PARTIAL | classifier with static fixtures, no live Gamma schema confirmation |
| G04 | PARTIAL | v2 cursor and dedup implemented, but no real feed replay / gap audit |
| G05 | PARTIAL | source-event age, market and price drift; no full strategy/replay matrix |
| G06 | PARTIAL | bid/ask depth and VWAP; no validated market-specific fees/latency replay |
| G07 | PARTIAL | cost-basis and settlement; no full crash/reconciliation/backup invariant suite |
| G08 | PARTIAL | sizing/exposure/concurrency/entry pause; daily drawdown stop not implemented |
| G09 | PARTIAL | independent DB and unchanged existing workers; concurrency smoke absent |
| G10 | BLOCKED | Windows launcher present but no smoke, writer lock, graceful stop or service watchdog |
| G11 | PARTIAL | status / events / fills / positions; no dashboard/Telegram or complete NAV |
| G12 | BLOCKED | clean Windows VPS deployment not rehearsed |
| G13 | BLOCKED | baseline earlier PR code passed 47 tests in GitHub Actions, but **new worker and scripts have not been tested** |
| G14 | BLOCKED | PR #11 remains a draft; not a completed release |

CI is exhausted per operator request. Do not trigger GitHub Actions for tests. The earlier successful GitHub Actions run (https://github.com/Jarrod95/Obsidian-Prediction/actions/runs/40280043964) predates the new worker and is not evidence that these changes pass.

## Local required commands

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m obsidian_prediction.cli doctor
```

Do **not** flip `BONEREAPER_SOURCE_VERIFIED` or start paper operation on unverified wallet attribution. Do not merge or deploy until all mandatory gates are demonstrated.

## Subsequent hardening changes

- Added a fail-closed post-requote budget assertion: no BUY fill is accepted if recomputed cash outlay still exceeds its budget.
- Malformed source numeric fields now receive a durable `REJECTED_SCHEMA` disposition instead of poisoning the whole processing poll.
- Added offline regression test cases for malformed-event isolation, ambiguous settlement rejection, once-only settlement across restart and invalid/stale FX.
- **These new tests have not been executed.** The current tool container cannot resolve github.com to clone the branch, and the connected GitHub app exposes repository read/write but no local shell runner. GitHub Actions must remain unused at the user's request.

### Known critical limitations to resolve before deploying

- The current source event key is a hash of the payload, not a verified canonical fill ID. Changed upstream serialization can lead to duplicate decisions; source attribution and feed completeness are not proven.
- The settlement predicate uses closed Gamma market and a binary outcome-price vector; this is not sufficient independent proof of official final settlement under all market states.
- The current Windows stop script uses forceful termination after verifying command-line PID, not graceful shutdown; no watchdog or autostart-on-reboot is provided.
- Status deliberately leaves open equity unmarked, so daily net drawdown protection and truthful NAV are not yet implemented.
- Telegram/dashboard shared integration, performance sensitivity report, exports, migration backup, crash injection and exact reproducible local test evidence are not complete.
