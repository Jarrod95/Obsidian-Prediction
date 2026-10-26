# Bonereaper BTC copy implementation — verification status

**Status: NOT READY FOR MERGE OR DEPLOYMENT.** This branch contains only the initial, isolated safety primitives and deterministic unit tests. It does **not** satisfy the complete Bonereaper implementation contract.

## Implemented

- Strict BTC 5m/15m slug plus condition/token/outcome/end-time/state validation, with reason-coded exclusions.
- Decimal-based order-book execution simulation: buy walks asks, sell walks bids; fee, tick and insufficient depth handling.
- Network-free tests for eligibility, timing, malformed metadata, depth, VWAP, precision and fees.
- Existing `copy_paper.py`, `@bigggggggg` configuration, wallet and parity bot are unchanged.

## Critical blockers / incomplete contract

- Full Bonereaper wallet attribution has **not** been verified from the official proxy/profile mapping. Do not create a source wallet by guessing or inferring from the handle.
- No dedicated Bonereaper runtime, ingestion, journal, risk manager, persistent ledger, settlement, reporting, Telegram integration or Windows service/lifecycle management has been wired up.
- Current legacy source ingestion and copy engine are **not** safe for 5m replication without further work.
- Market classifier assumes Gamma short-window slugs and end-date schema; fixtures need confirmation against current official Gamma responses before integration.
- Quote freshness and order-book API schema must be validated at the adapter boundary. These pure functions do not validate quote age.
- No observed profitability, follower latency study, forward-test, Windows VPS smoke or deployment rehearsal exists for this branch. The existing regression suite passed in GitHub Actions (47 tests) but does not cover the missing worker implementation.
- No deployment instructions are valid until the independent runtime and its migration/restart tooling exist.

## Acceptance gates

| Gate | Status |
|---|---|
| G01 baseline | PARTIAL — repository tree and existing entrypoints inspected; baseline tests not executed |
| G02 wallet | BLOCKED — no verified address |
| G03 BTC filter | PARTIAL — coded with unit fixtures; live schema not yet confirmed |
| G04 feed | NOT DONE |
| G05 decisions | NOT DONE |
| G06 fill realism | PARTIAL — depth primitive only, not integrated |
| G07 accounting | NOT DONE |
| G08 risk | NOT DONE |
| G09 isolation | NOT DONE |
| G10 Windows operations | NOT DONE |
| G11 visibility | NOT DONE |
| G12 deployment | NOT DONE |
| G13 quality | PARTIAL — GitHub Actions Linux Python 3.12: Ruff, 47 pytest, doctor PASS; no Windows smoke or full spec tests |
| G14 merge-ready PR | NOT DONE |

## Safe reviewer commands

```powershell
python -m pytest -q tests/test_bonereaper_primitives.py
python -m pytest -q
python -m ruff check .
```

CI evidence: https://github.com/Jarrod95/Obsidian-Prediction/actions/runs/40280043964 (Ruff success, pytest 47 passed, CLI doctor success). This does **not** certify the full specification.\n\nNo new strategy must be enabled in `bots/registry.json` until all release gates pass. This branch should remain draft / explicitly blocked.
