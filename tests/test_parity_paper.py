from datetime import datetime, timezone
from decimal import Decimal
from obsidian_prediction.markets import Book, Level
from obsidian_prediction.parity_paper import evaluate, tokens, db_open

def test_mapping():
    assert tokens({"outcomes": '["Yes","No"]', "clobTokenIds": '["123","456"]', "active": True}) == {"YES":"123","NO":"456"}
    assert tokens({"outcomes": '["Yes","No","Maybe"]', "clobTokenIds": '["1","2","3"]'}) is None

def test_budget():
    now = datetime.now(timezone.utc)
    yes = Book("1", (Level(Decimal("0.46"), Decimal("1000")),), now)
    no = Book("2", (Level(Decimal("0.51"), Decimal("1000")),), now)
    op = evaluate(yes, no, Decimal("1.49"), Decimal("100"), Decimal("0"), now)
    assert op is not None
    assert (op.cost + op.fees) * Decimal("1.49") <= 100
    assert op.pair_size == 69

def test_db(tmp_path):
    db = db_open(tmp_path / "test.sqlite3")
    assert db.execute("SELECT count(*) FROM baskets").fetchone()[0] == 0
