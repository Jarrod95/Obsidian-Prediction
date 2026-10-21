from datetime import datetime, timedelta, timezone
from decimal import Decimal

from obsidian_prediction.markets import Book, Level, parity

NOW = datetime.now(timezone.utc)


def book(name, levels, time=NOW):
    return Book(
        name,
        tuple(Level(Decimal(str(p)), Decimal(str(q))) for p, q in levels),
        time,
    )


def test_size_aware_parity_and_fee():
    yes = book("yes", [("0.40", "2"), ("0.45", "3")])
    no = book("no", [("0.50", "5")])
    result = parity(
        yes,
        no,
        units=Decimal("4"),
        fee_rate=Decimal("0.01"),
        complementary_verified=True,
        now=NOW,
    )
    assert result.cost == Decimal("3.7")
    assert result.net_edge == Decimal("0.263")


def test_unverified_never_scans():
    assert parity(
        book("yes", [("0.1", "1")]),
        book("no", [("0.1", "1")]),
        units=Decimal("1"),
        now=NOW,
    ) is None


def test_insufficient_depth_and_stale_data():
    yes = book("yes", [("0.4", "1")])
    no = book("no", [("0.4", "5")])
    assert parity(
        yes,
        no,
        units=Decimal("2"),
        complementary_verified=True,
        now=NOW,
    ) is None
    stale = book("yes", [("0.4", "5")], NOW - timedelta(minutes=1))
    assert parity(
        stale,
        no,
        units=Decimal("2"),
        complementary_verified=True,
        now=NOW,
    ) is None
