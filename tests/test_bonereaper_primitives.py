"""Deterministic, network-free tests for Bonereaper paper eligibility and execution."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from obsidian_prediction.bonereaper_primitives import (
    classify_btc_market,
    decimal,
    simulate_depth,
)


def market(timeframe="5m", start=1793010000):
    seconds = {"5m": 300, "15m": 900}[timeframe]
    return {
        "slug": f"btc-updown-{timeframe}-{start}",
        "active": True,
        "closed": False,
        "conditionId": "0x" + "a" * 64,
        "clobTokenIds": '["123","456"]',
        "outcomes": '["Up","Down"]',
        "endDate": datetime.fromtimestamp(start + seconds, timezone.utc).isoformat(),
    }


@pytest.mark.parametrize("frame", ["5m", "15m"])
def test_short_btc_classification(frame):
    m = market(frame)
    result, code = classify_btc_market(m, "123", 1793010020)
    assert code == "ELIGIBLE"
    assert result is not None and result.timeframe == frame
    assert result.outcome == "Up"


@pytest.mark.parametrize("slug", [
    "eth-updown-5m-1793010000",
    "btc-updown-1h-1793010000",
    "btc-updown-5m-notatime",
    "btc-updown-5m-1793010000-extra",
])
def test_rejects_unapproved_markets(slug):
    m = market()
    m["slug"] = slug
    assert classify_btc_market(m, "123", 1793010020)[0] is None


def test_rejects_ambiguous_metadata():
    m = market()
    m["outcomes"] = '["Yes","No"]'
    assert classify_btc_market(m, "123", 1793010020)[1] == "REJECTED_OUTCOMES"
    m = market()
    m["endDate"] = "2026-01-01T00:00:00Z"
    assert classify_btc_market(m, "123", 1793010020)[1] == "REJECTED_EXPIRY_MISMATCH"
    m = market()
    m["closed"] = True
    assert classify_btc_market(m, "123", 1793010020)[1] == "REJECTED_MARKET_STATE"


def test_rejects_late_market_and_wrong_token():
    m = market()
    assert classify_btc_market(m, "123", 1793010250)[1] == "REJECTED_TIME_WINDOW"
    assert classify_btc_market(m, "999", 1793010020)[1] == "REJECTED_TOKEN"


def test_buy_depth_walk_and_fee():
    book = {"asks": [
        {"price": "0.51", "size": "5"}, {"price": "0.50", "size": "3"}
    ], "bids": [{"price": "0.49", "size": "7"}]}
    fill = simulate_depth(book, "BUY", Decimal("6"), Decimal("0.01"))
    assert fill.shares == Decimal("6")
    assert fill.notional == Decimal("3.03")
    assert fill.average_price == Decimal("0.505")
    assert fill.fee == Decimal("0.0303")
    assert fill.total_cash == Decimal("3.0603")
    assert fill.levels_consumed == 2


def test_sell_depth_best_bid_first():
    book = {"bids": [{"price": "0.40", "size": "3"}, {"price": "0.45", "size": "2"}]}
    fill = simulate_depth(book, "SELL", Decimal("4"), Decimal("0.01"))
    assert fill.notional == Decimal("1.70")
    assert fill.total_cash == Decimal("1.6830")


def test_insufficient_depth_fails_closed():
    book = {"asks": [{"price": "0.50", "size": "1"}]}
    with pytest.raises(ValueError, match="REJECTED_NO_DEPTH"):
        simulate_depth(book, "BUY", Decimal("2"))
    assert simulate_depth(book, "BUY", Decimal("2"), allow_partial=True).shares == 1


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "garbage"])
def test_bad_decimals_rejected(value):
    with pytest.raises(ValueError):
        decimal(value)


def test_invalid_tick_and_negative_size():
    with pytest.raises(ValueError):
        simulate_depth({"asks": [{"price": "0.505", "size": "4"}]},
                       "BUY", Decimal("2"))
    with pytest.raises(ValueError):
        simulate_depth({"asks": [{"price": "0.50", "size": "-4"}]},
                       "BUY", Decimal("2"))
