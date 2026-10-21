"""Precision-safe public binary market data and parity calculation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class Level:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.price <= Decimal("1")) or self.size < 0:
            raise ValueError("Invalid price or size")


@dataclass(frozen=True)
class Book:
    contract_id: str
    asks: tuple[Level, ...]
    received_at: datetime

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("Order book timestamp must be timezone aware")


@dataclass(frozen=True)
class Opportunity:
    pair_size: Decimal
    cost: Decimal
    payoff: Decimal
    fees: Decimal
    slippage: Decimal
    net_edge: Decimal
    edge_per_pair: Decimal


def execution_cost(asks: tuple[Level, ...], units: Decimal) -> Decimal | None:
    remaining = units
    total = Decimal("0")
    for level in sorted(asks, key=lambda x: x.price):
        if remaining <= 0:
            break
        take = min(level.size, remaining)
        total += take * level.price
        remaining -= take
    return total if remaining == 0 else None


def parity(
    yes: Book,
    no: Book,
    *,
    units: Decimal,
    payout_per_pair: Decimal = Decimal("1"),
    fee_rate: Decimal = Decimal("0"),
    slippage_per_pair: Decimal = Decimal("0"),
    max_age_seconds: int = 20,
    now: datetime | None = None,
    complementary_verified: bool = False,
) -> Opportunity | None:
    """Returns executable-depth-aware parity, not a trading instruction."""
    if not complementary_verified:
        return None
    if units <= 0 or payout_per_pair <= 0 or fee_rate < 0 or slippage_per_pair < 0:
        raise ValueError("Invalid economics")
    now = now or datetime.now(timezone.utc)
    for book in (yes, no):
        age = (now - book.received_at).total_seconds()
        if age < -2 or age > max_age_seconds:
            return None
    a, b = execution_cost(yes.asks, units), execution_cost(no.asks, units)
    if a is None or b is None:
        return None
    cost = a + b
    fees = cost * fee_rate
    slippage = units * slippage_per_pair
    payoff = units * payout_per_pair
    net_edge = payoff - cost - fees - slippage
    return Opportunity(units, cost, payoff, fees, slippage, net_edge, net_edge / units)
