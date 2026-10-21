"""Read-only Polymarket parity shadow-paper runner. Never sends orders."""
import argparse
import asyncio
import csv
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .exchanges import PublicPolymarket
from .fx import FXProvider
from .markets import Book, Level, parity
from .paper_account import migrate, sizing, state, record_entry
from .settlement import reconcile, deliver

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

def closing_at(market):
    """Return an explicit Gamma market end timestamp, or None if unknown."""
    for key in ("endDate", "end_date_iso", "endDateIso"):
        value = market.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def prioritize_markets(markets, now, horizon_hours):
    """Sort near-closing eligible markets first; do not mistake closure for settlement."""
    cutoff = now + timedelta(hours=horizon_hours)
    selected = []
    for market in markets:
        if tokens(market) is None:
            continue
        end = closing_at(market)
        if end is None or not now < end <= cutoff:
            continue
        selected.append((end, market))
    selected.sort(key=lambda item: item[0])
    return [market for _, market in selected]


async def scan(client, db, args):
    """Bounded parallel I/O; SQLite writes remain serialized on the event loop."""
    started = time.monotonic()
    discovered = []
    async for market in client.markets(limit=args.limit, max_pages=args.pages):
        discovered.append(market)
        if len(discovered) >= args.discovery_limit:
            break
    markets = prioritize_markets(discovered, utc(), args.closing_within_hours)
    if not args.strict_closing:
        prioritized_ids = {str(m.get("id")) for m in markets}
        markets.extend(m for m in discovered if str(m.get("id")) not in prioritized_ids)
    markets = markets[:args.max_markets]
    print(f"Discovery: {len(discovered)} markets; {len(prioritize_markets(discovered, utc(), args.closing_within_hours))} eligible closing within {args.closing_within_hours:g}h; scanning {len(markets)}")
    semaphore = asyncio.Semaphore(args.concurrency)
    stats = {"eligible": 0, "books": 0, "rejected": 0, "candidates": 0,
             "entered": 0, "errors": 0}
    top = []

    async def fetch(market):
        mapping = tokens(market)
        if mapping is None:
            return (market, None, None, None, "ineligible")
        async with semaphore:
            try:
                yes_raw, no_raw = await asyncio.gather(
                    client.orderbook(mapping["YES"]), client.orderbook(mapping["NO"]))
                return (market, mapping, yes_raw, no_raw, None)
            except Exception as exc:
                return (market, mapping, None, None, f"{type(exc).__name__}: {exc}")

    tasks = [asyncio.create_task(fetch(m)) for m in markets]
    for task in asyncio.as_completed(tasks):
        market, mapping, yes_raw, no_raw, error = await task
        if mapping is None:
            continue
        stats["eligible"] += 1
        if error:
            stats["errors"] += 1
            if args.verbose:
                print("Book fetch failed:", market.get("id"), error[:180])
            continue
        stats["books"] += 1
        try:
            now = utc()
            yes = book(mapping["YES"], yes_raw, now)
            no = book(mapping["NO"], no_raw, now)
            budget, slots, count = sizing(db)
            op = evaluate(yes, no, D(args.fx_aud_per_usd), budget,
                          D(args.fee_rate), now)
            cost = (op.cost + op.fees + op.slippage) if op else D(0)
            edge = op.net_edge / cost * 100 if op and cost else D(0)
            reason = "insufficient_depth_or_budget" if op is None else (
                "below_threshold" if edge < D(args.min_edge_pct) else "candidate")
            market_id = str(market.get("id", ""))
            sid = str(uuid.uuid4())
            decision = "REJECTED" if reason != "candidate" else "CANDIDATE"
            stats["candidates" if reason == "candidate" else "rejected"] += 1
            if op:
                top.append((edge, market_id, market.get("question", "")[:65]))
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
                    bid = str(uuid.uuid4())
                    entered = record_entry(
                        db, basket_id=bid, signal_id=sid, market_id=market_id,
                        question=market.get("question"), pairs=op.pair_size,
                        cost_usd=cost, cost_aud=cost*D(args.fx_aud_per_usd),
                        expected_profit_aud=op.net_edge*D(args.fx_aud_per_usd),
                        fx=D(args.fx_aud_per_usd), yes_token=mapping["YES"],
                        no_token=mapping["NO"], yes_book=yes_raw, no_book=no_raw)
                    if entered:
                        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (str(uuid.uuid4()), now.isoformat(), bid, "OPTIMISTIC_PAPER_ENTRY",
                             json.dumps({"yes": yes_raw, "no": no_raw,
                                         "warning": "snapshot fill only; not execution evidence"})))
                        stats["entered"] += 1
                        print("PAPER ENTRY", market_id, "AUD",
                              cost * D(args.fx_aud_per_usd), "expected edge %", edge)
        except Exception as exc:
            stats["errors"] += 1
            if args.verbose:
                print("Market skipped", market.get("id"), type(exc).__name__, str(exc)[:180])
    elapsed = time.monotonic() - started
    print(f"Scanned {len(markets)} in {elapsed:.1f}s | eligible={stats['eligible']} "
          f"books={stats['books']} candidates={stats['candidates']} "
          f"entered={stats['entered']} rejected={stats['rejected']} errors={stats['errors']}")
    for edge, mid, question in sorted(top, reverse=True)[:args.top]:
        print(f"  top edge={edge:.3f}% market={mid} {question}")

