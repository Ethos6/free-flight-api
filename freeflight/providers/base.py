"""Provider base class: polite pacing, circuit breaking, and normalization helpers."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, ClassVar

import httpx

from ..models import Aircraft

USER_AGENT = "FreeFlightAPI/1.0 (+https://github.com/Ethos6/free-flight-api)"


class ProviderError(Exception):
    pass


class ProviderBusy(ProviderError):
    """Our own pacing says we must not call this provider right now."""


class ProviderBlocked(ProviderError):
    """The provider answered 429/403: back off."""


class Unsupported(ProviderError):
    pass


class Provider:
    name: ClassVar[str]
    url: ClassVar[str] = ""  # homepage, for attribution
    license: ClassVar[str] = ""
    kinds: ClassVar[set[str]] = set()
    min_interval: ClassVar[float] = 1.05  # seconds between requests (most networks allow 1 req/s)

    def __init__(self, max_wait: float = 2.5) -> None:
        self.max_wait = max_wait
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self.cooldown_until = 0.0
        self.failures = 0
        self.requests = 0
        self.successes = 0
        self.last_error: str | None = None
        self.latencies: list[int] = []

    # ------------------------------------------------------------- health
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def _ok(self, ms: int) -> None:
        self.failures, self.cooldown_until, self.last_error = 0, 0.0, None
        self.requests += 1
        self.successes += 1
        self.latencies = (self.latencies + [ms])[-50:]

    def _fail(self, err: str, blocked: bool) -> None:
        self.failures += 1
        self.requests += 1
        self.last_error = err
        if blocked:
            delay = min(30 * 2 ** (self.failures - 1), 900)
        elif self.failures >= 3:
            delay = min(10 * 2 ** (self.failures - 3), 300)
        else:
            delay = 0
        self.cooldown_until = time.monotonic() + delay

    def status(self) -> dict[str, Any]:
        lat = sorted(self.latencies)
        return {
            "name": self.name,
            "url": self.url,
            "license": self.license,
            "supports": sorted(self.kinds),
            "available": self.available(),
            "cooldown_seconds": max(0, round(self.cooldown_until - time.monotonic())),
            "requests": self.requests,
            "success_rate": round(self.successes / self.requests, 3) if self.requests else None,
            "median_latency_ms": lat[len(lat) // 2] if lat else None,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------- pacing
    async def _pace(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > self.max_wait:
                raise ProviderBusy(f"rate limit: next slot in {wait:.1f}s")
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def get_json(self, client: httpx.AsyncClient, url: str, **kw: Any) -> Any:
        await self._pace()
        start = time.perf_counter()
        try:
            resp = await client.get(url, **kw)
        except httpx.HTTPError as exc:
            self._fail(f"{type(exc).__name__}: {exc}"[:200], blocked=False)
            raise ProviderError(str(exc)) from exc
        ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code in (403, 429):
            self._fail(f"HTTP {resp.status_code}", blocked=True)
            raise ProviderBlocked(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            self._fail(f"HTTP {resp.status_code}", blocked=False)
            raise ProviderError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            self._fail("invalid JSON", blocked=False)
            raise ProviderError("invalid JSON") from exc
        self._ok(ms)
        return data

    # ------------------------------------------------------------- queries
    async def query(self, client: httpx.AsyncClient, kind: str, **params: Any) -> list[Aircraft]:  # pragma: no cover
        raise Unsupported(kind)


# --------------------------------------------------------------------------- helpers


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3440.065  # Earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def clean_callsign(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip().upper()
    return s or None


EMERGENCY_SQUAWKS = {"7500": "unlawful", "7600": "nordo", "7700": "general"}
