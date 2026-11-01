"""Offline lifecycle safety tests. No Polymarket connections or secrets."""
import json
import os
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from obsidian_prediction.fleet_registry import validate
from obsidian_prediction.fleet_controller import decision
from obsidian_prediction.fleet_lifecycle import (
    atomic, desired, fingerprint, identity, read, report, set_desired, status,
)


def worker():
    return validate([{
        "id": "fake_worker", "enabled": True,
        "module": "obsidian_prediction.fake_worker",
        "args": ["run", "--mode", "paper", "--interval", "5"],
    }])[0]


def test_atomic_read_and_corrupt_record(tmp_path):
    target = tmp_path / "state.json"
    atomic(target, {"hello": "world"})
    assert read(target) == {"hello": "world"}
    target.write_text("{broken", encoding="utf-8")
    assert read(target) is None


def test_desired_stop_persists(tmp_path):
    entry = worker()
    assert desired(entry, tmp_path)
    set_desired(entry, tmp_path, False)
    assert not desired(entry, tmp_path)
    assert json.loads((tmp_path / "fake_worker.desired.json").read_text())["running"] is False


def test_pid_recycle_does_not_match(tmp_path):
    entry = worker()
    fake = Mock()
    fake.create_time.return_value = 100.0
    with patch("obsidian_prediction.fleet_lifecycle.psutil.Process", return_value=fake):
        assert identity(entry, {"pid": 123, "created": 900.0}) is None


def test_command_line_mismatch_does_not_match():
    entry = worker()
    fake = Mock()
    fake.create_time.return_value = 100.0
    fake.exe.return_value = os.path.realpath(__import__("sys").executable)
    fake.cmdline.return_value = ["python", "-u", "-m", "obsidian_prediction.other", "run"]
    with patch("obsidian_prediction.fleet_lifecycle.psutil.Process", return_value=fake):
        assert identity(entry, {
            "pid": 123, "created": 100.0,
            "interpreter": os.path.realpath(__import__("sys").executable),
            "args_sha256": fingerprint(entry.module, entry.args),
        }) is None


def test_operator_stopped_cannot_restart():
    allowed, state = decision({}, 1000, False, "STOPPED")
    assert not allowed
    assert state["reason"] == "operator stopped"


def test_restart_budget_is_persistent_and_quarantined():
    hist = {"events": [100, 200, 300, 400, 500]}
    allowed, state = decision(hist, 600, True, "STOPPED")
    assert not allowed
    assert state["quarantined"]
    # State roundtrips through JSON as it would across controller restarts.
    assert not decision(json.loads(json.dumps(state)), 601, True, "UNKNOWN")[0]


def test_backoff_prevents_immediate_respawn():
    assert not decision({"events": [100]}, 100.5, True, "STOPPED")[0]
    assert decision({"events": [100]}, 103, True, "STOPPED")[0]


def test_active_idle_is_not_a_crash():
    for state in ("HEALTHY", "IDLE", "DEGRADED", "ALIVE_NOT_READY"):
        assert not decision({}, 1000, True, state)[0]


def test_state_does_not_claim_readiness_without_heartbeat(tmp_path):
    entry = worker()
    assert status(entry, tmp_path)["state"] == "STOPPED"


def test_report_never_conflates_alive_with_ready():
    entry = worker()
    summary = report([entry], [{
        "id": entry.id, "state": "ALIVE_NOT_READY", "reason": "no heartbeat"
    }])
    assert summary["alive"] == [entry.id]
    assert summary["ready"] == []
    assert summary["degraded"] == [entry.id]
