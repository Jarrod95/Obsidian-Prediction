"""Run worker modules with a run-scoped crash/exit read-model.

Preserves the worker's original argparse shape, environment and research logic.
This module does not import any order or broker execution adapter.
"""
from __future__ import annotations

import os
import runpy
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .fleet_lifecycle import atomic


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m obsidian_prediction.fleet_bootstrap MODULE [args...]")
    module = sys.argv[1]
    worker = os.environ.get("OBSIDIAN_FLEET_WORKER_ID")
    runtime = os.environ.get("OBSIDIAN_FLEET_RUNTIME")
    run_id = os.environ.get("OBSIDIAN_FLEET_RUN_ID")
    sys.argv = [module, *sys.argv[2:]]
    outcome = {"run_id": run_id, "worker_id": worker, "module": module,
               "exit_code": 0, "cause": "normal_exit"}
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        outcome["exit_code"] = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        outcome["cause"] = "system_exit"
        raise
    except KeyboardInterrupt:
        outcome["exit_code"] = 130
        outcome["cause"] = "keyboard_interrupt"
        raise
    except BaseException as exc:
        outcome["exit_code"] = 1
        outcome["cause"] = type(exc).__name__
        outcome["error"] = str(exc)[:350]
        traceback.print_exc()
        raise
    finally:
        if worker and runtime and run_id:
            outcome["exited_at"] = datetime.now(timezone.utc).isoformat()
            try:
                atomic(Path(runtime) / (worker + ".exit.json"), outcome)
            except OSError:
                traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
