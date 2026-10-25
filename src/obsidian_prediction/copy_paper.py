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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

LOG = logging.getLogger("copy_paper")
D = lambda x: Decimal(str(x))
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


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
        rows = []
        offset = 0
        while True:
            page = self.api(DATA, "/positions", {"user": self.wallet, "limit": 500,
                                                 "offset": offset, "sizeThreshold": 0})
            if not isinstance(page, list):
                raise RuntimeError("Unexpected positions response; refusing to trade")
            rows.extend(page)
            if len(page) < 500:
                break
            offset += 500
            if offset > 20000:
                raise RuntimeError("Source pagination exceeded safe bound")
        return {str(p["asset"]): p for p in rows if D(p.get("size", 0)) > 0}

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
        source = self.source()  # fetch completely before changing state
        holdings = self.positions()
        if self.get("initialized") == "0":
            # Begin flat; don't retroactively buy every historic position.
            self.set("initialized", "1")
            self.set("baseline", json.dumps({a: str(p["size"]) for a, p in source.items()}))
            LOG.info("Initialized baseline of %d source holdings; waiting for changes", len(source))
            return
        baseline = json.loads(self.get("baseline"))
        # Resolve paper positions from public settlement flags, even if source holdings disappear.
        for asset, row in list(holdings.items()):
            item = source.get(asset, {})
            if not item:
                try:
                    resolved = self.api(DATA, "/positions", {"user": self.wallet, "limit": 500, "sizeThreshold": 0})
                    item = next((p for p in resolved if str(p.get("asset")) == asset), {})
                except httpx.HTTPError:
                    item = {}
            if not item.get("redeemable"):
                continue
            # Redeemable does not alone prove the outcome: require explicit outcome payout.
            payout = item.get("payout")
            if payout is None or D(payout) not in (D(0), D(1)):
                LOG.warning("Settlement pending verified payout for %s", asset)
                continue
            qty, basis = D(row[3]), D(row[4])
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.fill(asset, "SELL", qty, D(payout), qty * D(payout) - basis, "resolved")
                self.conn.execute("DELETE FROM positions WHERE asset=?", (asset,))
                self.conn.execute("INSERT OR REPLACE INTO settled VALUES (?,?,?)", (asset, str(payout), utcnow()))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        holdings = self.positions()
        # A source reduction triggers a proportional paper sale; disappearance closes all.
        for asset, row in holdings.items():
            src_size = D(source.get(asset, {}).get("size", 0))
            previous = D(row[6])
            held = D(row[3])
            if src_size < previous and previous > 0:
                qty = held * (previous - src_size) / previous
                try:
                    px = self.price(asset, "SELL")
                except (httpx.HTTPError, RuntimeError) as exc:
                    LOG.warning("Sell deferred %s: %s", asset, exc)
                    continue
                basis = D(row[4]) * qty / held
                pnl = qty * px - basis
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    self.fill(asset, "SELL", qty, px, pnl, "source_reduced")
                    if qty >= held:
                        self.conn.execute("DELETE FROM positions WHERE asset=?", (asset,))
                    else:
                        self.conn.execute("UPDATE positions SET shares=?,cost_usd=?,last_price=?,source_shares=? WHERE asset=?",
                                          (str(held - qty), str(D(row[4]) - basis), str(px), str(src_size), asset))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
        # New source additions are sized against current available paper equity.
        holdings = self.positions()
        cash = D(self.get("cash_usd"))
        equity = cash
        for asset, row in holdings.items():
            try:
                equity += D(row[3]) * self.price(asset, "SELL")
            except (httpx.HTTPError, RuntimeError):
                equity += D(row[3]) * D(row[5])  # stale mark flagged in logs
                LOG.warning("Using stale mark for %s", asset)
        for asset, item in source.items():
            new_size = D(item["size"])
            previous = D(baseline.get(asset, "0"))
            if new_size <= previous or self.conn.execute("SELECT 1 FROM settled WHERE asset=?", (asset,)).fetchone():
                continue
            try:
                px = self.price(asset, "BUY")
            except (httpx.HTTPError, RuntimeError) as exc:
                LOG.warning("Buy deferred %s: %s", asset, exc)
                continue
            # Fixed fraction of *current* equity; never borrow or spend more than cash.
            allocation = min(equity * self.max_fraction, D(self.get("cash_usd")))
            if allocation < D(os.getenv("COPY_MIN_TRADE_USD", "1")):
                continue
            fee = D(os.getenv("COPY_FEE_RATE", "0"))
            qty = allocation / (px * (1 + fee))
            old = self.positions().get(asset)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.fill(asset, "BUY", qty, px, D(0), "source_increased")
                shares = qty + (D(old[3]) if old else 0)
                cost = allocation + (D(old[4]) if old else 0)
                self.conn.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?)",
                                  (asset, item.get("conditionId", ""), item.get("outcome", ""),
                                   str(shares), str(cost), str(px), str(new_size), utcnow()))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        # Store only fully processed source snapshot; retry failed executions next tick.
        # For held positions retain their last processed source size when a sale was deferred.
        current = self.positions()
        updated = {a: str(p["size"]) for a, p in source.items()}
        for asset, row in current.items():
            # The baseline must not advance past failed paper fills.
            if asset in source and D(row[6]) != D(source[asset]["size"]):
                updated[asset] = row[6]
        self.set("baseline", json.dumps(updated))
        cash = D(self.get("cash_usd"))
        marked = cash
        for asset, row in current.items():
            try:
                marked += D(row[3]) * self.price(asset, "SELL")
            except (httpx.HTTPError, RuntimeError):
                marked += D(row[3]) * D(row[5])
        self.conn.execute("INSERT INTO observations VALUES (?,?,?,?)",
                          (utcnow(), str(cash), str(marked), str(marked - cash - sum(D(r[4]) for r in current.values()))))
        LOG.info("Paper cash USD=%s equity USD=%s realized USD=%s", cash, marked, self.get("realized_usd"))

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
