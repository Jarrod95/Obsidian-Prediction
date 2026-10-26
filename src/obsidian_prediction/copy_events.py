"""Durable source-trade journal for paper copying. No live trading credentials."""
import hashlib
from decimal import Decimal

def key(row):
    raw = "|".join(str(row.get(k, "")) for k in
                   ("transactionHash", "timestamp", "asset", "side", "size", "price"))
    return hashlib.sha256(raw.encode()).hexdigest()

def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS copy_events (
      event_key TEXT PRIMARY KEY, ts INTEGER NOT NULL, asset TEXT NOT NULL,
      condition_id TEXT, outcome TEXT, side TEXT NOT NULL,
      source_size TEXT NOT NULL, source_price TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'PENDING', error TEXT)""")

def fetch_activity(http, wallet, since, limit=500):
    """Read every TRADE in an overlapping window; fail closed if truncated."""
    rows = []
    offset = 0
    while True:
        response = http.get("https://data-api.polymarket.com/activity",
                            params={"user": wallet, "type": "TRADE", "start": max(0, since - 120),
                                    "limit": limit, "offset": offset, "sortDirection": "ASC"})
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise RuntimeError("Invalid Polymarket activity response")
        rows.extend(page)
        if len(page) < limit:
            return sorted(rows, key=lambda x: (int(x["timestamp"]), key(x)))
        offset += len(page)
        if offset >= 5000:
            raise RuntimeError("Activity window exceeded pagination cap; reduce polling interval")

def ingest(conn, rows):
    """Idempotent journal insertion; never discard pending records on failure."""
    for row in rows:
        if str(row.get("type", "")).upper() != "TRADE":
            continue
        side = str(row.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            continue
        asset = str(row.get("asset", ""))
        if not asset or Decimal(str(row.get("size", 0))) <= 0:
            continue
        conn.execute("""INSERT OR IGNORE INTO copy_events
            (event_key,ts,asset,condition_id,outcome,side,source_size,source_price)
            VALUES (?,?,?,?,?,?,?,?)""",
            (key(row), int(row["timestamp"]), asset, row.get("conditionId", ""),
             row.get("outcome", ""), side, str(row["size"]), str(row.get("price", 0))))
