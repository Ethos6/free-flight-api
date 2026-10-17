"""Code written for the OpenSky REST API works unchanged against FreeFlight."""

import requests

BASE = "http://localhost:8000/api"  # instead of https://opensky-network.org/api

states = requests.get(f"{BASE}/states/all", params={"lamin": 45.8, "lomin": 5.9, "lamax": 47.8, "lomax": 10.5}).json()
for s in states["states"][:10]:
    icao24, callsign, country, *_rest = s
    print(icao24, (callsign or "").strip(), country, "lat", s[6], "lon", s[5], "alt(m)", s[7])
