"""Deterministic fake worker for isolated offline lifecycle tests.

Never accesses markets, Telegram, credentials or accounts.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run")
    p.add_argument("--mode", choices=("paper", "observe"), required=True)
    p.add_argument("--interval", type=float, default=0.1)
    p.add_argument("--exit-code", type=int)
    p.add_argument("--child-pid-file")
    p.add_argument("--spawn-child", action="store_true")
    a = p.parse_args()
    if a.spawn_child:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        if a.child_pid_file:
            Path(a.child_pid_file).write_text(str(child.pid), encoding="utf-8")
    if a.exit_code is not None:
        raise SystemExit(a.exit_code)
    while True:
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
