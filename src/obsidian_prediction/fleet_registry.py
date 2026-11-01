"""Strict, paper-only canonical worker registry.

No import side effects or network calls. Invalid inventory is rejected atomically.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_SLUG = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ALLOWED_MODES = frozenset({"paper", "observe"})


class RegistryError(ValueError):
    """Configuration problem which must block the entire launch plan."""


@dataclass(frozen=True)
class Entry:
    id: str
    enabled: bool
    module: str
    args: tuple[str, ...]
    mode: str
    kind: str = "bot"

    def as_dict(self) -> dict:
        return {"id": self.id, "enabled": self.enabled, "module": self.module,
                "args": list(self.args), "mode": self.mode, "kind": self.kind}


def validate(items: object) -> list[Entry]:
    if not isinstance(items, list):
        raise RegistryError("bots/registry.json: root must be a JSON array")
    seen: set[str] = set()
    entries: list[Entry] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        label = f"registry[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        name = item.get("id")
        if not isinstance(name, str) or not _SLUG.fullmatch(name):
            errors.append(f"{label}: id must be a lowercase slug (3-64 characters)")
            continue
        if name in seen:
            errors.append(f"{label}: duplicate id {name!r}")
            continue
        seen.add(name)
        if type(item.get("enabled")) is not bool:
            errors.append(f"{label}: enabled must be true or false")
            continue
        module = item.get("module")
        if not isinstance(module, str) or not _MODULE.fullmatch(module):
            errors.append(f"{label}: invalid Python module")
            continue
        if not module.startswith("obsidian_prediction."):
            errors.append(f"{label}: module must be in obsidian_prediction package")
            continue
        args = item.get("args")
        if not isinstance(args, list) or not all(
            isinstance(arg, str) and bool(arg) and not any(ord(c) < 32 for c in arg)
            for arg in args
        ):
            errors.append(f"{label}: args must be nonempty strings without control characters")
            continue
        if "--mode" not in args or args.count("--mode") != 1:
            errors.append(f"{label}: explicit --mode paper|observe required exactly once")
            continue
        index = args.index("--mode")
        mode = args[index + 1] if index + 1 < len(args) else ""
        if mode not in _ALLOWED_MODES:
            errors.append(f"{label}: unsafe or unknown mode {mode!r}; use paper|observe")
            continue
        kind = item.get("kind", "bot")
        if kind not in ("bot", "service"):
            errors.append(f"{label}: kind must be bot or service")
            continue
        entries.append(Entry(name, item["enabled"], module, tuple(args), mode, kind))
    if errors:
        raise RegistryError("; ".join(errors))
    return entries


def load(path: Path) -> list[Entry]:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{path}: {exc}") from exc


def plan(entries: list[Entry], bot: str = "all") -> dict:
    selected = entries if bot == "all" else [e for e in entries if e.id == bot]
    if bot != "all" and not selected:
        raise RegistryError(f"Unknown bot {bot!r}; inspect bots/registry.json")
    enabled = [e.id for e in selected if e.enabled]
    disabled = [e.id for e in selected if not e.enabled]
    return {"expected": len(selected), "enabled": enabled, "disabled": disabled,
            "attempted": [], "alive": [], "ready": [], "degraded": [],
            "blocked": [], "failed": [],
            "workers": [dict(e.as_dict(), status="PENDING" if e.enabled else "DISABLED",
                             reason=None if e.enabled else "disabled by registry")
                        for e in selected]}
