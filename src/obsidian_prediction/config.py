"""Explicit fail-closed configuration. Nothing in this package sends exchange orders."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    trading_enabled: bool
    live_approved: bool
    data_dir: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        load_environment()

        def flag(key: str) -> bool:
            value = os.environ.get(key, "false").lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{key} must be true or false")
            return value == "true"

        trade, approved = flag("TRADING_ENABLED"), flag("LIVE_EXECUTION_APPROVED")
        if trade or approved:
            raise RuntimeError("Live trading is not implemented. Keep both trading flags false.")
        return cls(trade, approved, Path(os.environ.get("PREDICTION_DATA_DIR", ROOT / "data")))
