"""Read-only Polymarket parity shadow-paper runner. Never sends orders."""
import argparse
import asyncio
import csv
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .exchanges import PublicPolymarket
from .markets import Book, Level, parity

D = Decimal
BOT = "polymarket_yes_no_parity"

def utc():
    return datetime.now(timezone.utc)

def parse_array(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value

def tokens(market):
    outcomes = parse_array(market.get("outcomes", "[]"))
    ids = parse_array(market.get("clobTokenIds", "[]"))
    if len(outcomes) != 2 or len(ids) != 2 or set(str(x).upper() for x in outcomes) != {"YES", "NO"}:
        return None
    mapping = dict(zip((str(x).upper() for x in outcomes), (str(x) for x in ids)))
    if not all(x.isdecimal() for x in mapping.values()):
        return None
    if market.get("closed") in (True, "true") or market.get("active") in (False, "false"):
        return None
    return mapping

def book(token, raw, received):
    levels = tuple(Level(D(str(x["price"])), D(str(x["size"]))) for x in raw.get("asks", []))
    return Book(token, levels, received)

def db_open(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, market_id TEXT NOT NULL,
        question TEXT, yes_token TEXT, no_token TEXT, yes_book TEXT, no_book TEXT,
        fx_aud_per_usd TEXT, pairs TEXT, cost_usd TEXT, cost_aud TEXT,
        expected_profit_usd TEXT, expected_profit_aud TEXT, edge_pct TEXT,
        decision TEXT NOT NULL, reason TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS baskets (
        id TEXT PRIMARY KEY, signal_id TEXT UNIQUE, market_id TEXT, opened_at TEXT,
        status TEXT, pairs TEXT, cost_usd TEXT, cost_aud TEXT,
        expected_profit_aud TEXT, realized_profit_usd TEXT, realized_profit_aud TEXT,
        closed_at TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, timestamp TEXT, basket_id TEXT, kind TEXT, payload TEXT)""")
    db.commit()
    return db

def evaluate(yes, no, fx, budget, fee_rate, now):
    if fx <= 0:
        return None
    max_pairs = min(
        sum((x.size for x in yes.asks), D(0)),
        sum((x.size for x in no.asks), D(0)),
    )
    hi = int(max_pairs)
    lo = 0
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if mid == 0:
            lo = 1
            continue
        opportunity = parity(yes, no, units=D(mid), fee_rate=fee_rate,
                             max_age_seconds=2, now=now, complementary_verified=True)
        if opportunity is None or (opportunity.cost + opportunity.fees + opportunity.slippage) * fx > budget:
            hi = mid - 1
        else:
            best = opportunity
            lo = mid + 1
    return best

async def scan(client, db, args):
    count = 0
    async for market in client.markets(limit=args.limit, max_pages=args.pages):
        if count >= args.max_markets:
            break
        count += 1
        mapping = tokens(market)
        if mapping is None:
            continue
        try:
            yes_raw, no_raw = await asyncio.gather(
                client.orderbook(mapping["YES"]), client.orderbook(mapping["NO"]))
            now = utc()
            yes = book(mapping["YES"], yes_raw, now)
            no = book(mapping["NO"], no_raw, now)
            op = evaluate(yes, no, D(args.fx_aud_per_usd), D(args.basket_aud),
                          D(args.fee_rate), now)
            cost = (op.cost + op.fees + op.slippage) if op else D(0)
            edge = op.net_edge / cost * 100 if op and cost else D(0)
            reason = "insufficient_depth_or_budget" if op is None else (
                "below_threshold" if edge < D(args.min_edge_pct) else "candidate")
            market_id = str(market.get("id", ""))
            sid = str(uuid.uuid4())
            decision = "REJECTED" if reason != "candidate" else "CANDIDATE"
            with db:
                db.execute("""INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, now.isoformat(), market_id, market.get("question"),
                     mapping["YES"], mapping["NO"], json.dumps(yes_raw), json.dumps(no_raw),
                     args.fx_aud_per_usd, str(op.pair_size) if op else None,
                     str(cost), str(cost * D(args.fx_aud_per_usd)),
                     str(op.net_edge) if op else None,
                     str(op.net_edge * D(args.fx_aud_per_usd)) if op else None,
                     str(edge), decision, reason))
                if args.mode == "paper" and reason == "candidate":
                    open_count = db.execute("SELECT COUNT(*) FROM baskets WHERE status='OPEN_HEDGED'").fetchone()[0]
                    deployed = D(str(db.execute("SELECT COALESCE(SUM(CAST(cost_aud AS REAL)),0) FROM baskets WHERE status='OPEN_HEDGED'").fetchone()[0]))
                    existing = db.execute("SELECT 1 FROM baskets WHERE market_id=? AND status='OPEN_HEDGED'", (market_id,)).fetchone()
                    if open_count < args.max_open and not existing and deployed + cost * D(args.fx_aud_per_usd) <= D(args.capital_aud):
                        bid = str(uuid.uuid4())
                        db.execute("INSERT INTO baskets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (bid, sid, market_id, now.isoformat(), "OPEN_HEDGED",
                             str(op.pair_size), str(cost), str(cost * D(args.fx_aud_per_usd)),
                             str(op.net_edge * D(args.fx_aud_per_usd)), None, None, None))
                        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (str(uuid.uuid4()), now.isoformat(), bid, "OPTIMISTIC_PAPER_ENTRY",
                             json.dumps({"yes": yes_raw, "no": no_raw, "warning": "snapshot fill only; not execution evidence"})))
                        print("PAPER ENTRY", market_id, "AUD", cost * D(args.fx_aud_per_usd), "expected edge %", edge)
        except Exception as exc:
            print("Market skipped", market.get("id"), type(exc).__name__, str(exc)[:180])
    print("Scanned", count, "markets")

