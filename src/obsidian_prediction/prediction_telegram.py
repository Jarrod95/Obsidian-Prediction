"""Fail-closed, read-only Telegram command service for Obsidian-Prediction.

Run separately from the strategy process. No trading, settlement or database writes.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BOT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SYDNEY = ZoneInfo("Australia/Sydney")
MAX_OUTPUT = 3500
D = Decimal


def settings():
    """Never start without both allowlists and a token."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    users = os.getenv("TELEGRAM_USER_ID", "").strip()
    chats = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not users or not chats:
        raise ValueError("Telegram token and numeric user/chat allowlists are required")
    if not all(v.isascii() and v.isdecimal() for v in (users, chats)):
        raise ValueError("Telegram allowlists must be numeric")
    return token, int(users), int(chats)


def authorized(user_id, chat_id, chat_type, allowed_user, allowed_chat):
    return (
        chat_type == "private"
        and type(user_id) is int
        and type(chat_id) is int
        and user_id == allowed_user
        and chat_id == allowed_chat
    )


def registry(path: Path):
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("Invalid bot registry")
    names = set()
    bots = {}
    for item in items:
        name = item["id"]
        if not isinstance(name, str) or not BOT_ID.fullmatch(name) or name in names:
            raise ValueError("Invalid or duplicate canonical bot name")
        names.add(name)
        args = item.get("args", [])
        mode = args[args.index("--mode") + 1] if "--mode" in args and args.index("--mode") + 1 < len(args) else "paper"
        if mode not in ("paper", "live", "observe"):
            raise ValueError("Unknown bot mode")
        bots[name] = {"mode": mode, "enabled": bool(item.get("enabled", False)),
                      "venue": "polymarket" if "polymarket" in name else "UNKNOWN"}
    return bots


def connection(path: Path):
    """URI ro mode prevents implicit file creation or writes."""
    if not path.is_file():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=2000")
    return db


