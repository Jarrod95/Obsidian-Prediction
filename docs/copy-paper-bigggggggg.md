# Polymarket copy-paper experiment (not production certified)

Target: https://polymarket.com/@bigggggggg

This worker never submits real orders. It begins with AUD 500 converted to USD at an explicitly supplied rate, then tracks cash, positions, realized P/L and marked equity in SQLite. It observes source position-size changes and simulates fills at available best bid/ask. A position sale returns simulated proceeds to cash, so later trades can compound or draw down. It starts flat and ignores pre-existing holdings until their sizes increase.

## Required setup

1. Resolve the profile's actual **proxy wallet** from an authoritative Polymarket profile/API response. The username is NOT an EVM wallet.
2. Set environment variables on the VPS:

```powershell
$env:COPY_WALLET="0xREPLACE_WITH_VERIFIED_40_HEX_CHAR_ADDRESS"
$env:COPY_AUDUSD="0.65" # EXAMPLE ONLY: replace with a verified current rate
$env:COPY_INITIAL_AUD="500"
$env:COPY_MAX_POSITION_FRACTION="0.10"
$env:COPY_POLL_SECONDS="30"
$env:COPY_DB="data/copy_bigggggggg.sqlite"
$env:COPY_FEE_RATE="0"
python -m obsidian_prediction.copy_paper --once
python -m obsidian_prediction.copy_paper
```

## Important limitations before VPS deployment

- This is a first implementation, not yet validated against live API responses, and no automated tests have been executed.
- Legacy Data API endpoints may change; compare with the current v2 documentation.
- Paper fills use top-of-book prices without depth/slippage simulation; reported returns may be optimistic.
- The worker does not yet implement verified settlement/redemption for winning and losing tokens, market resolution checks, full source trade-event reconciliation, partial-fill depth, currency-rate updates, supervised process management, Telegram integration or dashboard integration.
- A sell that fails and an increase on an already-held source position require more robust event-level reconciliation.
- Keep it out of the VPS launch roster until the above are implemented and integration tests pass.
