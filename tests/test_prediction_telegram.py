"""Offline tests for read-only Telegram reports; no Telegram token/network required."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from obsidian_prediction.prediction_telegram import (
    authorized, connection, in_period, registry, render, settings,
)


def test_authorization_is_fail_closed():
    assert authorized(123, 456, "private", 123, 456)
    assert not authorized(123, 456, "group", 123, 456)
    assert not authorized(124, 456, "private", 123, 456)
    assert not authorized(123, 457, "private", 123, 456)
    assert not authorized("123", 456, "private", 123, 456)


def test_missing_environment_refuses_start(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_USER_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ValueError):
        settings()


def test_registry_rejects_duplicate_and_non_slug(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([{"id": "valid_bot"}, {"id": "valid_bot"}]))
    with pytest.raises(ValueError):
        registry(path)
    path.write_text(json.dumps([{"id": "../secret"}]))
    with pytest.raises(ValueError):
        registry(path)


def test_read_only_database_does_not_create_file(tmp_path):
    missing = tmp_path / "no-such.sqlite3"
    assert connection(missing) is None
    assert not missing.exists()


def test_sydney_window_uses_dst(tmp_path):
    assert in_period("2026-10-21T13:30:00+00:00", "today",
                     datetime(2026, 10, 22, 1, tzinfo=timezone.utc))
    assert not in_period("2026-10-21T12:30:00+00:00", "today",
                         datetime(2026, 10, 22, 1, tzinfo=timezone.utc))


def test_leaderboard_shared_capital_is_unranked(tmp_path):
    path = tmp_path / "paper.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE baskets (id TEXT PRIMARY KEY, signal_id TEXT)")
    conn.execute("CREATE TABLE paper_settlements (basket_id TEXT, pnl_aud TEXT, pnl_pct TEXT, settled_at TEXT)")
    conn.execute("INSERT INTO baskets VALUES ('basket-1','s1')")
    conn.execute("INSERT INTO paper_settlements VALUES ('basket-1','17.25','17.25','2026-10-21T15:00:00+00:00')")
    conn.commit()
    conn.close()
    db = connection(path)
    bots = {"polymarket_yes_no_parity": {"mode": "paper", "enabled": True, "venue": "polymarket"}}
    try:
        result = render("leaderboard", ["all", "month"], bots, db, path)
        assert "UNRANKED" in result
        assert "17.25" in result
        assert "LIVE: NOT CONFIGURED" in result
        assert "account return N/A" in result
        assert "Rank 1" not in result
    finally:
        db.close()


def test_unknown_bot_never_interpreted_as_account(tmp_path):
    bots = {"known_bot": {"mode": "paper", "enabled": True, "venue": "polymarket"}}
    assert "Unknown bot" in render("positions", ["123456789"], bots, None, tmp_path)
    assert "NOT AVAILABLE" in render("account", ["known_bot"], bots, None, tmp_path)