def table_exists(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def rows(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def money(value):
    try:
        return f"A${D(str(value)):+,.2f}"
    except (ValueError, InvalidOperation, TypeError):
        return "NOT AVAILABLE"


def aud_sum(records, column):
    return sum((D(str(row[column])) for row in records if row[column] is not None), D(0))


def window(period, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(SYDNEY)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end = today, today + timedelta(days=1)
    elif period == "yesterday":
        start, end = today - timedelta(days=1), today
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today + timedelta(days=1)
    elif period == "month":
        start, end = today.replace(day=1), today + timedelta(days=1)
    else:
        return None
    return start, end


def in_period(ts, period, now=None):
    if period == "all":
        return True
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return False
        start, end = window(period, now)
        local = parsed.astimezone(SYDNEY)
        return start <= local < end
    except (ValueError, TypeError):
        return False


def safe(value, limit=100):
    """Defang control characters, escape sequences and accidental long values."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value or ""))[:limit]


def render(command, args, bots, db, db_path, now=None):
    """Pure read model, deliberately never opens an execution client."""
    command = command.lower()
    if command in ("start", "help"):
        return ("OBSIDIAN PREDICTION / READ-ONLY\n"
                "/status /bots /health [bot] /db /account [bot]\n"
                "/positions [bot] /position <id> /trades [bot] [count]\n"
                "/pnl [bot] /today /yesterday /week /month\n"
                "/settlements [count] /unsettled /leaderboard [paper|live|all] [today|week|month|all]\n"
                "/signals [bot] [count] /rejections [bot] /version\n"
                "All bot selectors are canonical names. No process controls or execution.")
    if command == "bots":
        if not bots:
            return "No bots configured."
        return "CONFIGURED BOTS (not verified running)\n" + "\n".join(
            f"{name} | {b['mode'].upper()} | {b['venue']} | {'ENABLED' if b['enabled'] else 'DISABLED'}"
            for name, b in sorted(bots.items()))
    if command == "version":
        return "Deployed build: NOT AVAILABLE (set deployment SHA in supervisor metadata)."
    if command in ("status", "health"):
        bot = args[0] if command == "health" and args else None
        if bot and bot not in bots:
            return "Unknown bot name. Use /bots."
        latest = None
        if db and table_exists(db, "signals"):
            r = rows(db, "SELECT MAX(timestamp) AS ts FROM signals")
            latest = r[0]["ts"] if r else None
        return (f"PLATFORM: PARTIAL | configured={len(bots)} | "
                f"paper={sum(b['mode'] == 'paper' for b in bots.values())} | "
                f"live={sum(b['mode'] == 'live' for b in bots.values())}\n"
                f"Bot: {bot or 'all'} | process identity: UNKNOWN | scan heartbeat: UNKNOWN\n"
                f"Latest stored signal: {safe(latest) if latest else 'NOT AVAILABLE'}\n"
                "Process liveness is NOT established by registry membership.")
    if command == "db":
        if not db:
            return "DB: NOT AVAILABLE (database missing); no database was created."
        names = ("signals", "baskets", "paper_settlements", "paper_ledger", "telegram_outbox")
        counts = []
        for name in names:
            if table_exists(db, name):
                result = rows(db, f"SELECT COUNT(*) AS n FROM {name}")
                counts.append(f"{name}: {result[0]['n'] if result else 'UNAVAILABLE'}")
        wal = rows(db, "PRAGMA journal_mode")
        size = db_path.stat().st_size if db_path.is_file() else 0
        return ("DB: READ ONLY | integrity: NOT CHECKED\n"
                f"SQLite bytes: {size} | journal: {wal[0][0] if wal else 'UNKNOWN'}\n"
                + "\n".join(counts))
    if command == "leaderboard":
        if len(args) > 2 or (args and args[0] not in ("paper", "live", "all")):
            return "Usage: /leaderboard [paper|live|all] [today|week|month|all]"
        mode = args[0] if args else "paper"
        period = args[1] if len(args) > 1 else "month"
        if period not in ("today", "week", "month", "all"):
            return "Invalid period. Choose today, week, month or all."
        chunks = []
        for current_mode in (("paper", "live") if mode == "all" else (mode,)):
            if current_mode == "live":
                chunks.append("LIVE: NOT CONFIGURED — no verified live ledger/adapter")
                continue
            paper_bots = [n for n, b in sorted(bots.items()) if b["mode"] == "paper"]
            if not paper_bots:
                chunks.append("PAPER: no registered bots.")
                continue
            settled = rows(db, """SELECT b.id, b.signal_id, p.pnl_aud, p.pnl_pct, p.settled_at
                                  FROM paper_settlements p JOIN baskets b ON b.id=p.basket_id
                                  ORDER BY p.settled_at DESC LIMIT 10000""") if db else []
            filtered = [r for r in settled if in_period(r["settled_at"], period, now)]
            # Old shared-account rows have no immutable bot attribution.
            chunks.append(f"PAPER / {period.upper()} / AUD | UNRANKED (shared account)\n"
                          + "\n".join(f"— {n}: NOT ATTRIBUTABLE | account return N/A"
                                      for n in paper_bots)
                          + f"\nUnattributed settlements: {len(filtered)} | "
                          f"shared realized P/L: {money(aud_sum(filtered, 'pnl_aud'))}\n"
                          "Snapshot-simulated fills; capital is shared. No bot return or rank.")
        return "\n\n".join(chunks)
    if command in ("today", "yesterday", "week", "month", "pnl", "settlements", "trades"):
        if command in ("pnl", "trades") and args and args[0] in bots:
            return "Bot-level realized results: NOT AVAILABLE (shared account; no verified attribution)."
        if command in ("pnl", "trades") and args and args[0] not in bots and not (command == "trades" and args[0].isdecimal()):
            return "Unknown bot name. Use /bots."
        if not db:
            return "NOT AVAILABLE: paper database missing."
        records = rows(db, "SELECT basket_id, settled_at, pnl_aud, pnl_usd, pnl_pct, winner FROM paper_settlements ORDER BY settled_at DESC LIMIT 10000")
        period = command if command in ("today", "yesterday", "week", "month") else "all"
        records = [r for r in records if in_period(r["settled_at"], period, now)]
        if command in ("settlements", "trades"):
            raw = args[-1] if args else "10"
            if not raw.isdecimal() or not 1 <= int(raw) <= 20:
                return "Count must be between 1 and 20."
            return ("PAPER SETTLEMENTS | realized, modeled costs only\n" +
                    ("\n".join(f"{safe(r['basket_id'], 16)} | {safe(r['settled_at'], 25)} | "
                               f"{money(r['pnl_aud'])} | {safe(r['winner'], 5)}"
                               for r in records[:int(raw)]) or "No settled baskets."))
        return (f"PAPER PORTFOLIO / {period.upper()} | settlements={len(records)}\n"
                f"Realized P/L: {money(aud_sum(records, 'pnl_aud'))}\n"
                "Shared capital; simulated fills; modeled costs may be incomplete. "
                "Open baskets are not marked to market.")
    if command == "account":
        if args:
            if args[0] not in bots:
                return "Unknown bot name. Use /bots."
            return "Per-bot cash: NOT AVAILABLE; paper account is shared."
        if not db:
            return "PAPER ACCOUNT: NOT AVAILABLE"
        account = rows(db, "SELECT initial_aud,cash_aud,realized_aud FROM paper_accounts WHERE id=1")
        if not account:
            return "PAPER ACCOUNT: NOT AVAILABLE"
        open_rows = rows(db, "SELECT cost_aud FROM baskets WHERE status='OPEN_HEDGED'")
        deployed = aud_sum(open_rows, "cost_aud")
        a = account[0]
        return (f"PAPER SHARED ACCOUNT | initial {money(a['initial_aud'])} | "
                f"cash {money(a['cash_aud'])} | reserved {money(deployed)}\n"
                f"cost-basis equity {money(D(a['cash_aud']) + deployed)} | "
                f"realized {money(a['realized_aud'])}\nNot mark-to-market.")
    if command in ("positions", "unsettled", "position"):
        if command == "positions" and args:
            if args[0] not in bots:
                return "Unknown bot name. Use /bots."
            return "Per-bot positions: NOT AVAILABLE (legacy basket attribution not verified)."
        if not db:
            return "NOT AVAILABLE: paper database missing."
        if command == "position":
            if len(args) != 1 or len(args[0]) > 64 or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", args[0]):
                return "Usage: /position <basket_id>"
            result = rows(db, "SELECT id, market_question, status, cost_aud, opened_at FROM baskets WHERE id=?", (args[0],))
            if not result:
                return "Basket not found."
            r = result[0]
            return (f"PAPER basket {safe(r['id'], 40)}\n{safe(r['market_question'], 140)}\n"
                    f"State {safe(r['status'])} | cost {money(r['cost_aud'])}\n"
                    f"Opened {safe(r['opened_at'])}\n"
                    "Snapshot-simulated fills; bot attribution NOT AVAILABLE.")
        result = rows(db, "SELECT id, market_question, cost_aud, opened_at FROM baskets WHERE status='OPEN_HEDGED' ORDER BY opened_at DESC LIMIT 20")
        if command == "unsettled":
            return ("UNSETTLED: advertised closing times are not persistently recorded; "
                    "expired-vs-open cannot be verified.\n"
                    f"Open baskets awaiting resolution: {len(result)} (max 20 shown).")
        return ("PAPER OPEN BASKETS (max 20; shared account)\n" +
                ("\n".join(f"{safe(r['id'], 12)} | {money(r['cost_aud'])} | {safe(r['market_question'], 70)}"
                           for r in result) or "No open baskets.") +
                "\nSnapshot-simulated, not executable fills.")
    if command in ("signals", "rejections"):
        if args and args[0] not in bots:
            return "Unknown bot name. Use /bots."
        if not db:
            return "NOT AVAILABLE: paper database missing."
        if command == "rejections":
            data = rows(db, "SELECT reason FROM signals WHERE decision='REJECTED' ORDER BY timestamp DESC LIMIT 1000")
            counts = Counter(safe(r["reason"], 50) for r in data)
            return "REJECTIONS / latest 1000 evaluations\n" + ("\n".join(f"{k}: {v}" for k,v in counts.most_common(15)) or "No recorded rejections.")
        limit = args[1] if len(args) > 1 else "10"
        if not limit.isdecimal() or not 1 <= int(limit) <= 20:
            return "Count must be between 1 and 20."
        data = rows(db, "SELECT timestamp, market_id, decision, reason, edge_pct FROM signals ORDER BY timestamp DESC LIMIT ?", (int(limit),))
        return "PAPER SIGNALS / observed (not trades)\n" + ("\n".join(
            f"{safe(r['timestamp'], 22)} market={safe(r['market_id'], 16)} "
            f"{safe(r['decision'], 12)} {safe(r['reason'], 30)} edge={safe(r['edge_pct'], 12)}%"
            for r in data) or "No signals.")
    return "Command not implemented in this release. /help lists supported commands."


async def reply(update, context):
    user = update.effective_user
    chat = update.effective_chat
    authorized_user = authorized(user.id if user else None, chat.id if chat else None,
                                 chat.type if chat else None,
                                 context.application.bot_data["allowed_user"],
                                 context.application.bot_data["allowed_chat"])
    # Never disclose the permitted IDs to an unauthorized caller.
    if not authorized_user:
        return
    message = update.effective_message
    if not message or not message.text:
        return
    command = message.text.split()[0].split("@")[0].lstrip("/")
    args = message.text.split()[1:4]
    data = context.application.bot_data
    db = None
    try:
        db = connection(data["db_path"])
        output = render(command, args, data["bots"], db, data["db_path"])
    except (ValueError, sqlite3.Error, OSError, KeyError):
        output = "NOT AVAILABLE: read-only report failed. Check local service logs."
    finally:
        if db:
            db.close()
    await message.reply_text(safe(output, MAX_OUTPUT), disable_web_page_preview=True)


def main():
    from telegram.ext import Application, CommandHandler
    token, user, chat = settings()
    root = Path(os.environ.get("OBSIDIAN_PREDICTION_ROOT", ".")).resolve()
    bots = registry(root / "bots" / "registry.json")
    app = Application.builder().token(token).build()
    app.bot_data.update(allowed_user=user, allowed_chat=chat,
                        bots=bots, db_path=root / "runtime" / "parity_paper.sqlite3")
    commands = ("start help status bots health db account positions position trades pnl "
                "today yesterday week month settlements unsettled leaderboard signals "
                "rejections version")
    for command in commands.split():
        app.add_handler(CommandHandler(command, reply))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
