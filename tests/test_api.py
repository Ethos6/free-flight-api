import pytest
from fastapi.testclient import TestClient

from freeflight.api import create_app


@pytest.fixture
def client(ff, settings):
    with TestClient(create_app(settings, ff=ff)) as c:
        yield c


def test_meta(client):
    assert client.get("/health").json()["status"] == "ok"
    assert "leaflet" in client.get("/").text.lower()
    assert client.get("/openapi.json").status_code == 200
    names = [p["name"] for p in client.get("/v1/providers").json()["providers"]]
    assert names == ["adsb.lol", "airplanes.live", "adsb.fi", "opensky"]


def test_nearby_with_filters(client):
    r = client.get("/v1/flights/nearby", params={"lat": 51.47, "lon": -0.46, "radius_nm": 40})
    assert r.status_code == 200 and r.json()["count"] >= 4
    air = client.get(
        "/v1/flights/nearby", params={"lat": 51.47, "lon": -0.46, "radius_nm": 40, "on_ground": False, "min_alt": 10000}
    ).json()
    assert air["aircraft"] and all(a["altitude_ft"] >= 10000 and not a["on_ground"] for a in air["aircraft"])
    t = client.get("/v1/flights/nearby", params={"lat": 51.47, "lon": -0.46, "radius_nm": 40, "type": "a35k"}).json()
    assert [a["hex"] for a in t["aircraft"]] == ["406b0f"]
    assert client.get("/v1/flights/nearby", params={"lat": 100, "lon": 0}).status_code == 422


def test_bbox_world_military_emergency(client):
    assert client.get("/v1/flights/bbox", params={"lamin": 51.3, "lomin": -1.2, "lamax": 51.9, "lomax": 0}).json()["count"]
    assert client.get("/v1/flights/world").json()["count"] == 2
    assert client.get("/v1/flights/military").json()["aircraft"][0]["military"] is True
    assert client.get("/v1/flights/emergencies").json()["aircraft"][0]["squawk"] == "7700"
    assert client.get("/v1/flights/squawk/77").status_code == 400


def test_lookups(client):
    assert client.get("/v1/callsign/baw117").json()["aircraft"][0]["hex"] == "406b0f"
    assert client.get("/v1/aircraft/406B0F").json()["count"] >= 1
    t = client.get("/v1/track/BAW117").json()
    assert t["route"]["destination"]["iata"] == "JFK"
    assert client.get("/v1/route/BAW117").json()["airline"] == "British Airways"
    assert client.get("/v1/aircraft/406b0f/info").json()["registration"] == "G-XWBA"


def test_opensky_compat(client):
    body = client.get("/api/states/all").json()
    assert "time" in body and len(body["states"]) == 2
    s = next(x for x in body["states"] if x[0] == "a0f1b2")
    assert len(s) == 18 and s[1].strip() == "AAL100" and abs(s[7] - 10668) < 1
    box = client.get("/api/states/all", params={"lamin": 51.3, "lomin": -1.2, "lamax": 51.9, "lomax": 0}).json()
    assert box["states"]


def test_adsbx_v2_compat(client):
    body = client.get("/v2/point/51.47/-0.46/40").json()
    assert body["msg"] == "No error" and body["total"] == len(body["ac"])
    rec = next(a for a in body["ac"] if a["hex"] == "406b0f")
    assert rec["flight"].strip() == "BAW117" and rec["t"] == "A35K"
    gnd = next(a for a in body["ac"] if a["hex"] == "4ca7b5")
    assert gnd["alt_baro"] == "ground"
    assert client.get("/v2/mil").json()["ac"][0]["dbFlags"] == 1
    assert client.get("/v2/callsign/BAW117").json()["total"] >= 1


def test_all_providers_down_is_503(client, upstream):
    upstream.fail = {"adsb.lol": 500, "airplanes.live": 500, "adsb.fi": 500}
    assert client.get("/v1/flights/nearby", params={"lat": 10, "lon": 10}).status_code == 503


def test_api_keys_and_rate_limit(ff, settings):
    settings.api_keys = ["k"]
    settings.rate_limit = 2
    with TestClient(create_app(settings, ff=ff)) as c:
        assert c.get("/v1/flights/world").status_code == 401
        assert c.get("/v1/flights/world", headers={"X-API-Key": "k"}).status_code == 200
        assert c.get("/v1/flights/world", params={"api_key": "k"}).status_code == 200
        assert c.get("/v1/flights/world", headers={"Authorization": "Bearer k"}).status_code == 429
        assert c.get("/health").status_code == 200
