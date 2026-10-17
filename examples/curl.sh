#!/usr/bin/env sh
curl "http://localhost:8000/v1/flights/nearby?lat=51.47&lon=-0.45&radius_nm=30"
curl "http://localhost:8000/v1/flights/bbox?lamin=45.8&lomin=5.9&lamax=47.8&lomax=10.5&on_ground=false"
curl "http://localhost:8000/v1/flights/world?min_alt=30000&limit=100"
curl "http://localhost:8000/v1/track/BAW117"
curl "http://localhost:8000/v1/flights/emergencies"
curl "http://localhost:8000/v1/flights/military"
curl "http://localhost:8000/v1/flights/type/A388"
curl -N "http://localhost:8000/v1/stream/nearby?lat=40.64&lon=-73.78&radius_nm=40"

# Drop-in compatible
curl "http://localhost:8000/api/states/all?lamin=45.8&lomin=5.9&lamax=47.8&lomax=10.5"   # OpenSky
curl "http://localhost:8000/v2/point/51.47/-0.45/30"                                       # ADSBExchange v2 / adsb.lol