def report(db):
    for table in ("signals", "baskets", "events", "paper_accounts", "paper_ledger", "paper_settlements", "paper_fills", "telegram_outbox"):
        print(table, db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
    print("Open deployed AUD", db.execute("SELECT COALESCE(SUM(CAST(cost_aud AS REAL)),0) FROM baskets WHERE status='OPEN_HEDGED'").fetchone()[0])
    s = state(db)
    print(f"Cash AUD {s['cash']:.2f} | Deployed AUD {s['deployed']:.2f} | Equity AUD {s['equity']:.2f} | Realized P/L AUD {s['realized']:.2f}")

def export(db, output):
    Path(output).mkdir(parents=True, exist_ok=True)
    for table in ("signals", "baskets", "events", "paper_accounts", "paper_ledger", "paper_settlements", "paper_fills", "telegram_outbox"):
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
    p.add_argument("--fx-aud-per-usd", required=False, default=None, help="Optional manual override; otherwise fetch free daily USD/AUD FX")
    p.add_argument("--fx-max-age-days", type=int, default=7)
    p.add_argument("--fx-refresh-seconds", type=int, default=3600)
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
    p.add_argument("--concurrency", type=int, default=12)
    p.add_argument("--discovery-limit", type=int, default=2000)
    p.add_argument("--closing-within-hours", type=float, default=72)
    p.add_argument("--strict-closing", action="store_true")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if not 1 <= args.concurrency <= 32 or args.max_markets < 1 or args.top < 0 or args.discovery_limit < 1 or args.closing_within_hours <= 0:
        p.error("concurrency must be 1..32, max-markets >= 1 and top >= 0")
    db = db_open(args.db)
    migrate(db, initial_capital=args.capital_aud)
    if args.command == "doctor":
        print("read-only API; live orders impossible; db", args.db)
    elif args.command == "report":
        report(db)
    elif args.command == "export":
        export(db, args.output)
    else:
        if args.fx_aud_per_usd is not None and D(args.fx_aud_per_usd) <= 0:
            p.error("--fx-aud-per-usd must be positive")
        if args.fx_max_age_days < 0 or args.fx_refresh_seconds < 60:
            p.error("FX age must be nonnegative and refresh >= 60 seconds")
        async def loop():
            client = PublicPolymarket()
            fx_provider = FXProvider(max_age_days=args.fx_max_age_days, refresh_seconds=args.fx_refresh_seconds)
            try:
                while True:
                    if args.fx_aud_per_usd is None:
                        try:
                            rate, observed = await fx_provider.latest()
                        except Exception as exc:
                            print(f"FX unavailable; skipping cycle: {type(exc).__name__}: {exc}")
                            if args.once:
                                break
                            await asyncio.sleep(max(5, args.interval))
                            continue
                        args.fx_aud_per_usd = str(rate)
                        print(f"FX USD/AUD={rate} | observation={observed} | source=Frankfurter daily reference")
                        try:
                            await reconcile(client, db, D(args.fx_aud_per_usd))
                            await deliver(db)
                            await scan(client, db, args)
                        finally:
                            args.fx_aud_per_usd = None
                    else:
                        await reconcile(client, db, D(args.fx_aud_per_usd))
                        await deliver(db)
                        await scan(client, db, args)
                    if args.once:
                        break
                    await asyncio.sleep(max(5, args.interval))
            finally:
                await client.close()
        asyncio.run(loop())

if __name__ == "__main__":
    main()
