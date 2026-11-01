"""Owned-process fleet lifecycle for paper/observe workers only.

No broker credentials and no real order capability. PID records are never proof
without creation time, executable and command line identity checks.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil
from dotenv import load_dotenv

from .config import ROOT
from .fleet_registry import RegistryError, load, plan

RUNTIME = ROOT / "runtime" / "supervisor"
REGISTRY = ROOT / "bots" / "registry.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(document, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def fingerprint(module: str, args: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps([module, *args], ensure_ascii=False).encode()).hexdigest()


@contextlib.contextmanager
def lock(name: str, root: Path = RUNTIME, timeout: float = 12):
    """Per-worker exclusive mkdir lock with conservative stale recovery.

    A stale lock is removed only if its owner PID is demonstrably absent.
    Unreadable locks block instead of creating a duplicate process.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / (name + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir()
            atomic(path / "owner.json", {"pid": os.getpid(), "created": psutil.Process().create_time()})
            break
        except FileExistsError:
            owner = read(path / "owner.json")
            age = time.time() - path.stat().st_mtime
            if age > 30 and owner:
                try:
                    p = psutil.Process(int(owner["pid"]))
                    stale = abs(p.create_time() - float(owner["created"])) > 1
                except psutil.NoSuchProcess:
                    stale = True
                except (psutil.AccessDenied, KeyError, TypeError, ValueError):
                    stale = False  # never steal a lock when ownership cannot be checked
                if stale:
                    (path / "owner.json").unlink(missing_ok=True)
                    with contextlib.suppress(OSError):
                        path.rmdir()
                    continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Could not acquire worker lock: {name}")
            time.sleep(0.08)
    try:
        yield
    finally:
        (path / "owner.json").unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            path.rmdir()


def identity(entry, record: dict | None):
    if not record:
        return None
    try:
        proc = psutil.Process(int(record["pid"]))
        if abs(proc.create_time() - float(record["created"])) > 1:
            return None
        if os.path.normcase(os.path.realpath(proc.exe())) != os.path.normcase(
            os.path.realpath(record["interpreter"])
        ):
            return None
        command = proc.cmdline()
        if len(command) < 5 or command[1:4] != ["-u", "-m", "obsidian_prediction.fleet_bootstrap"]:
            return None
        if command[4] != entry.module or command[5:] != list(entry.args):
            return None
        if fingerprint(entry.module, entry.args) != record["args_sha256"]:
            return None
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        return None


def record_path(entry, root: Path) -> Path:
    return root / (entry.id + ".json")


def desired_path(entry, root: Path) -> Path:
    return root / (entry.id + ".desired.json")


def desired(entry, root: Path) -> bool:
    data = read(desired_path(entry, root))
    return entry.enabled if data is None else data.get("running") is True


def set_desired(entry, root: Path, running: bool) -> None:
    atomic(desired_path(entry, root), {"running": running, "updated": now()})


def unmanaged(entry, owned_pid: int | None = None) -> list[int]:
    """Find unmanaged exact-module workers, including manual python -m variants.

    The result is diagnostic only. Never signal an external process.
    """
    matches = []
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            argv = proc.info["cmdline"] or []
            if proc.pid == owned_pid or "-m" not in argv:
                continue
            if not (proc.info["name"] or "").lower().startswith(("python", "py.exe")):
                continue
            index = argv.index("-m")
            module = argv[index + 1] if len(argv) > index + 1 else ""
            values = argv[index + 2:]
            if module == "obsidian_prediction.fleet_bootstrap" and values:
                module, values = values[0], values[1:]
            if module == entry.module and (
                entry.kind == "service" or values == list(entry.args)
            ):
                matches.append(proc.pid)
        except (psutil.Error, OSError, ValueError):
            continue
    return matches


def missing_config(entry) -> list[str]:
    load_dotenv(ROOT / ".env", override=False)
    missing = [key for key in entry.requires_env if not os.getenv(key)]
    if entry.module == "obsidian_prediction.bonereaper_worker":
        if os.getenv("BONEREAPER_SOURCE_VERIFIED") != "YES":
            missing.append("BONEREAPER_SOURCE_VERIFIED=YES (official source verification)")
        if os.getenv("BONEREAPER_MODE", "observe") != entry.mode:
            missing.append("BONEREAPER_MODE must match registry mode")
    return sorted(set(missing))


def spawn(entry, root: Path = RUNTIME) -> dict:
    with lock(entry.id, root):
        if not entry.enabled:
            return {"id": entry.id, "state": "DISABLED", "reason": "registry disabled"}
        missing = missing_config(entry)
        if missing:
            return {"id": entry.id, "state": "BLOCKED_CONFIG",
                    "reason": "missing environment keys: " + ", ".join(missing)}
        current = read(record_path(entry, root))
        proc = identity(entry, current)
        external = unmanaged(entry, proc.pid if proc else None)
        if external:
            return {"id": entry.id, "state": "BLOCKED_CONFIG",
                    "reason": "exact unmanaged worker already running",
                    "unmanaged_pids": external}
        if proc:
            set_desired(entry, root, True)
            return {"id": entry.id, "state": "ALIVE_NOT_READY", "pid": proc.pid,
                    "reason": "already owned; heartbeat unverified"}
        if current:
            # Identity mismatch might mean a recycled PID; NEVER terminate it.
            atomic(root / (entry.id + ".stale.json"),
                   {"detected": now(), "previous": current, "reason": "identity mismatch"})
        run_id = uuid.uuid4().hex
        stdout = root / f"{entry.id}.{run_id}.stdout.log"
        stderr = root / f"{entry.id}.{run_id}.stderr.log"
        argv = [sys.executable, "-u", "-m", "obsidian_prediction.fleet_bootstrap",
                entry.module, *entry.args]
        root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, OBSIDIAN_FLEET_RUN_ID=run_id,
                   OBSIDIAN_FLEET_WORKER_ID=entry.id,
                   OBSIDIAN_FLEET_RUNTIME=str(root.resolve()))
        with stdout.open("ab") as out, stderr.open("ab") as err:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            child = subprocess.Popen(argv, cwd=ROOT, stdout=out, stderr=err,
                                     creationflags=flags, stdin=subprocess.DEVNULL, env=env)
        proc = psutil.Process(child.pid)
        sha = os.environ.get("OBSIDIAN_RELEASE_SHA", "UNKNOWN")
        data = {"pid": child.pid, "created": proc.create_time(),
                "interpreter": os.path.realpath(sys.executable), "module": entry.module,
                "args_sha256": fingerprint(entry.module, entry.args),
                "args": list(entry.args), "run_id": run_id, "launched": now(),
                "controller": os.getpid(), "release_sha": sha,
                "stdout": str(stdout), "stderr": str(stderr)}
        atomic(record_path(entry, root), data)
        set_desired(entry, root, True)
        return {"id": entry.id, "state": "STARTING", "pid": child.pid,
                "reason": "spawned; heartbeat not yet observed"}


