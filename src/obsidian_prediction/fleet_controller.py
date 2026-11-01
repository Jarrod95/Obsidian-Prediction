"""Persistent bounded desired-state reconciliation for paper workers."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .fleet_lifecycle import RUNTIME, atomic, desired, now, read, spawn, status
from .fleet_registry import load
from .fleet_lifecycle import REGISTRY

WINDOW = 3600
MAX_RESTARTS = 5


def decision(history: dict, timestamp: float, running: bool, state: str) -> tuple[bool, dict]:
    """Pure restart policy; history survives controller restarts."""
    events = [float(x) for x in history.get("events", [])
              if 0 <= timestamp - float(x) < WINDOW]
    quarantine = bool(history.get("quarantined")) and len(events) >= MAX_RESTARTS
    if not running:
        return False, {"events": events, "quarantined": quarantine, "reason": "operator stopped"}
    if state in ("HEALTHY", "IDLE", "STARTING", "ALIVE_NOT_READY", "DEGRADED", "BLOCKED_CONFIG"):
        return False, {"events": events, "quarantined": quarantine,
                       "reason": "blocked configuration" if state == "BLOCKED_CONFIG" else "owned process alive"}
    if quarantine or len(events) >= MAX_RESTARTS:
        return False, {"events": events, "quarantined": True, "reason": "CRASH_LOOP"}
    base_delay = min(300, 2 ** len(events))
    if events and timestamp - events[-1] < base_delay:
        return False, {"events": events, "quarantined": False, "reason": "backoff"}
    return True, {"events": events, "quarantined": False, "reason": "restart permitted"}


def reconcile(root: Path = RUNTIME, timestamp: float | None = None) -> list[dict]:
    timestamp = time.time() if timestamp is None else timestamp
    results = []
    for entry in load(REGISTRY):
        if not entry.enabled:
            results.append({"id": entry.id, "state": "DISABLED"})
            continue
        try:
            current = status(entry, root)
            state_path = root / f"{entry.id}.restarts.json"
            history = read(state_path) or {}
            allowed, updated = decision(history, timestamp, desired(entry, root),
                                        current["state"])
            if allowed:
                result = spawn(entry, root, respect_desired=True)
                if result["state"] == "STARTING":
                    updated["events"].append(timestamp)
                updated["last_restart"] = now()
            else:
                result = dict(current)
                if updated["quarantined"]:
                    result.update(state="CRASH_LOOP", reason="restart budget exhausted")
                elif not desired(entry, root):
                    result.update(state="STOPPED", reason="operator desired stopped")
                elif updated["reason"] == "backoff":
                    result["reason"] = "restart backoff in effect"
            atomic(state_path, updated)
            results.append(result)
        except Exception as exc:
            results.append({"id": entry.id, "state": "UNKNOWN",
                            "reason": f"{type(exc).__name__}: {exc}"})
    atomic(root / "controller.json", {"schema_version": 1, "updated": now(), "pid": __import__("os").getpid(),
                                     "workers": results})
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description="Prediction fleet recovery controller")
    p.add_argument("action", choices=["run", "once", "reset"])
    p.add_argument("--bot")
    p.add_argument("--interval", type=int, default=30)
    args = p.parse_args(argv)
    if args.action == "reset":
        if not args.bot:
            p.error("--bot required to reset a crash window")
        entry = next((e for e in load(REGISTRY) if e.id == args.bot), None)
        if entry is None:
            p.error("unknown bot")
        atomic(RUNTIME / f"{entry.id}.restarts.json",
               {"events": [], "quarantined": False, "reason": "operator reset"})
        print(json.dumps({"reset": entry.id}))
        return
    while True:
        print(json.dumps(reconcile(), indent=2), flush=True)
        if args.action == "once":
            break
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    main()
