"""Independent, paper-only Bonereaper BTC copy worker.

Fail closed when attribution, source events, market metadata, book freshness,
depth or FX evidence cannot be verified. No authenticated order endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from .bonereaper_primitives import classify_btc_market, decimal, simulate_depth

BOT_ID = "polymarket_bonereaper_btc_copy"
DATA = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_key(row: dict) -> str:
    # Include the event payload as well as transaction identifier: split fills
    # in one transaction must not be silently collapsed.
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class PaperBot:
    def __init__(self, db: str, wallet: str, audusd: str, *,
                 fx_at: str, observe: bool = True, initial_aud: str = "500",
                 max_fraction: str = "0.05", max_deployed: str = "0.20",
                 fee_rate: str = "0", price_drift: str = "0.03",
                 book_max_age: int = 3, poll_seconds: int = 5,
                 timeframes: tuple[str, ...] = ("5m", "15m"),
                 http: httpx.Client | None = None):
        if not ADDRESS.fullmatch(wallet):
            raise ValueError("BONEREAPER_WALLET must be a full EVM address")
        if wallet.lower() == "0x" + "0" * 40:
            raise ValueError("Zero wallet rejected")
        self.wallet = wallet.lower()
        self.fx = decimal(audusd)
        self.initial_aud = decimal(initial_aud)
        self.max_fraction = decimal(max_fraction)
        self.max_deployed = decimal(max_deployed)
        self.fee = decimal(fee_rate)
        self.drift = decimal(price_drift)
        self.fx_at = datetime.fromisoformat(fx_at.replace("Z", "+00:00"))
        if self.fx_at.tzinfo is None or abs((datetime.now(timezone.utc) -
                                             self.fx_at).total_seconds()) > 86400:
            raise ValueError("FX timestamp missing or stale (>24h)")
        if not (0 < self.fx <= 2 and self.initial_aud > 0 and
                0 < self.max_fraction <= self.max_deployed <= 1 and
                0 <= self.fee < 1 and 0 <= self.drift < 1 and
                0 < book_max_age <= 30 and 1 <= poll_seconds <= 60):
            raise ValueError("Invalid FX, risk or polling configuration")
        if not timeframes or not set(timeframes) <= {"5m", "15m"}:
            raise ValueError("Only 5m and 15m BTC windows are supported")
        self.timeframes = frozenset(timeframes)
        self.book_max_age = book_max_age
        self.poll_seconds = poll_seconds
        self.observe = observe
        self.path = Path(db).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=15)
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          event_key TEXT PRIMARY KEY, ts INTEGER NOT NULL, payload TEXT NOT NULL,
          disposition TEXT NOT NULL, reason TEXT, processed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions (
          token TEXT PRIMARY KEY, condition_id TEXT NOT NULL, outcome TEXT NOT NULL,
          shares TEXT NOT NULL, basis TEXT NOT NULL, source_shares TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fills (
          event_key TEXT PRIMARY KEY REFERENCES events(event_key),
          token TEXT NOT NULL, side TEXT NOT NULL, shares TEXT NOT NULL,
          price TEXT NOT NULL, fee TEXT NOT NULL, cash_delta TEXT NOT NULL,
          realized TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlements (
          token TEXT PRIMARY KEY, payout TEXT NOT NULL, proceeds TEXT NOT NULL,
          realized TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incidents (
          at TEXT NOT NULL, detail TEXT NOT NULL);
        """)
        for key, value in {"bot_id": BOT_ID, "wallet": self.wallet,
                           "mode": "observe" if observe else "paper",
                           "schema_version": "1"}.items():
            old = self.get(key)
            if old is not None and old != value:
                raise ValueError(f"DB identity mismatch: {key}; use another DB")
            self.set(key, value)
        if self.get("cash") is None:
            self.set("cash", self.initial_aud * self.fx)
            self.set("initial_cash", self.initial_aud * self.fx)
            self.set("realized", "0")
            self.set("paused", "0")
        self.http = http or httpx.Client(timeout=10, headers={
            "User-Agent": "ObsidianPrediction-BonereaperPaper/1"})
        self.stopping = False

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: object) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    def request(self, base: str, path: str, params: dict) -> object:
        response = self.http.get(base + path, params=params)
        response.raise_for_status()
        return response.json()

    def activity(self, since: int) -> list[dict]:
        """Official v2 ascending keyset feed; refuse loops/truncation/schema changes."""
        rows = []
        cursor = None
        seen = set()
        for _ in range(20):
            params = {"user": self.wallet, "type": "TRADE", "start": max(1, since - 120),
                      "sort_direction": "ASC", "limit": 500}
            if cursor:
                params["cursor"] = cursor
            envelope = self.request(DATA, "/v2/activity", params)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), list):
                raise RuntimeError("Invalid v2 activity envelope")
            rows.extend(envelope["data"])
            pagination = envelope.get("pagination")
            if not isinstance(pagination, dict):
                raise RuntimeError("Missing cursor metadata")
            cursor = pagination.get("next_cursor")
            if not cursor:
                if pagination.get("has_more"):
                    raise RuntimeError("Activity truncated without cursor")
                return rows
            if cursor in seen:
                raise RuntimeError("Repeated activity cursor")
            seen.add(cursor)
        raise RuntimeError("Source catch-up exceeded 20 pages; no checkpoint advance")

    def metadata(self, condition: str) -> dict:
        result = self.request(GAMMA, "/markets", {"condition_id": condition, "limit": 100})
        if not isinstance(result, list):
            raise ValueError("Invalid market response")
        matches = [m for m in result if str(m.get("conditionId", "")).lower() == condition.lower()]
        if len(matches) != 1:
            raise ValueError("Market ambiguous or missing")
        return matches[0]

    def book(self, token: str, side: str, qty: Decimal):
        data = self.request(CLOB, "/book", {"token_id": token})
        if not isinstance(data, dict):
            raise ValueError("Bad book response")
        observed = data.get("timestamp")
        if observed is None:
            raise ValueError("Missing exchange quote timestamp")
        quote_seconds = decimal(observed) / (1000 if decimal(observed) > 100000000000 else 1)
        if abs(Decimal(str(time.time())) - quote_seconds) > self.book_max_age:
            raise ValueError("REJECTED_STALE_BOOK")
        tick = decimal(data.get("tick_size", "0.01"))
        return simulate_depth(data, side, qty, self.fee, tick_size=tick)

    def holdings(self) -> dict:
        return {r[0]: r for r in self.conn.execute(
            "SELECT token,condition_id,outcome,shares,basis,source_shares FROM positions")}

    def status(self) -> dict:
        rows = self.holdings()
        cash = decimal(self.get("cash"))
        basis = sum((decimal(p[4]) for p in rows.values()), Decimal(0))
        counts = dict(self.conn.execute(
            "SELECT disposition,COUNT(*) FROM events GROUP BY disposition").fetchall())
        # Unknown bid mark is never represented as an accurate NAV.
        return {"bot": BOT_ID, "source": "@bonereaper",
                "mode": "observe" if self.observe else "paper",
                "wallet": self.wallet, "cash_usdc_assumed": str(cash),
                "open_basis_usdc_assumed": str(basis),
                "realized_usdc_assumed": self.get("realized"),
                "open_positions": len(rows), "events": counts,
                "last_poll_success": self.get("last_poll_success"),
                "last_error": self.get("last_error"),
                "paused": self.get("paused") == "1",
                "last_heartbeat": self.get("heartbeat"),
                "paper_equity_unmarked": True, "fx_audusd": str(self.fx)}

    def handle(self, event: dict) -> None:
        key = event_key(event)
        if self.conn.execute("SELECT 1 FROM events WHERE event_key=?", (key,)).fetchone():
            return
        side = str(event.get("side", "")).upper()
        token = str(event.get("asset") or event.get("token_id") or "")
        condition = str(event.get("conditionId") or event.get("condition_id") or "")
        ts = int(event.get("timestamp", 0))
        size = decimal(event.get("size", 0))
        source_price = decimal(event.get("price", 0))
        reason = "REJECTED_SCHEMA"
        decision = None
        fill = None
        previous = self.holdings().get(token)
        current = int(time.time())
        if side in {"BUY", "SELL"} and token and condition and size > 0 and 0 < source_price < 1:
            age = current - ts
            if age < -2 or age > 20:
                reason = "REJECTED_LATE"
            elif side == "SELL" and previous is None:
                reason = "REJECTED_NOT_HELD"
            elif side == "BUY" and self.get("paused") == "1":
                reason = "REJECTED_PAUSED"
            else:
                try:
                    market = self.metadata(condition)
                    identity, reason = classify_btc_market(market, token, current)
                    if identity is not None and identity.timeframe not in self.timeframes:
                        identity, reason = None, "REJECTED_TIMEFRAME"
                    if identity is not None and age > (10 if identity.timeframe == "5m" else 20):
                        identity, reason = None, "REJECTED_LATE"
                    if identity is not None:
                        cash = decimal(self.get("cash"))
                        holdings = self.holdings()
                        deployed = sum((decimal(p[4]) for p in holdings.values()), Decimal(0))
                        if side == "BUY":
                            if len({p[1] for p in holdings.values()}) >= 3 and not previous:
                                reason = "REJECTED_MAX_MARKETS"
                            else:
                                per_budget = self.max_fraction * decimal(self.get("initial_cash"))
                                aggregate = self.max_deployed * decimal(self.get("initial_cash"))
                                budget = min(cash, per_budget - (decimal(previous[4]) if previous else 0),
                                             aggregate - deployed)
                                if budget < 1:
                                    reason = "REJECTED_RISK"
                                else:
                                    qty = budget / (Decimal(1) + self.fee)
                                    # Conservative upper bound at price=1; actual request below capped by budget.
                                    depth = self.book(token, side, qty)
                                    if depth.total_cash > budget:
                                        qty = qty * budget / depth.total_cash
                                        depth = self.book(token, side, qty)
                                    if depth.average_price - source_price > self.drift:
                                        reason = "REJECTED_PRICE_DRIFT"
                                    else:
                                        fill = (qty, depth)
                        else:
                            ratio = min(Decimal(1), size / decimal(previous[5])) if decimal(previous[5]) > 0 else 0
                            qty = decimal(previous[3]) * ratio
                            if qty <= 0:
                                reason = "REJECTED_NOT_HELD"
                            else:
                                fill = (qty, self.book(token, side, qty))
                        if fill is not None:
                            reason = "COPIED" if not self.observe else "OBSERVED_ELIGIBLE"
                except (ValueError, KeyError, TypeError, httpx.HTTPError, RuntimeError) as exc:
                    reason = f"REJECTED_DATA:{str(exc)[:90]}"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (key, ts, json.dumps(event, sort_keys=True), reason, None, now_utc()))
            if fill is not None and not self.observe:
                qty, bookfill = fill
                old_qty = decimal(previous[3]) if previous else Decimal(0)
                old_basis = decimal(previous[4]) if previous else Decimal(0)
                old_source = decimal(previous[5]) if previous else Decimal(0)
                if side == "BUY":
                    cost = bookfill.total_cash
                    self.set("cash", decimal(self.get("cash")) - cost)
                    self.conn.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)",
                                      (token, condition, identity.outcome, str(old_qty + qty),
                                       str(old_basis + cost), str(old_source + size)))
                    change, realized = -cost, Decimal(0)
                else:
                    proceeds = bookfill.total_cash
                    allocated = old_basis * qty / old_qty
                    realized = proceeds - allocated
                    change = proceeds
                    self.set("cash", decimal(self.get("cash")) + proceeds)
                    self.set("realized", decimal(self.get("realized")) + realized)
                    if qty >= old_qty:
                        self.conn.execute("DELETE FROM positions WHERE token=?", (token,))
                    else:
                        self.conn.execute("UPDATE positions SET shares=?,basis=?,source_shares=? WHERE token=?",
                            (str(old_qty - qty), str(old_basis - allocated),
                             str(max(Decimal(0), old_source - size)), token))
                self.conn.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, token, side, str(qty), str(bookfill.average_price),
                     str(bookfill.fee), str(change), str(realized), now_utc()))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def settle(self) -> None:
        for token, position in self.holdings().items():
            condition = position[1]
            try:
                market = self.metadata(condition)
                if not market.get("closed"):
                    continue
                tokens = market.get("clobTokenIds")
                values = market.get("outcomePrices")
                tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
                values = json.loads(values) if isinstance(values, str) else values
                if not isinstance(tokens, list) or not isinstance(values, list):
                    continue
                if len(tokens) != len(values) or token not in [str(t) for t in tokens]:
                    continue
                payouts = [decimal(v) for v in values]
                if payouts.count(1) != 1 or any(v not in (0, 1) for v in payouts):
                    continue
                payout = payouts[[str(t) for t in tokens].index(token)]
                amount = decimal(position[3]) * payout
                realized = amount - decimal(position[4])
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    if not self.conn.execute("SELECT 1 FROM settlements WHERE token=?",
                                             (token,)).fetchone():
                        self.set("cash", decimal(self.get("cash")) + amount)
                        self.set("realized", decimal(self.get("realized")) + realized)
                        self.conn.execute("INSERT INTO settlements VALUES (?,?,?,?,?)",
                                          (token, str(payout), str(amount), str(realized), now_utc()))
                        self.conn.execute("DELETE FROM positions WHERE token=?", (token,))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                self.conn.execute("INSERT INTO incidents VALUES (?,?)",
                                  (now_utc(), "Settlement deferred: " + str(exc)[:200]))

    def tick(self) -> None:
        self.set("heartbeat", now_utc())
        self.settle()
        if self.get("watermark") is None:
            self.set("watermark", int(time.time()))
            return
        since = int(self.get("watermark"))
        events = self.activity(since)
        for event in sorted(events, key=lambda e: (int(e.get("timestamp", 0)), event_key(e))):
            self.handle(event)
        # Advance only after every event decision committed.
        if events:
            self.set("watermark", max(since, max(int(e["timestamp"]) for e in events)))
        self.set("last_poll_success", now_utc())
        self.set("last_error", "")

    def run(self, once: bool) -> None:
        while not self.stopping:
            try:
                self.tick()
            except (httpx.HTTPError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self.set("last_error", str(exc)[:300])
                self.conn.execute("INSERT INTO incidents VALUES (?,?)", (now_utc(), str(exc)[:500]))
                if once:
                    raise
            if once:
                return
            time.sleep(self.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bonereaper BTC paper follower; never submits orders")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["run", "status", "pause", "resume", "events", "positions", "fills", "doctor"])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    wallet = os.environ.get("BONEREAPER_WALLET", "")
    if not os.environ.get("BONEREAPER_SOURCE_VERIFIED") == "YES":
        raise SystemExit("Source wallet attribution not confirmed: set BONEREAPER_SOURCE_VERIFIED=YES only after checking official profile data")
    bot = PaperBot(os.environ.get("BONEREAPER_DB", "data/bonereaper/paper.sqlite"),
                   wallet, os.environ["BONEREAPER_AUDUSD"],
                   fx_at=os.environ["BONEREAPER_FX_AT"],
                   observe=os.environ.get("BONEREAPER_MODE", "observe") == "observe")
    if args.command == "run":
        signal.signal(signal.SIGTERM, lambda *_: setattr(bot, "stopping", True))
        bot.run(args.once)
    elif args.command in ("pause", "resume"):
        bot.set("paused", "1" if args.command == "pause" else "0")
        print(json.dumps(bot.status(), indent=2))
    elif args.command in ("events", "positions", "fills"):
        table = {"events": "events", "positions": "positions", "fills": "fills"}[args.command]
        cursor = bot.conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 50")
        columns = [x[0] for x in cursor.description]
        print(json.dumps([dict(zip(columns, row)) for row in cursor], indent=2))
    elif args.command == "doctor":
        print(json.dumps({"sqlite_integrity": bot.conn.execute("PRAGMA integrity_check").fetchone()[0],
                          "database": str(bot.path), "mode": "observe" if bot.observe else "paper"}, indent=2))
    else:
        print(json.dumps(bot.status(), indent=2))


if __name__ == "__main__":
    main()
