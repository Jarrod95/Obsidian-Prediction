"""Read-only Polymarket copy-trading paper worker. No live order capability.

Configure COPY_WALLET with the target user's *proxy wallet* address.
Uses legacy public Data API routes and CLOB midpoint prices. Execution is simulated.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import re
import time
from .copy_events import setup as setup_events, fetch_activity, ingest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

LOG = logging.getLogger("copy_paper")
D = lambda x: Decimal(str(x))
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Worker:
    def __init__(self, wallet: str, db: str, initial_aud: Decimal = D(500),
                 max_fraction: Decimal = D("0.10"), poll: int = 30):
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
            raise ValueError("COPY_WALLET must be a valid hex EVM address")
        if not (D("0") < max_fraction <= D("1")):
            raise ValueError("COPY_MAX_POSITION_FRACTION must be in (0,1]")
        if poll < 5:
            raise ValueError("COPY_POLL_SECONDS must be >= 5")
        self.wallet, self.poll, self.max_fraction = wallet.lower(), poll, max_fraction
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(
          asset TEXT PRIMARY KEY, condition_id TEXT, outcome TEXT,
          shares TEXT NOT NULL, cost_usd TEXT NOT NULL, last_price TEXT NOT NULL,
          source_shares TEXT NOT NULL, opened_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fills(
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, asset TEXT, side TEXT,
          shares TEXT, price TEXT, cash_change_usd TEXT, realized_pnl_usd TEXT,
          reason TEXT);
        CREATE TABLE IF NOT EXISTS observations(
          at TEXT, cash_usd TEXT, equity_usd TEXT, unrealized_usd TEXT);
        """)
        self.conn.execute("CREATE TABLE IF NOT EXISTS settled(asset TEXT PRIMARY KEY, payout TEXT, at TEXT)")
        setup_events(self.conn)
        self.http = httpx.Client(timeout=15, headers={"User-Agent": "ObsidianPrediction-Paper/1"})
        if self.get("cash_usd") is None:
            fx = self.fx()
            self.set("initial_aud", initial_aud)
            self.set("initial_usd", initial_aud * fx)
            self.set("cash_usd", initial_aud * fx)
            self.set("audusd", fx)
            self.set("realized_usd", 0)
            self.set("initialized", "0")

    def get(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    def fx(self):
        # Explicit FX override avoids silently pretending AUD and USDC are equivalent.
        value = os.environ.get("COPY_AUDUSD")
        if not value or D(value) <= 0:
            raise ValueError("Set COPY_AUDUSD to a verified AUD/USD conversion rate")
        return D(value)

    def api(self, base, path, params):
        r = self.http.get(base + path, params=params)
        r.raise_for_status()
        value = r.json()
        if isinstance(value, dict) and "data" in value:
            return value["data"]
        return value

    def source(self):
        result = {}
        cursor = None
        seen = set()
        while True:
            params = {"user": self.wallet, "status": "OPEN", "limit": 500,
                      "filter_type": "TOKENS", "filter_amount": 0}
            if cursor:
                params["cursor"] = cursor
            response = self.http.get(DATA + "/v2/positions", params=params)
            response.raise_for_status()
            body = response.json()
            page = body.get("data")
            if not isinstance(page, list):
                raise RuntimeError("Invalid v2 positions response")
            for p in page:
                asset = str(p.get("token_id") or p.get("asset") or "")
                size = p.get("current_size", p.get("size"))
                if not asset or size is None:
                    raise RuntimeError("Position missing asset or size")
                if D(size) > 0:
                    result[asset] = {"asset": asset, "size": str(size),
                                     "conditionId": p.get("condition_id", p.get("conditionId", "")),
                                     "outcome": p.get("outcome", "")}
            cursor = (body.get("pagination") or {}).get("next_cursor")
            if not cursor:
                return result
            if cursor in seen:
                raise RuntimeError("Repeated v2 cursor")
            seen.add(cursor)

    def resolution(self, condition_id, asset):
        if not condition_id:
            return None
        response = self.http.get(GAMMA + "/markets", params={"condition_id": condition_id, "limit": 100})
        response.raise_for_status()
        markets = response.json()
        if not isinstance(markets, list):
            raise RuntimeError("Invalid Gamma response")
        matching = [m for m in markets if str(m.get("conditionId", "")).lower() == str(condition_id).lower()]
        if len(matching) != 1:
            return None
        market = matching[0]
        if not market.get("closed"):
            return None
        tokens = market.get("clobTokenIds")
        prices = market.get("outcomePrices")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not isinstance(tokens, list) or not isinstance(prices, list) or len(tokens) != len(prices):
            return None
        identifiers = [str(token) for token in tokens]
        if asset not in identifiers:
            return None
        values = [D(price) for price in prices]
        if values.count(D(1)) != 1 or any(v not in (D(0), D(1)) for v in values):
            return None
        return values[identifiers.index(asset)]

    def price(self, asset, side):
        # Buy at ask, sell at bid; never fill at an optimistic midpoint.
        book = self.api(CLOB, "/book", {"token_id": asset})
        levels = book.get("asks" if side == "BUY" else "bids", [])
        if not levels:
            raise RuntimeError(f"No {side} liquidity for {asset}")
        prices = [D(x["price"]) for x in levels if D(x.get("size", 0)) > 0]
        if not prices:
            raise RuntimeError(f"No executable {side} quote for {asset}")
        return min(prices) if side == "BUY" else max(prices)

    def positions(self):
        return {r[0]: r for r in self.conn.execute(
            "SELECT asset,condition_id,outcome,shares,cost_usd,last_price,source_shares,opened_at FROM positions")}

    def fill(self, asset, side, shares, price, pnl, reason):
        fee = D(os.getenv("COPY_FEE_RATE", "0"))
        gross = shares * price
        change = -gross * (1 + fee) if side == "BUY" else gross * (1 - fee)
        cash = D(self.get("cash_usd")) + change
        self.set("cash_usd", cash)
        self.set("realized_usd", D(self.get("realized_usd")) + pnl)
        self.conn.execute("INSERT INTO fills(at,asset,side,shares,price,cash_change_usd,realized_pnl_usd,reason) VALUES (?,?,?,?,?,?,?,?)",
                          (utcnow(), asset, side, str(shares), str(price), str(change), str(pnl), reason))

    def tick(self):
        # Initial start is flat. Source events are journaled from now onward.
        if self.get("event_watermark") is None:
            self.set("event_watermark", int(time.time()))
            LOG.info("Started source trade journal at current timestamp")
            return
        watermark = int(self.get("event_watermark"))
        events = fetch_activity(self.http, self.wallet, watermark)
        ingest(self.conn, events)
        if events:
            self.set("event_watermark", max(watermark, max(int(e["timestamp"]) for e in events)))
        # Always settle holdings first; settlement does not require a source sale.
        for asset, row in list(self.positions().items()):
            try:
                payout = self.resolution(row[1], asset)
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                LOG.warning("Settlement lookup deferred %s: %s", asset, exc)
                continue
            if payout is None:
                continue
            qty, basis = D(row[3]), D(row[4])
            proceeds = qty * payout
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.set("cash_usd", D(self.get("cash_usd")) + proceeds)
                self.set("realized_usd", D(self.get("realized_usd")) + proceeds - basis)
                self.conn.execute(
                    "INSERT INTO fills(at,asset,side,shares,price,cash_change_usd,realized_pnl_usd,reason) VALUES (?,?,?,?,?,?,?,?)",
                    (utcnow(), asset, "SETTLE", str(qty), str(payout),
                     str(proceeds), str(proceeds - basis), "market_resolution"))
                self.conn.execute("DELETE FROM positions WHERE asset=?", (asset,))
                self.conn.execute("INSERT OR REPLACE INTO settled VALUES (?,?,?)",
                                  (asset, str(payout), utcnow()))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        # Each journaled source trade is applied at most once, in time order.
        pending = self.conn.execute(
            "SELECT event_key,asset,condition_id,outcome,side,source_size FROM copy_events "
            "WHERE status='PENDING' ORDER BY ts,event_key").fetchall()
        for event_key, asset, condition, outcome, side, source_size in pending:
            if self.conn.execute("SELECT 1 FROM settled WHERE asset=?", (asset,)).fetchone():
                self.conn.execute("UPDATE copy_events SET status='SKIPPED' WHERE event_key=?", (event_key,))
                continue
            held = self.positions().get(asset)
            try:
                px = self.price(asset, side)
            except (httpx.HTTPError, RuntimeError) as exc:
                LOG.warning("Trade %s deferred: %s", event_key, exc)
                # Do not process later trades out of order when an earlier one failed.
                break
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if side == "BUY":
                    cash = D(self.get("cash_usd"))
                    # Current equity is cash plus conservative last-known holding marks.
                    equity = cash + sum(D(p[3]) * D(p[5]) for p in self.positions().values())
                    current_value = D(held[3]) * px if held else D(0)
                    budget = max(D(0), min(cash, equity * self.max_fraction - current_value))
                    fee = D(os.getenv("COPY_FEE_RATE", "0"))
                    if budget >= D(os.getenv("COPY_MIN_TRADE_USD", "1")):
                        qty = budget / (px * (1 + fee))
                        self.fill(asset, "BUY", qty, px, D(0), "copied_source_trade")
                        self.conn.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?)",
                            (asset, condition or (held[1] if held else ""),
                             outcome or (held[2] if held else ""),
                             str(qty + (D(held[3]) if held else 0)),
                             str(budget + (D(held[4]) if held else 0)),
                             str(px), str(D(source_size) + (D(held[6]) if held else 0)), utcnow()))
                elif held:
                    # Source sells reduce the copied position in the same proportion.
                    tracked_source = D(held[6])
                    held_qty = D(held[3])
                    qty = held_qty * min(D(1), D(source_size) / tracked_source) if tracked_source > 0 else D(0)
                    if qty > 0:
                        basis = D(held[4]) * qty / held_qty
                        fee = D(os.getenv("COPY_FEE_RATE", "0"))
                        self.fill(asset, "SELL", qty, px, qty * px * (1 - fee) - basis, "copied_source_trade")
                        remaining = held_qty - qty
                        if remaining <= 0:
                            self.conn.execute("DELETE FROM positions WHERE asset=?", (asset,))
                        else:
                            self.conn.execute("UPDATE positions SET shares=?,cost_usd=?,last_price=?,source_shares=? WHERE asset=?",
                                (str(remaining), str(D(held[4]) - basis), str(px),
                                 str(max(D(0), tracked_source - D(source_size))), asset))
                self.conn.execute("UPDATE copy_events SET status='DONE' WHERE event_key=?", (event_key,))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        cash = D(self.get("cash_usd"))
        marked = cash
        unrealized = D(0)
        for asset, row in self.positions().items():
            try:
                mark = self.price(asset, "SELL")
            except (httpx.HTTPError, RuntimeError):
                mark = D(row[5])
                LOG.warning("Using stale mark for %s", asset)
            marked += D(row[3]) * mark
            unrealized += D(row[3]) * mark - D(row[4])
        self.conn.execute("INSERT INTO observations VALUES (?,?,?,?)",
                          (utcnow(), str(cash), str(marked), str(unrealized)))
        LOG.info("Cash=%s equity=%s realized=%s", cash, marked, self.get("realized_usd"))

    def run(self, once=False):
        while True:
            try:
                self.tick()
            except Exception:
                LOG.exception("Tick failed; retrying without placing real orders")
            if once:
                break
            time.sleep(self.poll)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = Worker(os.environ["COPY_WALLET"], os.getenv("COPY_DB", "data/copy_bigggggggg.sqlite"),
                    D(os.getenv("COPY_INITIAL_AUD", "500")),
                    D(os.getenv("COPY_MAX_POSITION_FRACTION", "0.10")),
                    int(os.getenv("COPY_POLL_SECONDS", "30")))
    worker.run(args.once)


if __name__ == "__main__":
    main()
