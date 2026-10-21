"""Free daily USD/AUD reference FX, with explicit source date and cache."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import httpx

URL = "https://api.frankfurter.dev/v2/rate/USD/AUD"

def parse_fx(payload, *, today=None, max_age_days=7):
    today = today or datetime.now(timezone.utc).date()
    if payload.get("base", "").upper() != "USD" or payload.get("quote", "").upper() != "AUD":
        raise ValueError("Unexpected FX currency pair")
    rate = Decimal(str(payload["rate"]))
    if not rate.is_finite() or rate <= 0:
        raise ValueError("Invalid FX rate")
    observation = date.fromisoformat(payload["date"])
    if observation > today or today - observation > timedelta(days=max_age_days):
        raise ValueError("FX observation date outside allowed window")
    return rate, observation.isoformat()

class FXProvider:
    def __init__(self, *, max_age_days=7, refresh_seconds=3600):
        self.max_age_days = max_age_days
        self.refresh_seconds = refresh_seconds
        self.last_fetch = None
        self.rate = None
        self.observation_date = None

    async def latest(self):
        now = datetime.now(timezone.utc)
        if self.rate is not None and self.last_fetch is not None and (now - self.last_fetch).total_seconds() < self.refresh_seconds:
            return self.rate, self.observation_date
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(URL)
            response.raise_for_status()
            rate, observation = parse_fx(response.json(), today=now.date(), max_age_days=self.max_age_days)
        self.rate, self.observation_date, self.last_fetch = rate, observation, now
        return rate, observation
