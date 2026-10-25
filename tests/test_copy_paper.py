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
