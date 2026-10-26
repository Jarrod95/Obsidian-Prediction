"""Conservative primitives for Bonereaper BTC short-window paper copying.

Pure functions intentionally fail closed on ambiguous metadata and book data.
No order placement is possible from this module.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

BTC_SLUG = re.compile(r"^btc-updown-(5m|15m)-(\d{10})$")
TIMEFRAMES = {"5m": 300, "15m": 900}


def decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid decimal value") from exc
    if not result.is_finite():
        raise ValueError("Non-finite decimal value")
    return result


def utc_timestamp(value: object) -> int:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing UTC timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return int(dt.astimezone(timezone.utc).timestamp())


@dataclass(frozen=True)
class MarketIdentity:
    condition_id: str
    token_id: str
    outcome: str
    timeframe: str
    opened: int
    expires: int


def classify_btc_market(metadata: Mapping[str, object], token_id: str,
                        now: int, min_remaining: Mapping[str, int] | None = None
                        ) -> tuple[MarketIdentity | None, str]:
    """Return only structurally validated BTC 5m/15m Up/Down markets.

    Timestamp in slug denotes start of the short trading window, not necessarily
    the Gamma market creation time. Refuse unsupported/missing schema instead of
    guessing from titles or condition IDs.
    """
    slug = str(metadata.get("slug") or "")
    match = BTC_SLUG.fullmatch(slug)
    if not match:
        return None, "REJECTED_MARKET_SLUG"
    timeframe, start = match.group(1), int(match.group(2))
    end = start + TIMEFRAMES[timeframe]
    if metadata.get("closed") is not False or metadata.get("active") is not True:
        return None, "REJECTED_MARKET_STATE"
    condition = str(metadata.get("conditionId") or "")
    if not condition.startswith("0x") or len(condition) != 66:
        return None, "REJECTED_CONDITION_ID"
    raw_tokens = metadata.get("clobTokenIds")
    raw_outcomes = metadata.get("outcomes")
    try:
        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    except (ValueError, TypeError):
        return None, "REJECTED_METADATA"
    if not isinstance(tokens, list) or not isinstance(outcomes, list):
        return None, "REJECTED_METADATA"
    if len(tokens) != 2 or len(outcomes) != 2 or set(outcomes) != {"Up", "Down"}:
        return None, "REJECTED_OUTCOMES"
    tokens = [str(t) for t in tokens]
    if len(set(tokens)) != 2 or token_id not in tokens:
        return None, "REJECTED_TOKEN"
    try:
        declared_end = utc_timestamp(str(metadata.get("endDate") or ""))
    except ValueError:
        return None, "REJECTED_EXPIRY_METADATA"
    if abs(declared_end - end) > 1:
        return None, "REJECTED_EXPIRY_MISMATCH"
    threshold = (min_remaining or {"5m": 60, "15m": 120})[timeframe]
    if now < start or end - now < threshold:
        return None, "REJECTED_TIME_WINDOW"
    return MarketIdentity(condition, token_id, str(outcomes[tokens.index(token_id)]),
                          timeframe, start, end), "ELIGIBLE"


@dataclass(frozen=True)
class DepthFill:
    shares: Decimal
    average_price: Decimal
    notional: Decimal
    fee: Decimal
    total_cash: Decimal
    levels_consumed: int


def simulate_depth(book: Mapping[str, object], side: str, quantity: Decimal,
                   fee_rate: Decimal = Decimal("0"),
                   *, allow_partial: bool = False,
                   tick_size: Decimal = Decimal("0.01")) -> DepthFill:
    """Walk actual book asks for BUY and bids for SELL; no midpoint fills.

    Quote timestamp/freshness must be validated by the caller from the
    contemporaneous feed, not inferred from the local execution clock.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError("Invalid order side")
    quantity, fee_rate, tick_size = decimal(quantity), decimal(fee_rate), decimal(tick_size)
    if quantity <= 0 or not 0 <= fee_rate < 1 or not 0 < tick_size <= 1:
        raise ValueError("Invalid quantity, fee or tick size")
    levels = book.get("asks" if side == "BUY" else "bids")
    if not isinstance(levels, list):
        raise ValueError("Missing book levels")
    parsed = []
    for level in levels:
        if not isinstance(level, dict):
            raise ValueError("Malformed level")
        price, size = decimal(level.get("price")), decimal(level.get("size"))
        if not 0 < price < 1 or size <= 0:
            raise ValueError("Invalid book level")
        if price % tick_size != 0:
            raise ValueError("Non-tick-aligned price")
        parsed.append((price, size))
    parsed.sort(key=lambda item: item[0], reverse=side == "SELL")
    remaining, gross, count = quantity, Decimal("0"), 0
    for price, available in parsed:
        taken = min(remaining, available)
        if taken:
            gross += taken * price
            remaining -= taken
            count += 1
        if remaining == 0:
            break
    executed = quantity - remaining
    if executed <= 0 or (remaining and not allow_partial):
        raise ValueError("REJECTED_NO_DEPTH")
    fee = gross * fee_rate
    return DepthFill(executed, gross / executed, gross, fee,
                     gross + fee if side == "BUY" else gross - fee, count)
