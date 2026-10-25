from decimal import Decimal
import pytest
from obsidian_prediction.copy_paper import Worker


def test_rejects_invalid_wallet(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    with pytest.raises(ValueError):
        Worker("0xnot-a-wallet", str(tmp_path / "a.sqlite"))


def test_paper_cash_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    path = str(tmp_path / "copy.sqlite")
    wallet = "0x" + "a" * 40
    first = Worker(wallet, path)
    assert Decimal(first.get("cash_usd")) == Decimal("325.00")
    first.set("cash_usd", "275")
    first.conn.close()
    second = Worker(wallet, path)
    assert Decimal(second.get("cash_usd")) == Decimal("275")
    second.conn.close()


def test_buy_and_sell_cash_recycles(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    worker = Worker("0x" + "b" * 40, str(tmp_path / "b.sqlite"))
    worker.fill("token", "BUY", Decimal("10"), Decimal("0.4"), Decimal(0), "test")
    assert Decimal(worker.get("cash_usd")) == Decimal("321")
    worker.fill("token", "SELL", Decimal("10"), Decimal("0.6"), Decimal("2"), "test")
    assert Decimal(worker.get("cash_usd")) == Decimal("327")
    assert Decimal(worker.get("realized_usd")) == Decimal("2")
    worker.conn.close()


def test_winning_market_settles_and_compounds(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    worker = Worker("0x" + "c" * 40, str(tmp_path / "winner.sqlite"))
    worker.set("initialized", "1")
    worker.set("baseline", '{"token": "10"}')
    worker.conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?)",
                        ("token", "condition", "Yes", "10", "4", "0.4", "10", "now"))
    worker.set("cash_usd", "321")
    monkeypatch.setattr("obsidian_prediction.copy_paper.fetch_activity", lambda *args: [])
    worker.set("event_watermark", "100")
    worker.resolution = lambda condition, asset: Decimal("1")
    worker.tick()
    assert Decimal(worker.get("cash_usd")) == Decimal("331")
    assert Decimal(worker.get("realized_usd")) == Decimal("6")
    assert not worker.positions()
    worker.tick()
    assert Decimal(worker.get("cash_usd")) == Decimal("331")
    worker.conn.close()


def test_losing_market_settles_to_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    worker = Worker("0x" + "d" * 40, str(tmp_path / "loser.sqlite"))
    worker.set("initialized", "1")
    worker.set("baseline", '{"token": "10"}')
    worker.conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?)",
                        ("token", "condition", "No", "10", "4", "0.4", "10", "now"))
    worker.set("cash_usd", "321")
    monkeypatch.setattr("obsidian_prediction.copy_paper.fetch_activity", lambda *args: [])
    worker.set("event_watermark", "100")
    worker.resolution = lambda condition, asset: Decimal("0")
    worker.tick()
    assert Decimal(worker.get("cash_usd")) == Decimal("321")
    assert Decimal(worker.get("realized_usd")) == Decimal("-4")
    assert not worker.positions()
    worker.conn.close()


def test_resolution_requires_unambiguous_market(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    worker = Worker("0x" + "e" * 40, str(tmp_path / "market.sqlite"))
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return [{"conditionId": "condition", "closed": True,
                     "clobTokenIds": '["yes","no"]', "outcomePrices": '["1","0"]'}]
    worker.http.get = lambda *args, **kwargs: Response()
    assert worker.resolution("condition", "yes") == Decimal("1")
    assert worker.resolution("condition", "no") == Decimal("0")
    assert worker.resolution("condition", "unknown") is None
    worker.conn.close()


def test_source_trade_replay_is_idempotent(tmp_path, monkeypatch):
    from obsidian_prediction.copy_events import ingest
    monkeypatch.setenv("COPY_AUDUSD", "0.65")
    worker = Worker("0x" + "f" * 40, str(tmp_path / "events.sqlite"))
    event = {"type": "TRADE", "timestamp": 1234, "transactionHash": "0xabc",
             "asset": "token", "conditionId": "condition", "outcome": "Yes",
             "side": "BUY", "size": 10, "price": 0.5}
    ingest(worker.conn, [event, event])
    assert worker.conn.execute("SELECT COUNT(*) FROM copy_events").fetchone()[0] == 1
    worker.set("event_watermark", "100")
    monkeypatch.setattr("obsidian_prediction.copy_paper.fetch_activity", lambda *args: [event])
    worker.resolution = lambda *args: None
    worker.price = lambda *args: Decimal("0.5")
    worker.tick()
    balance = worker.get("cash_usd")
    worker.tick()
    assert worker.get("cash_usd") == balance
    assert worker.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    worker.conn.close()
