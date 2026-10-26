from decimal import Decimal
import pytest

from obsidian_prediction.bonereaper_worker import PaperBot, event_key


def test_event_hash_is_deterministic_and_split_fills_differ():
    a = {"transactionHash": "0xabc", "size": 1, "timestamp": 123}
    b = {**a, "size": 2}
    assert event_key(a) == event_key(dict(reversed(list(a.items()))))
    assert event_key(a) != event_key(b)


def bot(tmp_path, monkeypatch, **kw):
    from datetime import datetime, timezone
    monkeypatch.setattr("obsidian_prediction.bonereaper_worker.time.time", lambda: 1793010020)
    return PaperBot(str(tmp_path / "paper.sqlite"), "0x" + "a" * 40, "0.65",
                    fx_at=datetime.now(timezone.utc).isoformat(), **kw)


def test_isolated_cash_and_identity(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch)
    assert Decimal(x.get("cash")) == Decimal("325")
    x.set("cash", 200)
    x.conn.close()
    y = bot(tmp_path, monkeypatch)
    assert y.get("cash") == "200"
    y.conn.close()
    with pytest.raises(ValueError, match="identity mismatch"):
        PaperBot(str(tmp_path / "paper.sqlite"), "0x" + "b" * 40, "0.65",
                 fx_at=__import__("datetime").datetime.now(
                     __import__("datetime").timezone.utc).isoformat())


def test_observe_journals_rejected_event_once(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch)
    row = {"type": "TRADE", "asset": "token", "conditionId": "0x" + "a" * 64,
           "timestamp": 1793000000, "size": 1, "price": "0.5", "side": "BUY"}
    x.handle(row)
    x.handle(row)
    assert x.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert x.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    x.conn.close()


def test_feed_rejects_cursor_loop(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch)
    x.request = lambda *args: {"data": [], "pagination": {"next_cursor": "repeat", "has_more": True}}
    with pytest.raises(RuntimeError, match="Repeated"):
        x.activity(1793000000)
    x.conn.close()


def test_malformed_source_event_cannot_block_later_events(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch)
    malformed = {"transactionHash": "0x1", "timestamp": "invalid", "asset": "123",
                 "conditionId": "0x" + "a" * 64, "side": "BUY",
                 "size": "NaN", "price": "bad"}
    x.handle(malformed)
    x.handle({**malformed, "transactionHash": "0x2", "timestamp": 1793010020,
              "size": "1", "price": "0.5", "asset": ""})
    assert x.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert set(r[0] for r in x.conn.execute("SELECT disposition FROM events")) == {
        "REJECTED_SCHEMA"
    }
    x.conn.close()


def test_ambiguity_defers_settlement_and_does_not_mint_cash(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch, observe=False)
    x.conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?)",
                   ("123", "0x" + "a" * 64, "Up", "10", "4", "10"))
    x.set("cash", "321")
    x.metadata = lambda condition: {
        "closed": True, "clobTokenIds": '["123","456"]',
        "outcomePrices": '["0.5","0.5"]'
    }
    x.settle()
    assert x.get("cash") == "321"
    assert len(x.holdings()) == 1
    assert x.conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 0
    x.conn.close()


def test_settlement_exactly_once_on_restart(tmp_path, monkeypatch):
    x = bot(tmp_path, monkeypatch, observe=False)
    x.conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?)",
                   ("123", "0x" + "a" * 64, "Up", "10", "4", "10"))
    x.set("cash", "321")
    data = {"closed": True, "clobTokenIds": '["123","456"]',
            "outcomePrices": '["1","0"]'}
    x.metadata = lambda condition: data
    x.settle()
    x.settle()
    assert Decimal(x.get("cash")) == Decimal("331")
    assert Decimal(x.get("realized")) == Decimal("6")
    assert x.conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 1
    x.conn.close()
    y = bot(tmp_path, monkeypatch, observe=False)
    y.metadata = lambda condition: data
    y.settle()
    assert Decimal(y.get("cash")) == Decimal("331")
    y.conn.close()


def test_missing_fx_validation(tmp_path):
    from datetime import datetime, timezone
    with pytest.raises(ValueError):
        PaperBot(str(tmp_path / "p.sqlite"), "0x" + "a" * 40,
                 "0.65", fx_at="2020-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        PaperBot(str(tmp_path / "q.sqlite"), "0x" + "a" * 40,
                 "NaN", fx_at=datetime.now(timezone.utc).isoformat())