def stop(entry, root: Path = RUNTIME, timeout: float = 5) -> dict:
    with lock(entry.id, root):
        set_desired(entry, root, False)
        record = read(record_path(entry, root))
        proc = identity(entry, record)
        if proc is None:
            # An invalid record is preserved for diagnosis; do not signal unknown PID.
            return {"id": entry.id, "state": "STOPPED" if not record else "UNKNOWN",
                    "reason": "no verified owned process"}
        children = proc.children(recursive=True)
        targets = [*children, proc]
        # Give the owned Python process a chance to run its cleanup handlers.
        # Windows worker processes are created in independent process groups.
        try:
            if os.name == "nt":
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
        except (OSError, psutil.Error, AttributeError):
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                proc.terminate()
        _, alive = psutil.wait_procs(targets, timeout=timeout)
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.terminate()
        _, alive = psutil.wait_procs(alive, timeout=min(2, timeout))
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()
        _, alive = psutil.wait_procs(alive, timeout=timeout)
        if alive:
            return {"id": entry.id, "state": "DEGRADED",
                    "reason": "owned process tree did not terminate",
                    "pids": [p.pid for p in alive]}
        record_path(entry, root).unlink(missing_ok=True)
        return {"id": entry.id, "state": "STOPPED", "reason": "owned tree exited"}


