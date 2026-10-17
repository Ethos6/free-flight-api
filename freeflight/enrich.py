"""Enrichment: flight routes and aircraft details (adsbdb.com) and photos (planespotters.net)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .models import AircraftInfo, Airport, Route


class TTLCache:
    def __init__(self, ttl: float, max_items: int = 20000) -> None:
        self.ttl, self.max_items = ttl, max_items
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        hit = self._d.get(key)
        if hit and hit[0] > time.monotonic():
            return True, hit[1]
        return False, None

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        if len(self._d) >= self.max_items:
            now = time.monotonic()
            self._d = {k: v for k, v in self._d.items() if v[0] > now}
            if len(self._d) >= self.max_items:
                self._d.clear()
        self._d[key] = (time.monotonic() + (self.ttl if ttl is None else ttl), value)


def _airport(a: dict | None) -> Airport | None:
    if not a:
        return None
    return Airport(
        name=a.get("name"),
        iata=a.get("iata_code"),
        icao=a.get("icao_code"),
        city=a.get("municipality"),
        country=a.get("country_name"),
        lat=a.get("latitude"),
        lon=a.get("longitude"),
    )


def parse_adsbdb_route(data: dict, callsign: str) -> Route | None:
    resp = data.get("response") if isinstance(data, dict) else None
    fr = resp.get("flightroute") if isinstance(resp, dict) else None
    if not isinstance(fr, dict):
        return None
    airline = fr.get("airline") or {}
    return Route(
        callsign=callsign,
        airline=airline.get("name"),
        airline_iata=airline.get("iata"),
        airline_icao=airline.get("icao"),
        flight_iata=fr.get("callsign_iata"),
        origin=_airport(fr.get("origin")),
        destination=_airport(fr.get("destination")),
    )


def parse_adsbdb_aircraft(data: dict, hex_: str) -> AircraftInfo | None:
    resp = data.get("response") if isinstance(data, dict) else None
    ac = resp.get("aircraft") if isinstance(resp, dict) else None
    if not isinstance(ac, dict):
        return None
    return AircraftInfo(
        hex=hex_,
        registration=ac.get("registration"),
        type=ac.get("type"),
        icao_type=ac.get("icao_type"),
        manufacturer=ac.get("manufacturer"),
        owner=ac.get("registered_owner"),
        country=ac.get("registered_owner_country_name"),
        photo=ac.get("url_photo"),
        photo_thumbnail=ac.get("url_photo_thumbnail"),
    )


def parse_planespotters(data: dict) -> dict | None:
    photos = (data or {}).get("photos") or []
    if not photos:
        return None
    p = photos[0]
    return {
        "photo": (p.get("thumbnail_large") or {}).get("src"),
        "photo_thumbnail": (p.get("thumbnail") or {}).get("src"),
        "photo_credit": p.get("photographer"),
        "photo_link": p.get("link"),
    }


class Enricher:
    def __init__(self, ttl: float = 86400, photos: bool = True) -> None:
        self.cache = TTLCache(ttl)
        self.photos = photos

    async def _get(self, client: httpx.AsyncClient, url: str) -> Any:
        try:
            r = await client.get(url, timeout=8)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def route(self, client: httpx.AsyncClient, callsign: str) -> Route | None:
        cs = callsign.strip().upper()
        hit, val = self.cache.get(f"route:{cs}")
        if hit:
            return val
        data = await self._get(client, f"https://api.adsbdb.com/v0/callsign/{cs}")
        route = parse_adsbdb_route(data, cs) if data else None
        self.cache.set(f"route:{cs}", route, None if route else 3600)
        return route

    async def aircraft(self, client: httpx.AsyncClient, hex_: str) -> AircraftInfo | None:
        h = hex_.strip().lower()
        hit, val = self.cache.get(f"ac:{h}")
        if hit:
            return val
        data = await self._get(client, f"https://api.adsbdb.com/v0/aircraft/{h}")
        info = parse_adsbdb_aircraft(data, h) if data else None
        if self.photos:
            ps = await self._get(client, f"https://api.planespotters.net/pub/photos/hex/{h}")
            extra = parse_planespotters(ps) if ps else None
            if extra:
                info = (info or AircraftInfo(hex=h)).model_copy(update={k: v for k, v in extra.items() if v})
        self.cache.set(f"ac:{h}", info, None if info else 3600)
        return info
