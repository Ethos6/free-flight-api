"""Offline test harness: every upstream API is served from fixtures through httpx.MockTransport."""

import json
from pathlib import Path

import httpx
import pytest

from freeflight.config import Settings
from freeflight.core import FreeFlight
from freeflight.providers import AdsbFi, AdsbLol, AirplanesLive, OpenSky

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class Upstream:
    """Fake internet. Set .fail = {"airplanes.live": 429} to simulate outages."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        host = request.url.host
        for name, code in self.fail.items():
            if name in host:
                return httpx.Response(code, json={})
        for seg, field in (("/callsign/", "flight"), ("/hex/", "hex"), ("/reg/", "r"), ("/registration/", "r"), ("/type/", "t")):
            if seg in url and ("adsb.lol" in host or "adsb.fi" in host):
                want = url.rsplit("/", 1)[1].upper()
                d = load("adsb_lol_point.json") if "adsb.lol" in host else load("adsbfi_point.json")
                key = "ac" if "ac" in d else "aircraft"
                d[key] = [a for a in d[key] if str(a.get(field, "")).strip().upper() == want]
                return httpx.Response(200, json=d)
        if "api.adsb.lol" in host:
            if "/sqk/7700" in url:
                d = load("adsb_lol_point.json")
                d["ac"] = [a for a in d["ac"] if a.get("squawk") == "7700"]
                return httpx.Response(200, json=d)
            if "/sqk/" in url:
                return httpx.Response(200, json={"ac": [], "total": 0})
            if "/mil" in url:
                d = load("adsb_lol_point.json")
                d["ac"] = [a for a in d["ac"] if a.get("dbFlags") == 1]
                return httpx.Response(200, json=d)
            return httpx.Response(200, json=load("adsb_lol_point.json"))
        if "airplanes.live" in host:
            return httpx.Response(200, json={"ac": [], "total": 0, "now": 0})
        if "adsb.fi" in host:
            return httpx.Response(200, json=load("adsbfi_point.json"))
        if "opensky-network.org" in host:
            return httpx.Response(200, json=load("opensky_states.json"))
        if "adsbdb.com" in host:
            if "/callsign/" in url:
                return httpx.Response(200, json=load("adsbdb_callsign.json"))
            return httpx.Response(200, json=load("adsbdb_aircraft.json"))
        if "planespotters.net" in host:
            return httpx.Response(200, json=load("planespotters.json"))
        return httpx.Response(404, json={})


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def settings() -> Settings:
    return Settings(cache_ttl=5, world_ttl=60)


@pytest.fixture
def ff(settings, upstream) -> FreeFlight:
    providers = [AdsbLol(), AirplanesLive(), AdsbFi()]
    opensky = OpenSky()
    for p in [*providers, opensky]:
        p.min_interval = 0  # no pacing in tests
    inst = FreeFlight(settings, providers=providers, opensky=opensky)
    inst._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    return inst
