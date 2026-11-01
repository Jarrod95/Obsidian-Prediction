"""Bonereaper source watermark correctness; offline and no wallet required."""
from obsidian_prediction.bonereaper_worker import PaperBot


def test_watermark_advances_after_durable_event_processing(monkeypatch):
    bot = object.__new__(PaperBot)
    state = {"watermark": 100}
    seen = []
    bot.get = lambda key: state.get(key)
    bot.set = lambda key, value: state.__setitem__(key, value)
    bot.settle = lambda: None
    bot.activity = lambda since: [
        {"timestamp": 102, "id": "two"},
        {"timestamp": 101, "id": "one"},
    ]
    bot.handle = lambda event: seen.append(event["id"])
    bot.tick()
    assert seen == ["one", "two"]
    assert state["watermark"] == 102
    assert state["last_poll_success"]


def test_watermark_not_advanced_when_handler_crashes():
    bot = object.__new__(PaperBot)
    state = {"watermark": 100}
    bot.get = lambda key: state.get(key)
    bot.set = lambda key, value: state.__setitem__(key, value)
    bot.settle = lambda: None
    bot.activity = lambda since: [{"timestamp": 102}]
    def fail(event):
        raise RuntimeError("simulated SQLite transaction failure")
    bot.handle = fail
    import pytest
    with pytest.raises(RuntimeError):
        bot.tick()
    assert state["watermark"] == 100
