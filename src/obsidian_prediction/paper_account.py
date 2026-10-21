"""Durable paper account and settlement accounting. No real orders."""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

D = Decimal

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def migrate(db, initial_capital="5000"):
    db.execute("""CREATE TABLE IF NOT EXISTS paper_accounts (
        id INTEGER PRIMARY KEY CHECK(id=1), initial_aud TEXT NOT NULL,
        cash_aud TEXT NOT NULL, realized_aud TEXT NOT NULL DEFAULT '0')""")
    db.execute("""CREATE TABLE IF NOT EXISTS paper_ledger (
        id TEXT PRIMARY KEY, basket_id TEXT, kind TEXT NOT NULL, amount_aud TEXT NOT NULL,
        balance_aud TEXT NOT NULL, timestamp TEXT NOT NULL,
        UNIQUE(basket_id, kind))""")
    db.execute("""CREATE TABLE IF NOT EXISTS paper_settlements (
        basket_id TEXT PRIMARY KEY, market_id TEXT NOT NULL, winner TEXT NOT NULL,
        payout_usd TEXT NOT NULL, payout_aud TEXT NOT NULL,
        pnl_usd TEXT NOT NULL, pnl_aud TEXT NOT NULL, pnl_pct TEXT NOT NULL,
        fx_aud_per_usd TEXT NOT NULL, settled_at TEXT NOT NULL, evidence TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS telegram_outbox (
        basket_id TEXT PRIMARY KEY, message TEXT NOT NULL, delivered_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS paper_fills (
        id TEXT PRIMARY KEY, basket_id TEXT NOT NULL, side TEXT NOT NULL,
        token_id TEXT NOT NULL, units TEXT NOT NULL, book_snapshot TEXT NOT NULL,
        UNIQUE(basket_id, side))""")
    for column, typ in [("market_question","TEXT"),("entry_fx","TEXT")]:
        cols = {row[1] for row in db.execute("PRAGMA table_info(baskets)")}
        if column not in cols:
            db.execute(f"ALTER TABLE baskets ADD COLUMN {column} {typ}")
    if db.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0] == 0:
        old = db.execute("SELECT COUNT(*) FROM baskets").fetchone()[0]
        if old:
            raise RuntimeError("Existing baskets require explicit reconciliation before initializing account; refusing to silently reset balance")
        db.execute("INSERT INTO paper_accounts(id,initial_aud,cash_aud,realized_aud) VALUES(1,?,?,?)",
                   (str(initial_capital), str(initial_capital), "0"))
        db.execute("INSERT INTO paper_ledger VALUES(?,?,?,?,?,?)",
                   (str(uuid.uuid4()), None, "INITIAL_DEPOSIT", str(initial_capital), str(initial_capital), now_iso()))
    db.commit()

def state(db):
    row = db.execute("SELECT initial_aud,cash_aud,realized_aud FROM paper_accounts WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Paper account not initialized")
    deployed = sum((D(r[0]) for r in db.execute("SELECT cost_aud FROM baskets WHERE status='OPEN_HEDGED'")), D(0))
    return {"initial":D(row[0]), "cash":D(row[1]), "realized":D(row[2]),
            "deployed":deployed, "equity":D(row[1])+deployed}

def sizing(db, fraction=D("0.02"), max_exposure=D("0.10"), max_positions=50):
    s = state(db)
    slots = min(max_positions, max(1, int(s["equity"] / D("1000"))))
    count = db.execute("SELECT COUNT(*) FROM baskets WHERE status='OPEN_HEDGED'").fetchone()[0]
    # Gradually allow more capital at work as equity grows, capped at 30%.
    exposure = min(D("0.30"), max_exposure + D(max(0, int((s["equity"]-D("5000")) / D("5000")))) * D("0.025"))
    capacity = max(D(0), min(s["cash"], s["equity"]*exposure-s["deployed"]))
    return max(D(0), min(s["equity"]*fraction, capacity)), slots, count

