import asyncio

import pytest

from freeflight.core import NoData, merge_aircraft
from freeflight.models import Aircraft


def test_merge_prefers_freshest_and_fills_gaps():
    a = Aircraft(hex="abc", lat=1, lon=1, seen_pos_s=5, registration="G-ABC", provider="x", providers=["x"])
    b = Aircraft(hex="abc", lat=2, lon=2, seen_pos_s=1, callsign="TEST1", provider="y", providers=["y"])
    m = merge_aircraft([[a], [b]])[0]
    assert m.lat == 2 and m.callsign == "TEST1" and m.registration == "G-ABC"
    assert m.providers == ["x", "y"]


async def test_nearby_merges_networks_and_sorts(ff, upstream):
    res = await ff.nearby(51.47, -0.46, 40)
    hexes = [a.hex for a in res.aircraft]
    assert "3c6444" in hexes  # only on adsb.fi
    baw = next(a for a in res.aircraft if a.hex == "406b0f")
    assert sorted(baw.providers) == ["adsb.fi", "adsb.lol"]
    assert baw.lat == 51.4701  # adsb.fi was fresher (seen_pos 0.1)
    d = [a.distance_nm for a in res.aircraft]
    assert d == sorted(d) and all(x <= 40 for x in d)
    assert {r.name for r in res.providers} == {"adsb.lol", "airplanes.live", "adsb.fi"}


async def test_cache_and_coalescing(ff, upstream):
    await asyncio.gather(*(ff.nearby(51.47, -0.46, 40) for _ in range(20)))
    point_calls = [u for u in upstream.calls if "/point/" in u or "/dist/" in u]
    assert len(point_calls) == 3  # one per provider for 20 concurrent clients
    again = await ff.nearby(51.48, -0.45, 35)  # same grid cell -> cache hit
    assert again.cached
    assert len([u for u in upstream.calls if "/point/" in u or "/dist/" in u]) == 3


async def test_failover_when_provider_blocked(ff, upstream):
    upstream.fail = {"adsb.lol": 429}
    res = await ff.nearby(51.47, -0.46, 40)
    assert res.count >= 2
    rep = {r.name: r for r in res.providers}
    assert not rep["adsb.lol"].ok and rep["adsb.fi"].ok
    assert not ff.providers["adsb.lol"].available()


async def test_all_fail_raises(ff, upstream):
    upstream.fail = {"adsb.lol": 500, "airplanes.live": 500, "adsb.fi": 500}
    with pytest.raises(NoData):
        await ff.nearby(10, 10, 50)


async def test_bbox_small_uses_networks_large_uses_world(ff, upstream):
    small = await ff.bbox(51.3, -1.2, 51.9, 0.0)
    assert small.count and all(51.3 <= a.lat <= 51.9 for a in small.aircraft)
    big = await ff.bbox(30, -80, 60, 10)
    assert {a.hex for a in big.aircraft} == {"4ca7b5", "a0f1b2"}
    assert any("opensky" in u for u in upstream.calls)


async def test_world_snapshot_is_cached(ff, upstream):
    w1 = await ff.world()
    w2 = await ff.world()
    assert w1.count == 2 and w2.cached
    assert len([u for u in upstream.calls if "opensky" in u]) == 1


async def test_world_serves_stale_on_failure(ff, upstream):
    await ff.world()
    ff._world = (ff._world[0] - 10_000, ff._world[1])
    upstream.fail = {"opensky": 503}
    w = await ff.world()
    assert w.count == 2 and w.cached


async def test_lookup_hex_falls_back_to_opensky(ff, upstream):
    upstream.fail = {"adsb.lol": 404, "airplanes.live": 404, "adsb.fi": 404}
    res = await ff.lookup("hex", "A0F1B2")
    assert res.count == 2  # opensky fixture returns its snapshot for any icao24 query
    assert any("icao24=a0f1b2" in u for u in upstream.calls)


async def test_emergencies_and_military(ff):
    em = await ff.emergencies()
    assert [a.hex for a in em.aircraft] == ["406b0f"]
    mil = await ff.military()
    assert [a.hex for a in mil.aircraft] == ["43c6f1"]


async def test_track_combines_live_route_and_photo(ff):
    t = await ff.track("BAW117")
    assert t["live"]["hex"] == "406b0f"
    assert t["route"]["origin"]["iata"] == "LHR"
    assert t["aircraft"]["owner"] == "British Airways"
    assert t["aircraft"]["photo_credit"] == "Jane Spotter"