def report(db):
    for table in ("signals", "baskets", "events"):
        print(table, db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
    print("Open deployed AUD", db.execute("SELECT COALESCE(SUM(CAST(cost_aud AS REAL)),0) FROM baskets WHERE status='OPEN_HEDGED'").fetchone()[0])
    print("Realized P/L: unavailable until authoritative settlement is implemented")

def export(db, output):
    Path(output).mkdir(parents=True, exist_ok=True)
    for table in ("signals", "baskets", "events"):
        cursor = db.execute("SELECT * FROM " + table)
        with (Path(output) / (table + ".csv")).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([x[0] for x in cursor.description])
            writer.writerows(cursor.fetchall())

def main():
    p = argparse.ArgumentParser(description="Public API parity research and optimistic paper simulation")
    p.add_argument("command", choices=["doctor", "run", "report", "export"])
    p.add_argument("--mode", choices=["observe", "paper"], default="observe")
    p.add_argument("--db", default="runtime/parity_paper.sqlite3")
    p.add_argument("--output", default="runtime/exports")
    p.add_argument("--fx-aud-per-usd", required=False, default=None)
    p.add_argument("--basket-aud", default="100")
    p.add_argument("--capital-aud", default="5000")
    p.add_argument("--min-edge-pct", default="1")
    p.add_argument("--fee-rate", default="0")
    p.add_argument("--max-open", type=int, default=5)
    p.add_argument("--max-markets", type=int, default=100)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    db = db_open(args.db)
    if args.command == "doctor":
        print("read-only API; live orders impossible; db", args.db)
    elif args.command == "report":
        report(db)
    elif args.command == "export":
        export(db, args.output)
    else:
        if args.fx_aud_per_usd is None or D(args.fx_aud_per_usd) <= 0:
            p.error("Explicit positive --fx-aud-per-usd required; not a live FX feed")
        async def loop():
            client = PublicPolymarket()
            try:
                while True:
                    await scan(client, db, args)
                    if args.once:
                        break
                    await asyncio.sleep(max(5, args.interval))
            finally:
                await client.close()
        asyncio.run(loop())

if __name__ == "__main__":
    main()