def record_entry(db, *, basket_id, signal_id, market_id, question, pairs, cost_usd,
                 cost_aud, expected_profit_aud, fx, yes_token, no_token, yes_book, no_book,
                 fraction=D("0.02"), max_exposure=D("0.10")):
    budget, slots, count = sizing(db, fraction, max_exposure)
    if cost_aud <= 0 or cost_aud > budget or count >= slots:
        return False
    if db.execute("SELECT 1 FROM baskets WHERE market_id=? AND status='OPEN_HEDGED'", (market_id,)).fetchone():
        return False
    ts = now_iso()
    db.execute("""INSERT INTO baskets
        (id,signal_id,market_id,opened_at,status,pairs,cost_usd,cost_aud,
         expected_profit_aud,realized_profit_usd,realized_profit_aud,closed_at,
         market_question,entry_fx)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (basket_id,signal_id,market_id,ts,"OPEN_HEDGED",str(pairs),str(cost_usd),
         str(cost_aud),str(expected_profit_aud),None,None,None,question,str(fx)))
    for side, token, book in (("YES",yes_token,yes_book),("NO",no_token,no_book)):
        db.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?)",
                   (str(uuid.uuid4()),basket_id,side,token,str(pairs),json.dumps(book)))
    s = state(db)
    balance = s["cash"]-cost_aud
    db.execute("UPDATE paper_accounts SET cash_aud=? WHERE id=1", (str(balance),))
    db.execute("INSERT INTO paper_ledger VALUES(?,?,?,?,?,?)",
               (str(uuid.uuid4()),basket_id,"ENTRY",str(-cost_aud),str(balance),ts))
    return True

def resolved_winner(market):
    """Only settle an explicitly closed, binary market with unambiguous one-hot payout."""
    if market.get("closed") not in (True,"true"):
        return None
    outcomes = market.get("outcomes", [])
    prices = market.get("outcomePrices", [])
    if isinstance(outcomes,str):
        outcomes=json.loads(outcomes)
    if isinstance(prices,str):
        prices=json.loads(prices)
    if len(outcomes)!=2 or len(prices)!=2 or set(map(str.upper,outcomes))!={"YES","NO"}:
        return None
    if [D(str(x)) for x in prices] not in ([D(1),D(0)],[D(0),D(1)]):
        return None
    return str(outcomes[0]).upper() if D(str(prices[0]))==1 else str(outcomes[1]).upper()

def settle(db, basket_id, market, fx):
    winner = resolved_winner(market)
    if winner is None:
        return False
    row=db.execute("""SELECT market_id,market_question,pairs,cost_usd,cost_aud,status
                      FROM baskets WHERE id=?""",(basket_id,)).fetchone()
    if not row or row[5]!="OPEN_HEDGED" or str(market.get("id"))!=str(row[0]):
        return False
    units,cost_usd,cost_aud=D(row[2]),D(row[3]),D(row[4])
    payout_usd=units
    payout_aud=payout_usd*fx
    pnl_usd=payout_usd-cost_usd
    pnl_aud=payout_aud-cost_aud
    pnl_pct=pnl_aud/cost_aud*100
    ts=now_iso()
    # Called within one SQLite transaction by the caller.
    db.execute("""INSERT INTO paper_settlements VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (basket_id,row[0],winner,str(payout_usd),str(payout_aud),
                str(pnl_usd),str(pnl_aud),str(pnl_pct),str(fx),ts,json.dumps(market)))
    db.execute("""UPDATE baskets SET status='SETTLED',closed_at=?,
                  realized_profit_usd=?,realized_profit_aud=? WHERE id=?""",
               (ts,str(pnl_usd),str(pnl_aud),basket_id))
    cash=state(db)["cash"]+payout_aud
    realized=state(db)["realized"]+pnl_aud
    db.execute("UPDATE paper_accounts SET cash_aud=?, realized_aud=? WHERE id=1",
               (str(cash),str(realized)))
    db.execute("INSERT INTO paper_ledger VALUES(?,?,?,?,?,?)",
               (str(uuid.uuid4()),basket_id,"SETTLEMENT",str(payout_aud),str(cash),ts))
    message=(f"Position Closed\nMarket: {row[1] or row[0]}\n"
             f"Amount wagered: A${cost_aud:.2f}\n"
             f"P/L: A${pnl_aud:+.2f} ({pnl_pct:+.2f}%)\n"
             f"Current balance: A${state(db)['equity']:.2f}")
    db.execute("INSERT INTO telegram_outbox(basket_id,message) VALUES(?,?)",(basket_id,message))
    return True
