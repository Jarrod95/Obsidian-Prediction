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
