"""Public Polymarket endpoints only; no signing or trading APIs."""
import asyncio
import random

import httpx

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


class PublicPolymarket:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._own = client is None
        self.client = client or httpx.AsyncClient(
            timeout=20, headers={"User-Agent": "Obsidian-Prediction/0.1"}
        )

    async def close(self) -> None:
        if self._own:
            await self.client.aclose()

    async def _get(self, url: str, params: dict | None = None):
        for attempt in range(5):
            response = await self.client.get(url, params=params)
            if response.status_code in (429, 500, 502, 503, 504):
                delay = response.headers.get("retry-after", "")
                try:
                    wait = (
                        min(30, max(0, float(delay)))
                        if delay else min(16, 2 ** attempt + random.random())
                    )
                except ValueError:
                    wait = min(16, 2 ** attempt + random.random())
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Public API retries exhausted")

    async def markets(self, limit: int = 100, max_pages: int = 5):
        """Stop on terminal offset, empty page, or short page."""
        if not 1 <= limit <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("Invalid page configuration")
        for page in range(max_pages):
            try:
                items = await self._get(
                    f"{GAMMA}/markets",
                    {
                        "limit": limit,
                        "offset": page * limit,
                        "active": "true",
                        "closed": "false",
                    },
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422 and page > 0:
                    break
                raise
            if not isinstance(items, list) or not items:
                break
            for item in items:
                yield item
            if len(items) < limit:
                break

    async def orderbook(self, token_id: str):
        if not token_id or not token_id.isdecimal():
            raise ValueError("Token id must be a numeric CLOB token id")
        return await self._get(f"{CLOB}/book", {"token_id": token_id})

    async def market(self, market_id: str):
        """Retrieve an individual market including closed/resolved market data."""
        if not str(market_id).isdecimal():
            raise ValueError('Market ID must be numeric')
        return await self._get(f'{GAMMA}/markets/{market_id}')
