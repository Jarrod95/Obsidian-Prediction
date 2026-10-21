"""Minimal read-only CLI for public market research."""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, Settings
from .exchanges import PublicPolymarket


def registry():
    path = ROOT / "bots" / "registry.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


async def collect(limit: int, pages: int, output: Path) -> int:
    client = PublicPolymarket()
    count = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("w", encoding="utf-8") as fh:
            async for item in client.markets(limit=limit, max_pages=pages):
                record = {
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "source": "polymarket_gamma",
                    "raw": item,
                }
                fh.write(json.dumps(record) + "\n")
                count += 1
    finally:
        await client.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(prog="obsidian-prediction")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    bots = sub.add_parser("bots")
    bots.add_argument("action", choices=["list", "status"])
    bots.add_argument("bot_id", nargs="?")
    cp = sub.add_parser("collect")
    cp.add_argument("--exchange", choices=["polymarket"], required=True)
    cp.add_argument("--mode", choices=["public"], default="public")
    cp.add_argument("--pages", type=int, default=5)
    cp.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    settings = Settings.from_environment()
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "version": "0.1.0",
                    "live_trading": False,
                    "data_dir": str(settings.data_dir),
                    "config_ok": True,
                },
                indent=2,
            )
        )
    elif args.command == "bots":
        entries = registry()
        if args.action == "list":
            print(json.dumps(entries, indent=2))
        else:
            matches = [x for x in entries if x.get("bot_id") == args.bot_id]
            if not matches:
                parser.error("Unknown bot id")
            print(json.dumps(matches[0], indent=2))
    elif args.command == "collect":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = settings.data_dir / "raw" / f"polymarket_markets_{stamp}.jsonl"
        print(
            f"Saved {asyncio.run(collect(args.limit, args.pages, output))} "
            f"markets to {output}"
        )


if __name__ == "__main__":
    main()
