import asyncio
import time

import pytest
from conftest import load

from freeflight.enrich import parse_adsbdb_aircraft, parse_adsbdb_route, parse_planespotters
from freeflight.providers import AdsbLol, ProviderBusy, ProviderError, haversine_nm, parse_readsb, parse_states


def test_parse_readsb():
    ac = {a.hex: a for a in parse_readsb(load("adsb_lol_point.json"), "adsb.lol")}
    assert "400aaa" not in ac  # no position -> skipped
    assert "2cc001" in ac and ac["2cc001"].position_source == "tisb"  # "~" stripped
    baw = ac["406b0f"]
    assert baw.callsign == "BAW117" and baw.registration == "G-XWBA" and baw.type == "A35K"
    assert baw.altitude_ft == 12000 and baw.altitude_m == 3658 and baw.speed_kmh == 574.5
    assert baw.emergency == "general" and baw.position_source == "adsb"
    assert ac["43c6f1"].military is True
    gnd = ac["4ca7b5"]
    assert gnd.on_ground and gnd.altitude_ft == 0
    assert ac["401eb3"].position_source == "mlat"


def test_parse_readsb_aircraft_key_and_errors():
    assert len(parse_readsb(load("adsbfi_point.json"), "adsb.fi")) == 2
    with pytest.raises(ProviderError):
        parse_readsb({"weird": 1}, "x")


def test_parse_opensky():
    ac = {a.hex: a for a in parse_states(load("opensky_states.json"))}
    assert set(ac) == {"4ca7b5", "a0f1b2"}  # null position skipped
    aal = ac["a0f1b2"]
    assert aal.callsign == "AAL100" and aal.origin_country == "United States"
    assert aal.altitude_ft == 35000  # 10668 m
    assert aal.ground_speed_kt == 450.0  # 231.5 m/s
    assert aal.vertical_rate_fpm == 1000
    assert aal.category == "A5"
    assert ac["4ca7b5"].on_ground and ac["4ca7b5"].altitude_ft == 0


def test_enrichment_parsers():
    r = parse_adsbdb_route(load("adsbdb_callsign.json"), "BAW117")
    assert r.origin.iata == "LHR" and r.destination.iata == "JFK" and r.airline == "British Airways"
    i = parse_adsbdb_aircraft(load("adsbdb_aircraft.json"), "406b0f")
    assert i.registration == "G-XWBA" and i.owner == "British Airways"
    p = parse_planespotters(load("planespotters.json"))
    assert p["photo_credit"] == "Jane Spotter"
    assert parse_adsbdb_route({"response": "unknown callsign"}, "X") is None


def test_haversine():
    assert abs(haversine_nm(51.47, -0.46, 40.64, -73.78) - 2999) < 15  # LHR-JFK ≈ 2,999 nm


async def test_pacing_respects_rate_limit():
    p = AdsbLol(max_wait=0.5)
    p.min_interval = 0.3
    t0 = time.monotonic()
    await p._pace()
    await p._pace()
    assert time.monotonic() - t0 >= 0.29
    p.min_interval = 5
    await p._pace()
    with pytest.raises(ProviderBusy):
        await p._pace()  # would have to wait > max_wait


def test_circuit_breaker():
    p = AdsbLol()
    p._fail("HTTP 429", blocked=True)
    assert not p.available() and p.status()["cooldown_seconds"] > 0
    asyncio.run(asyncio.sleep(0))
