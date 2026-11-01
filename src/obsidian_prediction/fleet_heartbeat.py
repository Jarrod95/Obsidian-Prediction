"""Atomic scan heartbeat schema for read-only and shadow-paper workers.

No worker logic or trade decisions are changed by these functions.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .fleet_lifecycle import atomic, read


def mark(**fields) -> None:
    worker = os.environ.get("OBSIDIAN_FLEET_WORKER_ID")
    run_id = os.environ.get("OBSIDIAN_FLEET_RUN_ID")
    root = os.environ.get("OBSIDIAN_FLEET_RUNTIME")
    if not (worker and run_id and root):
        return  # Manual launches do not claim supervisor ownership.
    path = Path(root) / f"{worker}.heartbeat.json"
    state = read(path) or {}
    if state.get("run_id") != run_id:
        state = {"run_id": run_id, "worker_id": worker}
    state["schema_version"] = 1
    state.update(fields)
    state["updated"] = datetime.now(timezone.utc).isoformat()
    try:
        atomic(path, state)
    except OSError:
        # Monitoring failure must not interrupt paper research or ledger writes.
        return