def status(entry, root: Path = RUNTIME) -> dict:
    if not entry.enabled:
        return {"id": entry.id, "state": "DISABLED", "reason": "registry disabled"}
    record = read(record_path(entry, root))
    proc = identity(entry, record)
    if proc is None:
        external = unmanaged(entry)
        if external:
            return {"id": entry.id, "state": "UNKNOWN",
                    "reason": "unmanaged process; do not terminate",
                    "unmanaged_pids": external}
        missing = missing_config(entry)
        if missing:
            return {"id": entry.id, "state": "BLOCKED_CONFIG",
                    "reason": "missing environment keys: " + ", ".join(missing)}
        previous = read(root / (entry.id + ".exit.json")) or {}
        exit_code = (previous.get("exit_code")
                     if previous.get("run_id") == (record or {}).get("run_id") else None)
        result = {"id": entry.id, "state": "UNKNOWN" if record else "STOPPED",
                  "reason": "ownership record unverified" if record else "no owned process"}
        if exit_code is not None:
            result["last_exit_code"] = exit_code
            result["last_exit_cause"] = previous.get("cause")
        return result
    hb = read(root / (entry.id + ".heartbeat.json"))
    if not hb or hb.get("run_id") != record.get("run_id"):
        state, reason = "ALIVE_NOT_READY", "no matching scan heartbeat"
    else:
        try:
            age = time.time() - datetime.fromisoformat(hb["updated"]).timestamp()
        except (ValueError, TypeError, KeyError):
            age = float("inf")
        if age > 300:
            state, reason = "DEGRADED", "scan heartbeat stale"
        elif hb.get("last_scan_completed"):
            state = "IDLE" if hb.get("idle_reason") else "HEALTHY"
            reason = hb.get("idle_reason") or "scan completed"
        else:
            state, reason = "ALIVE_NOT_READY", "first scan pending"
    return {"id": entry.id, "state": state, "pid": proc.pid, "reason": reason,
            "run_id": record.get("run_id")}


def report(entries, results):
    snapshot = plan(entries)
    snapshot["workers"] = results
    state_map = {"alive": ("STARTING", "ALIVE_NOT_READY", "HEALTHY", "IDLE", "DEGRADED"),
                 "ready": ("HEALTHY", "IDLE"),
                 "degraded": ("DEGRADED", "ALIVE_NOT_READY"),
                 "blocked": ("BLOCKED_CONFIG",),
                 "failed": ("UNKNOWN", "CRASH_LOOP")}
    snapshot["attempted"] = [r["id"] for r in results if r["state"] != "DISABLED"]
    for key, states in state_map.items():
        snapshot[key] = [r["id"] for r in results if r["state"] in states]
    return snapshot


def main(argv=None):
    p = argparse.ArgumentParser(description="Owned, offline worker lifecycle")
    p.add_argument("action", choices=["plan", "start", "stop", "restart", "status", "logs"])
    p.add_argument("--bot", default="all")
    p.add_argument("--tail", type=int, default=50)
    p.add_argument("--runtime", type=Path, default=RUNTIME)
    args = p.parse_args(argv)
    try:
        entries = load(REGISTRY)
        selected = entries if args.bot == "all" else [e for e in entries if e.id == args.bot]
        if not selected:
            raise RegistryError(f"Unknown bot {args.bot!r}")
        if args.action == "plan":
            result = plan(entries, args.bot)
        elif args.action == "logs":
            result = {}
            for entry in selected:
                record = read(record_path(entry, args.runtime)) or {}
                lines = {}
                for stream in ("stdout", "stderr"):
                    path = Path(record.get(stream, "missing"))
                    lines[stream] = path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.tail:] if path.is_file() else []
                result[entry.id] = lines
        else:
            results = []
            for entry in selected:
                try:
                    if args.action == "start":
                        r = spawn(entry, args.runtime)
                    elif args.action == "stop":
                        r = stop(entry, args.runtime)
                    elif args.action == "restart":
                        stopped = stop(entry, args.runtime)
                        if stopped["state"] != "STOPPED":
                            r = {"id": entry.id, "state": "UNKNOWN",
                                 "reason": "restart refused: owned stop unverified"}
                        else:
                            r = spawn(entry, args.runtime)
                    else:
                        r = status(entry, args.runtime)
                except (OSError, psutil.Error, TimeoutError, ValueError) as exc:
                    r = {"id": entry.id, "state": "UNKNOWN", "reason": f"{type(exc).__name__}: {exc}"}
                results.append(r)
            result = report(selected, results)
        print(json.dumps(result, indent=2))
    except RegistryError as exc:
        print(json.dumps({"error": str(exc), "config_ok": False}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
