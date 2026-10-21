from datetime import date
from decimal import Decimal
import pytest
from obsidian_prediction.fx import parse_fx

def test_fx_valid():
    today = date(2026, 10, 21)
    assert parse_fx({"base": "USD", "quote": "AUD", "rate": 1.52, "date": "2026-10-20"}, today=today) == (Decimal("1.52"), "2026-10-20")

def test_fx_wrong_pair():
    with pytest.raises(ValueError):
        parse_fx({"base": "AUD", "quote": "USD", "rate": 0.66, "date": "2026-10-20"}, today=date(2026, 10, 21))

def test_fx_stale():
    with pytest.raises(ValueError):
        parse_fx({"base": "USD", "quote": "AUD", "rate": 1.52, "date": "2026-10-01"}, today=date(2026, 10, 21))

def test_fx_zero():
    with pytest.raises(ValueError):
        parse_fx({"base": "USD", "quote": "AUD", "rate": 0, "date": "2026-10-20"}, today=date(2026, 10, 21))
