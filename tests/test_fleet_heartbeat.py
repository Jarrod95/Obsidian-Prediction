"""Run-specific worker health is not inferred from trade counts."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from obsidian_prediction.fleet_heartbeat import mark
from obsidian_prediction.fleet_registry import validate
from obsidian_prediction.fleet_lifecycle import fingerprint, status, atomic


def worker():
    return validate([{
        "id": "fake_worker", "enabled": True,
        "module": "obsidian_prediction._fleet_test_worker",
        "args": ["run", "--mode", "paper"]
    }])[0]


def owned(tmp_path):
    entry = worker()
    atomic(tmp_path / "fake_worker.json", {
        "pid": 123, "created": 100, "interpreter": "/fake/python",
        "module": entry.module, "args_sha256": fingerprint(entry.module, entry.args),
        "run_id": "the-run",
    })
    return entry


def test_heartbeat_is_scoped_to_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_FLEET_WORKER_ID", "fake_worker")
    monkeypatch.setenv("OBSIDIAN_FLEET_RUN_ID", "the-run")
    monkeypatch.setenv("OBSIDIAN_FLEET_RUNTIME", str(tmp_path))
    mark(last_scan_started=datetime.now(timezone.utc).isoformat(), discovered=0)
    mark(last_scan_completed=datetime.now(timezone.utc).isoformat(),
         evaluated=100, idle_reason="no eligible signals")
    import json
    state = json.loads((tmp_path / "fake_worker.heartbeat.json").read_text())
    assert state["run_id"] == "the-run"
    assert state["discovered"] == 0
    assert state["evaluated"] == 100


def test_alive_process_without_scan_not_ready(tmp_path):
    entry = owned(tmp_path)
    fake = Mock(pid=123)
    with patch("obsidian_prediction.fleet_lifecycle.identity", return_value=fake):
        assert status(entry, tmp_path)["state"] == "ALIVE_NOT_READY"


def test_stale_scan_degraded_and_idle_not_failed(tmp_path):
    entry = owned(tmp_path)
    fake = Mock(pid=123)
    old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    atomic(tmp_path / "fake_worker.heartbeat.json", {
        "run_id": "the-run", "updated": old,
        "last_scan_completed": old, "idle_reason": "empty book"
    })
    with patch("obsidian_prediction.fleet_lifecycle.identity", return_value=fake):
        assert status(entry, tmp_path)["state"] == "DEGRADED"
    latest = datetime.now(timezone.utc).isoformat()
    atomic(tmp_path / "fake_worker.heartbeat.json", {
        "run_id": "the-run", "updated": latest,
        "last_scan_completed": latest, "idle_reason": "no eligible signals",
        "entered": 0
    })
    with patch("obsidian_prediction.fleet_lifecycle.identity", return_value=fake):
        assert status(entry, tmp_path)["state"] == "IDLE"
