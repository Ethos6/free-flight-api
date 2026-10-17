"""Planes over your head, from your own FreeFlight server."""

import requests

BASE = "http://localhost:8000"

data = requests.get(f"{BASE}/v1/flights/nearby", params={"lat": 51.47, "lon": -0.45, "radius_nm": 20}, timeout=20).json()
for a in data["aircraft"][:10]:
    print(f"{a['callsign'] or '-':<8} {a['type'] or '':<5} {a['altitude_ft']:>6} ft  {a['distance_nm']:>5} nm")

flight = requests.get(f"{BASE}/v1/track/BAW117", timeout=20).json()
if flight["route"]:
    print(flight["route"]["origin"]["name"], "->", flight["route"]["destination"]["name"])
