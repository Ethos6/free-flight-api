"""Community ADS-B networks that speak the readsb / ADSBExchange v2 JSON schema:
adsb.lol, airplanes.live and adsb.fi. Each one is fed by thousands of volunteer receivers."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from ..models import Aircraft
from .base import EMERGENCY_SQUAWKS, Provider, ProviderError, Unsupported, clean_callsign


def _int(v: Any) -> int | None:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _source(t: str | None) -> str | None:
    if not t:
        return None
    for key in ("adsb", "mlat", "tisb", "adsc", "adsr"):
        if t.startswith(key):
            return "adsb" if key == "adsr" else key
    return "other"


def parse_readsb(data: dict[str, Any], provider: str) -> list[Aircraft]:
    """Parse a readsb/ADSBx-v2 response ({"ac": [...]} or {"aircraft": [...]})."""
    records = data.get("ac")
    if records is None:
        records = data.get("aircraft")
    if records is None:
        raise ProviderError("unexpected response shape")
    out: list[Aircraft] = []
    for r in records or []:
        hex_ = str(r.get("hex", "")).lower().lstrip("~")
        lat, lon = _float(r.get("lat")), _float(r.get("lon"))
        if not hex_ or lat is None or lon is None:
            continue  # no current position
        alt = r.get("alt_baro")
        on_ground = alt == "ground"
        squawk = r.get("squawk")
        emergency = r.get("emergency")
        if emergency in (None, "none", ""):
            emergency = EMERGENCY_SQUAWKS.get(squawk or "")
        out.append(
            Aircraft(
                hex=hex_,
                callsign=clean_callsign(r.get("flight")),
                registration=r.get("r") or None,
                type=r.get("t") or None,
                description=r.get("desc") or None,
                lat=round(lat, 6),
                lon=round(lon, 6),
                altitude_ft=0 if on_ground else _int(alt),
                geo_altitude_ft=_int(r.get("alt_geom")),
                on_ground=on_ground,
                ground_speed_kt=_float(r.get("gs")),
                track=_float(r.get("track") if r.get("track") is not None else r.get("true_heading")),
                vertical_rate_fpm=_int(r.get("baro_rate") if r.get("baro_rate") is not None else r.get("geom_rate")),
                squawk=squawk,
                emergency=emergency,
                category=r.get("category"),
                military=bool((_int(r.get("dbFlags")) or 0) & 1),
                position_source=_source(r.get("type")),
                seen_pos_s=_float(r.get("seen_pos")),
                distance_nm=_float(r.get("dst")),
                provider=provider,
                providers=[provider],
            ).fill_units()
        )
    return out


class ReadsbProvider(Provider):
    base: ClassVar[str]
    paths: ClassVar[dict[str, str]]

    @property
    def kinds(self) -> set[str]:  # type: ignore[override]
        return set(self.paths)

    async def query(self, client: httpx.AsyncClient, kind: str, **p: Any) -> list[Aircraft]:
        tmpl = self.paths.get(kind)
        if tmpl is None:
            raise Unsupported(f"{self.name} does not support {kind}")
        if kind == "point":
            p = {**p, "radius": min(int(round(p["radius"])), 250), "lat": round(p["lat"], 4), "lon": round(p["lon"], 4)}
        url = self.base + tmpl.format(**p)
        data = await self.get_json(client, url)
        return parse_readsb(data, self.name)


class AdsbLol(ReadsbProvider):
    name = "adsb.lol"
    url = "https://adsb.lol"
    license = "ODbL 1.0"
    base = "https://api.adsb.lol/v2"
    min_interval = 1.0
    paths: ClassVar[dict[str, str]] = {
        "point": "/point/{lat}/{lon}/{radius}",
        "hex": "/hex/{hex}",
        "callsign": "/callsign/{callsign}",
        "registration": "/reg/{registration}",
        "type": "/type/{type}",
        "military": "/mil",
        "squawk": "/sqk/{squawk}",
    }


class AirplanesLive(ReadsbProvider):
    name = "airplanes.live"
    url = "https://airplanes.live"
    license = "non-commercial use"
    base = "https://api.airplanes.live/v2"
    min_interval = 1.1
    paths: ClassVar[dict[str, str]] = {
        "point": "/point/{lat}/{lon}/{radius}",
        "hex": "/hex/{hex}",
        "callsign": "/callsign/{callsign}",
        "registration": "/reg/{registration}",
        "type": "/type/{type}",
        "military": "/mil",
        "squawk": "/squawk/{squawk}",
    }


class AdsbFi(ReadsbProvider):
    name = "adsb.fi"
    url = "https://adsb.fi"
    license = "non-commercial use, attribution required"
    base = "https://opendata.adsb.fi/api/v2"
    min_interval = 1.1
    paths: ClassVar[dict[str, str]] = {
        "point": "/lat/{lat}/lon/{lon}/dist/{radius}",
        "hex": "/hex/{hex}",
        "callsign": "/callsign/{callsign}",
        "registration": "/registration/{registration}",
        "military": "/mil",
        "squawk": "/sqk/{squawk}",
    }
