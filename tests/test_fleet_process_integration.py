"""Real local subprocess integration, no external network/credentials."""
import json
import uuid
import os
import sys
import time

import psutil
import pytest

from obsidian_prediction.fleet_registry import validate
from obsidian_prediction.fleet_lifecycle import identity, spawn, status, stop, set_desired
from obsidian_prediction.fleet_controller import decision


_TEST_RUN_NONCE = uuid.uuid4().hex


def fake(*extra):
    return validate([{
        "id": "fake_worker", "enabled": True,
        "module": "obsidian_prediction._fleet_test_worker",
        "args": ["run", "--mode", "paper", "--test-nonce", _TEST_RUN_NONCE, *extra],
    }])[0]


@pytest.mark.timeout(30)
def test_double_start_same_owner_and_scoped_stop(tmp_path):
    entry = fake()
    original = psutil.Process().pid
    try:
        first = spawn(entry, tmp_path)
        assert first["state"] == "STARTING"
        second = spawn(entry, tmp_path)
        assert second["pid"] == first["pid"]
        assert psutil.pid_exists(first["pid"])
        assert status(entry, tmp_path)["state"] == "ALIVE_NOT_READY"
    finally:
        stopped = stop(entry, tmp_path)
    assert stopped["state"] == "STOPPED"
    assert psutil.pid_exists(original)


@pytest.mark.timeout(30)
def test_stale_pid_record_does_not_kill_unrelated_process(tmp_path):
    entry = fake()
    path = tmp_path / "fake_worker.json"
    path.write_text(json.dumps({"pid": os.getpid(), "created": 0,
                                "interpreter": sys.executable,
                                "args_sha256": "wrong"}), encoding="utf-8")
    assert stop(entry, tmp_path)["state"] == "UNKNOWN"
    assert psutil.pid_exists(os.getpid())


@pytest.mark.timeout(30)
def test_child_tree_shutdown(tmp_path):
    pid_file = tmp_path / "child.pid"
    entry = fake("--spawn-child", "--child-pid-file", str(pid_file))
    try:
        result = spawn(entry, tmp_path)
        deadline = time.monotonic() + 8
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pid_file.is_file(), "fake worker child not spawned"
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        assert psutil.pid_exists(child_pid)
    finally:
        stopped = stop(entry, tmp_path)
    assert stopped["state"] == "STOPPED"
    assert not psutil.pid_exists(result["pid"])
    assert (not psutil.pid_exists(child_pid) or
            psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE)


@pytest.mark.timeout(30)
def test_sudden_exit_not_reported_alive(tmp_path):
    entry = fake("--exit-code", "17")
    result = spawn(entry, tmp_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and psutil.pid_exists(result["pid"]):
        time.sleep(0.1)
    observed = status(entry, tmp_path)
    assert observed["state"] in ("UNKNOWN", "STOPPED")
    assert observed.get("last_exit_code") == 17, observed
    assert observed.get("last_exit_cause") == "system_exit"
    stop(entry, tmp_path)


def test_restart_budget_survives_serialization():
    prior = {"events": [100, 200, 300, 400, 500]}
    persisted = json.loads(json.dumps(prior))
    restart, updated = decision(persisted, 550, True, "STOPPED")
    assert restart is False
    assert updated["quarantined"] is True

def _contender(runtime):
    from pathlib import Path
    from obsidian_prediction.fleet_lifecycle import spawn
    return spawn(fake(), Path(runtime))


@pytest.mark.timeout(40)
def test_concurrent_start_contenders_share_owned_pid(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: _contender(tmp_path), range(4)))
        assert len({r["pid"] for r in results}) == 1
    finally:
        assert stop(fake(), tmp_path)["state"] == "STOPPED"


def test_telegram_not_configured_is_blocked_independently(tmp_path, monkeypatch):
    from obsidian_prediction.fleet_lifecycle import missing_config
    entry = validate([{
        "id": "prediction_telegram", "enabled": True, "kind": "service",
        "module": "obsidian_prediction.prediction_telegram",
        "args": ["--mode", "observe"],
        "requires_env": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "TELEGRAM_CHAT_ID"]
    }])[0]
    for key in entry.requires_env:
        monkeypatch.delenv(key, raising=False)
    # Avoid loading any developer's local .env in this credential-free test.
    monkeypatch.setattr("obsidian_prediction.fleet_lifecycle.load_dotenv", lambda *a, **k: None)
    assert set(missing_config(entry)) == set(entry.requires_env)
    assert missing_config(fake()) == []

@pytest.mark.timeout(30)
def test_unmanaged_direct_worker_is_not_duplicated_or_killed(tmp_path):
    import subprocess
    entry = fake()
    external = subprocess.Popen([sys.executable, "-u", "-m", entry.module, *entry.args],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        from obsidian_prediction.fleet_lifecycle import unmanaged
        while time.monotonic() < deadline and external.pid not in unmanaged(entry):
            time.sleep(0.1)
        assert external.pid in unmanaged(entry)
        result = spawn(entry, tmp_path)
        assert result["state"] == "BLOCKED_CONFIG"
        assert external.pid in result["unmanaged_pids"]
        assert stop(entry, tmp_path)["state"] == "STOPPED"
        assert external.poll() is None, "manager killed an unmanaged Python process"
    finally:
        external.terminate()
        external.wait(timeout=5)

@pytest.mark.timeout(30)
def test_old_test_run_cannot_block_new_fake_worker(tmp_path):
    """Unmanaged duplicate detection must compare exact arguments, not just module."""
    import subprocess
    from obsidian_prediction.fleet_lifecycle import unmanaged
    prior = validate([{
        "id": "fake_worker", "enabled": True,
        "module": "obsidian_prediction._fleet_test_worker",
        "args": ["run", "--mode", "paper", "--test-nonce", "previous_run"],
    }])[0]
    old = subprocess.Popen(
        [sys.executable, "-u", "-m", prior.module, *prior.args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    fresh = fake()
    try:
        deadline = time.monotonic() + 5
        while old.pid not in unmanaged(prior) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert old.pid in unmanaged(prior)
        assert old.pid not in unmanaged(fresh)
        result = spawn(fresh, tmp_path)
        assert result["state"] == "STARTING", result
    finally:
        stop(fresh, tmp_path)
        old.terminate()
        old.wait(timeout=5)
