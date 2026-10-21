import sqlite3
from decimal import Decimal as D
from obsidian_prediction.parity_paper import db_open
from obsidian_prediction.paper_account import migrate, sizing, state, record_entry, settle, resolved_winner

def test_account_compounds_and_settles(tmp_path):
    db=db_open(tmp_path/"paper.sqlite3")
    migrate(db, "5000")
    assert sizing(db)[0]==D("100")
    with db:
        db.execute("""INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("sig","2026-10-21","123","Test market","1","2","{}","{}","1.5","100","99","148.5","1","1.5","1","CANDIDATE","candidate"))
        ok=record_entry(db,basket_id="b1",signal_id="sig",market_id="123",question="Test market",
            pairs=D("100"),cost_usd=D("66"),cost_aud=D("99"),expected_profit_aud=D("51"),
            fx=D("1.5"),yes_token="1",no_token="2",yes_book={},no_book={})
        assert ok
    assert state(db)["cash"]==D("4901")
    market={"id":"123","closed":True,"outcomes":'["Yes","No"]',"outcomePrices":'["1","0"]'}
    with db:
        assert settle(db,"b1",market,D("1.5"))
    assert state(db)["equity"]==D("5051")
    assert state(db)["realized"]==D("51")
    assert sizing(db)[0]==D("101.02")
    with db:
        assert not settle(db,"b1",market,D("1.5"))
    assert db.execute("SELECT COUNT(*) FROM telegram_outbox").fetchone()[0]==1
    settlement=db.execute("SELECT payout_usd,payout_aud,pnl_usd,pnl_aud,pnl_pct FROM paper_settlements WHERE basket_id=?",("b1",)).fetchone()
    assert tuple(map(D,settlement)) == (D("100"),D("150"),D("34"),D("51"),D("51")/D("99")*100)
    assert db.execute("SELECT COUNT(*) FROM paper_ledger").fetchone()[0]==3

def test_unresolved_not_paid():
    assert resolved_winner({"closed":False,"outcomes":["Yes","No"],"outcomePrices":["1","0"]}) is None
    assert resolved_winner({"closed":True,"outcomes":["Yes","No"],"outcomePrices":["0.5","0.5"]}) is None

def test_legacy_open_position_migration(tmp_path):
    db=db_open(tmp_path/"legacy.sqlite3")
    db.execute("""INSERT INTO baskets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               ("b","s","123","2026-10-20","OPEN_HEDGED","100","66","99","1",None,None,None))
    db.commit()
    migrate(db, "5000")
    assert state(db)["cash"]==D("4901")
    assert state(db)["equity"]==D("5000")
