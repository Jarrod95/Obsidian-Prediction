import pytest

from obsidian_prediction.config import Settings


def test_fail_closed(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_APPROVED", "false")
    with pytest.raises(RuntimeError):
        Settings.from_environment()


def test_default_paper(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_EXECUTION_APPROVED", "false")
    assert not Settings.from_environment().trading_enabled
