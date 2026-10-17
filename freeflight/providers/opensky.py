"""OpenSky Network: the only free source with a single global snapshot of every tracked aircraft.

Anonymous use is limited to a daily credit budget (a global query costs 4 credits), so
FreeFlight caches the world snapshot. Add free OpenSky API client credentials
(FREEFLIGHT_OPENSKY_CLIENT_ID / _SECRET) for a 10x bigger budget.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..models import FT_PER_M, KT_PER_MS, Aircraft
from .base import EMERGENCY_SQUAWKS, Provider, ProviderError, Unsupported, clean_callsign

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API = "https://opensky-network.org/api"
SOURCES = {0: "adsb", 1: "other", 2: "mlat", 3: "flarm"}
CATEGORIES = {
    2: "A1",
    3: "A2",
    4: "A3",
    5: "A4",
    6: "A5",
    7: "A6",
    8: "A7",
    9: "B1",
    10: "B2",
    11: "B3",
    12: "B4",
    14: "B6",
    15: "B7",
    16: "C1",
    17: "C2",
    18: "C3",
}


def parse_states(data: dict[str, Any], provider: str = "opensky") -> list[Aircraft]:
    if not isinstance(data, dict) or "states" not in data:
        raise ProviderError("unexpected response shape")
    now = data.get("time") or time.time()
    out: list[Aircraft] = []
    for s in data.get("states") or []:
        if len(s) < 17 or s[5] is None or s[6] is None:
            continue
        baro_m, vel, vrate, geo_m = s[7], s[9], s[11], s[13]
        squawk = s[14]
        out.append(
            Aircraft(
                hex=str(s[0]).lower(),
                callsign=clean_callsign(s[1]),
                origin_country=s[2] or None,
                lat=round(s[6], 6),
                lon=round(s[5], 6),
                altitude_ft=round(baro_m * FT_PER_M) if baro_m is not None else (0 if s[8] else None),
                geo_altitude_ft=round(geo_m * FT_PER_M) if geo_m is not None else None,
                on_ground=bool(s[8]),
                ground_speed_kt=round(vel * KT_PER_MS, 1) if vel is not None else None,
                track=s[10],
                vertical_rate_fpm=round(vrate * FT_PER_M * 60) if vrate is not None else None,
                squawk=squawk,
                emergency=EMERGENCY_SQUAWKS.get(squawk or ""),
                category=CATEGORIES.get(s[17]) if len(s) > 17 else None,
                position_source=SOURCES.get(s[16]),
                seen_pos_s=round(max(0.0, now - s[3]), 1) if s[3] else None,
                provider=provider,
                providers=[provider],
            ).fill_units()
        )
    return out


class OpenSky(Provider):
    name = "opensky"
    url = "https://opensky-network.org"
    license = "non-commercial research use"
    kinds = {"world", "bbox", "hex"}  # noqa: RUF012
    min_interval = 5.0

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, max_wait: float = 6.0) -> None:
        super().__init__(max_wait=max_wait)
        self.client_id, self.client_secret = client_id, client_secret
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def authenticated(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        if not self.authenticated:
            return {}
        if not self._token or time.time() > self._token_exp - 60:
            resp = await client.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret},
            )
            if resp.status_code != 200:
                raise ProviderError(f"OpenSky token request failed: HTTP {resp.status_code}")
            body = resp.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + float(body.get("expires_in", 1800))
        return {"Authorization": f"Bearer {self._token}"}

    async def query(self, client: httpx.AsyncClient, kind: str, **p: Any) -> list[Aircraft]:
        params: dict[str, Any] = {"extended": 1}
        if kind == "bbox":
            params.update(lamin=p["lamin"], lomin=p["lomin"], lamax=p["lamax"], lomax=p["lomax"])
        elif kind == "hex":
            params["icao24"] = p["hex"]
        elif kind != "world":
            raise Unsupported(f"opensky does not support {kind}")
        data = await self.get_json(client, f"{API}/states/all", params=params, headers=await self._headers(client))
        return parse_states(data, self.name)
