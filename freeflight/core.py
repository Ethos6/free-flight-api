"""FreeFlight: queries several free ADS-B networks, merges them, caches and coalesces requests.

Many clients asking about the same area share one upstream request (request coalescing +
short cache), which is what lets a single server answer unlimited API calls while staying
inside every network's polite rate limit.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from . import __version__
from .config import Settings
from .enrich import Enricher, TTLCache
from .models import Aircraft, FlightsResponse, ProviderReport
from .providers import (
    READSB_PROVIDERS,
    OpenSky,
    Provider,
    ProviderBlocked,
    ProviderBusy,
    ProviderError,
    Unsupported,
    haversine_nm,
)
from .providers.base import USER_AGENT

MAX_RADIUS_NM = 250
RADIUS_BUCKETS = (25, 50, 100, 150, 200, 250)


class NoData(Exception):
    """No provider could answer."""


def merge_aircraft(lists: list[list[Aircraft]]) -> list[Aircraft]:
    """Merge by ICAO hex. The freshest position wins; missing fields are filled from other networks."""
    merged: dict[str, Aircraft] = {}
    for items in lists:
        for a in items:
            cur = merged.get(a.hex)
            if cur is None:
                merged[a.hex] = a.model_copy(update={"providers": list(a.providers)})
                continue
            fresher = (a.seen_pos_s if a.seen_pos_s is not None else 1e9) < (cur.seen_pos_s if cur.seen_pos_s is not None else 1e9)
            base, other = (a, cur) if fresher else (cur, a)
            data = base.model_dump()
            for k, v in other.model_dump().items():
                if data.get(k) in (None, "", False) and v not in (None, "", False):
                    data[k] = v
            data["providers"] = sorted(set(cur.providers) | set(a.providers))
            merged[a.hex] = Aircraft(**data)
    return list(merged.values())


def bbox_center_radius(lamin: float, lomin: float, lamax: float, lomax: float) -> tuple[float, float, float]:
    lat = (lamin + lamax) / 2
    lon = (lomin + lomax) / 2
    radius = max(haversine_nm(lat, lon, la, lo) for la in (lamin, lamax) for lo in (lomin, lomax))
    return lat, lon, radius


def in_bbox(a: Aircraft, lamin: float, lomin: float, lamax: float, lomax: float) -> bool:
    return a.lat is not None and a.lon is not None and lamin <= a.lat <= lamax and lomin <= a.lon <= lomax


class FreeFlight:
    def __init__(self, settings: Settings | None = None, providers: list[Provider] | None = None, opensky: OpenSky | None = None):
        self.settings = settings or Settings.from_env()
        s = self.settings
        if providers is None:
            providers = [READSB_PROVIDERS[n]() for n in s.providers if n in READSB_PROVIDERS]
        self.providers: dict[str, Provider] = {p.name: p for p in providers}
        if opensky is None and s.opensky_enabled:
            opensky = OpenSky(s.opensky_client_id, s.opensky_client_secret)
        self.opensky = opensky
        self.cache = TTLCache(s.cache_ttl, max_items=5000)
        self.enricher = Enricher(s.enrich_ttl, s.photos)
        self._inflight: dict[str, asyncio.Task] = {}
        self._world: tuple[float, FlightsResponse] | None = None
        self._world_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.settings.timeout,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> FreeFlight:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------- plumbing
    async def _shared(self, key: str, ttl: float, factory: Callable[[], Awaitable[FlightsResponse]]) -> FlightsResponse:
        """Cache + request coalescing: concurrent identical requests share one upstream call."""
        hit, val = self.cache.get(key)
        if hit:
            return val.model_copy(update={"cached": True})
        task = self._inflight.get(key)
        if task is None:

            async def run() -> FlightsResponse:
                try:
                    res = await factory()
                    self.cache.set(key, res, ttl if res.count else min(ttl, 2.0))
                    return res
                finally:
                    self._inflight.pop(key, None)

            task = asyncio.create_task(run())
            self._inflight[key] = task
        return await asyncio.shield(task)

    async def _ask(self, provider: Provider, kind: str, params: dict[str, Any]) -> tuple[list[Aircraft], ProviderReport]:
        start = time.perf_counter()
        try:
            res = await provider.query(self.client, kind, **params)
            return res, ProviderReport(name=provider.name, ok=True, aircraft=len(res), elapsed_ms=int((time.perf_counter() - start) * 1000))
        except ProviderBusy as exc:
            return [], ProviderReport(name=provider.name, ok=False, skipped=True, error=str(exc))
        except (ProviderBlocked, ProviderError, Unsupported) as exc:
            return [], ProviderReport(
                name=provider.name, ok=False, error=str(exc)[:200], elapsed_ms=int((time.perf_counter() - start) * 1000)
            )
        except Exception as exc:  # noqa: BLE001 - never let one network break a request
            provider._fail(f"{type(exc).__name__}: {exc}"[:200], blocked=False)
            return [], ProviderReport(name=provider.name, ok=False, error=f"{type(exc).__name__}: {exc}"[:200])

    async def _query(self, kind: str, params: dict[str, Any], mode: str = "merge") -> FlightsResponse:
        capable = [p for p in self.providers.values() if kind in p.kinds]
        if not capable:
            raise NoData(f"no provider supports {kind!r}")
        ready = [p for p in capable if p.available()] or capable
        reports: list[ProviderReport] = [
            ProviderReport(name=p.name, ok=False, skipped=True, error="cooling down") for p in capable if p not in ready
        ]
        lists: list[list[Aircraft]] = []
        if mode == "fast":
            for p in ready:  # in priority order; stop at the first that answers
                res, rep = await self._ask(p, kind, params)
                reports.append(rep)
                if rep.ok:
                    lists.append(res)
                    break
        else:
            for res, rep in await asyncio.gather(*(self._ask(p, kind, params) for p in ready)):
                reports.append(rep)
                if rep.ok:
                    lists.append(res)
        if not any(r.ok for r in reports):
            raise NoData("all providers failed: " + "; ".join(f"{r.name}: {r.error}" for r in reports))
        aircraft = merge_aircraft(lists)
        return FlightsResponse(count=len(aircraft), aircraft=aircraft, providers=reports, timestamp=time.time())

    # ------------------------------------------------------------- public API
    async def nearby(self, lat: float, lon: float, radius_nm: float = 50, mode: str = "merge") -> FlightsResponse:
        radius_nm = max(1.0, min(float(radius_nm), MAX_RADIUS_NM))
        # Snap to a grid so nearby users share upstream calls; then filter exactly.
        g_lat, g_lon = round(lat * 4) / 4, round(lon * 4) / 4
        need = radius_nm + haversine_nm(lat, lon, g_lat, g_lon)
        g_radius = next((b for b in RADIUS_BUCKETS if b >= need), MAX_RADIUS_NM)
        key = f"point:{g_lat}:{g_lon}:{g_radius}:{mode}"
        res = await self._shared(
            key, self.settings.cache_ttl, lambda: self._query("point", {"lat": g_lat, "lon": g_lon, "radius": g_radius}, mode)
        )
        out = []
        for a in res.aircraft:
            d = haversine_nm(lat, lon, a.lat, a.lon)  # type: ignore[arg-type]
            if d <= radius_nm:
                out.append(a.model_copy(update={"distance_nm": round(d, 2)}))
        out.sort(key=lambda a: a.distance_nm or 0)
        return res.model_copy(update={"aircraft": out, "count": len(out)})

    async def bbox(self, lamin: float, lomin: float, lamax: float, lomax: float) -> FlightsResponse:
        lamin, lamax = sorted((lamin, lamax))
        lomin, lomax = sorted((lomin, lomax))
        lat, lon, radius = bbox_center_radius(lamin, lomin, lamax, lomax)
        if radius <= MAX_RADIUS_NM and self.providers:
            try:
                res = await self.nearby(lat, lon, radius)
                inside = [a.model_copy(update={"distance_nm": None}) for a in res.aircraft if in_bbox(a, lamin, lomin, lamax, lomax)]
                return res.model_copy(update={"aircraft": inside, "count": len(inside)})
            except NoData:
                pass
        res = await self.world()
        inside = [a for a in res.aircraft if in_bbox(a, lamin, lomin, lamax, lomax)]
        return res.model_copy(update={"aircraft": inside, "count": len(inside)})

    async def world(self) -> FlightsResponse:
        """Every aircraft currently tracked worldwide (OpenSky global snapshot, cached)."""
        if self.opensky is None:
            raise NoData("world snapshot needs OpenSky (FREEFLIGHT_OPENSKY=true)")
        ttl = self.settings.world_refresh
        if self._world and time.time() - self._world[0] < ttl:
            return self._world[1].model_copy(update={"cached": True})
        async with self._world_lock:
            if self._world and time.time() - self._world[0] < ttl:
                return self._world[1].model_copy(update={"cached": True})
            res, rep = await self._ask(self.opensky, "world", {})
            if rep.ok:
                snap = FlightsResponse(count=len(res), aircraft=res, providers=[rep], timestamp=time.time())
                self._world = (time.time(), snap)
                return snap
            if self._world:  # serve the last good snapshot rather than nothing
                stale = self._world[1]
                return stale.model_copy(update={"cached": True, "providers": [*stale.providers, rep]})
            raise NoData(f"OpenSky unavailable: {rep.error}")

    async def lookup(self, kind: str, value: str) -> FlightsResponse:
        value = value.strip()
        norm = value.lower() if kind == "hex" else value.upper()
        key = f"{kind}:{norm}"

        async def fetch() -> FlightsResponse:
            try:
                res = await self._query(kind, {kind: norm})
            except NoData:
                res = FlightsResponse(count=0, aircraft=[], timestamp=time.time())
            if kind == "hex" and not res.count and self.opensky is not None:
                extra, rep = await self._ask(self.opensky, "hex", {"hex": norm})
                res = res.model_copy(update={"aircraft": extra, "count": len(extra), "providers": [*res.providers, rep]})
            return res

        return await self._shared(key, self.settings.cache_ttl, fetch)

    async def military(self) -> FlightsResponse:
        return await self._shared("military", 15, lambda: self._query("military", {}, mode="fast"))

    async def squawk(self, code: str) -> FlightsResponse:
        return await self._shared(f"squawk:{code}", 10, lambda: self._query("squawk", {"squawk": code}, mode="fast"))

    async def emergencies(self) -> FlightsResponse:
        async def fetch() -> FlightsResponse:
            results = [await self.squawk(c) for c in ("7500", "7600", "7700")]
            aircraft = merge_aircraft([r.aircraft for r in results])
            reports = [rep for r in results for rep in r.providers]
            return FlightsResponse(count=len(aircraft), aircraft=aircraft, providers=reports, timestamp=time.time())

        return await self._shared("emergencies", 15, fetch)

    async def track(self, ident: str) -> dict[str, Any]:
        """Everything about one flight: live position (by callsign or hex), route, aircraft details, photo."""
        ident = ident.strip()
        is_hex = len(ident) == 6 and all(c in "0123456789abcdefABCDEF" for c in ident)
        res = await self.lookup("hex" if is_hex else "callsign", ident)
        if not res.count and is_hex:
            res = await self.lookup("callsign", ident)
        ac = res.aircraft[0] if res.aircraft else None
        callsign = (ac.callsign if ac else None) or (None if is_hex else ident.upper())
        hex_ = ac.hex if ac else (ident.lower() if is_hex else None)
        route, info = await asyncio.gather(
            self.enricher.route(self.client, callsign) if callsign else _none(),
            self.enricher.aircraft(self.client, hex_) if hex_ else _none(),
        )
        return {
            "query": ident,
            "live": ac.model_dump() if ac else None,
            "route": route.model_dump() if route else None,
            "aircraft": info.model_dump() if info else None,
            "providers": [r.model_dump() for r in res.providers],
        }

    def status(self) -> dict[str, Any]:
        provs = [p.status() for p in self.providers.values()]
        if self.opensky is not None:
            provs.append(
                {**self.opensky.status(), "authenticated": self.opensky.authenticated, "world_refresh_seconds": self.settings.world_refresh}
            )
        return {
            "version": __version__,
            "providers": provs,
            "world_snapshot_age_s": round(time.time() - self._world[0]) if self._world else None,
            "world_snapshot_aircraft": self._world[1].count if self._world else None,
        }


async def _none() -> None:
    return None
