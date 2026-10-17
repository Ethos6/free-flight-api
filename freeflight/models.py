"""Normalized data models (one schema regardless of which network reported the aircraft)."""

from __future__ import annotations

from pydantic import BaseModel, Field

FT_PER_M = 3.28084
KT_PER_MS = 1.943844


class Aircraft(BaseModel):
    hex: str = Field(..., description="ICAO 24-bit address, lowercase hex (e.g. 4ca7b5)")
    callsign: str | None = None
    registration: str | None = None
    type: str | None = Field(None, description="ICAO aircraft type designator, e.g. A320, B77W")
    description: str | None = None
    lat: float | None = None
    lon: float | None = None
    altitude_ft: int | None = Field(None, description="Barometric altitude in feet")
    altitude_m: int | None = None
    geo_altitude_ft: int | None = None
    on_ground: bool = False
    ground_speed_kt: float | None = None
    speed_kmh: float | None = None
    track: float | None = Field(None, description="True track over ground, degrees")
    vertical_rate_fpm: int | None = None
    squawk: str | None = None
    emergency: str | None = Field(None, description="general | lifeguard | minfuel | nordo | unlawful | downed, or 7500/7600/7700")
    category: str | None = None
    military: bool = False
    origin_country: str | None = None
    position_source: str | None = Field(None, description="adsb | mlat | tisb | adsc | other")
    seen_pos_s: float | None = Field(None, description="Seconds since the last position update")
    distance_nm: float | None = None
    provider: str = ""
    providers: list[str] = Field(default_factory=list)

    def fill_units(self) -> Aircraft:
        if self.altitude_ft is not None and self.altitude_m is None:
            self.altitude_m = round(self.altitude_ft / FT_PER_M)
        if self.ground_speed_kt is not None and self.speed_kmh is None:
            self.speed_kmh = round(self.ground_speed_kt * 1.852, 1)
        return self


class Airport(BaseModel):
    name: str | None = None
    iata: str | None = None
    icao: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None


class Route(BaseModel):
    callsign: str
    airline: str | None = None
    airline_iata: str | None = None
    airline_icao: str | None = None
    flight_iata: str | None = None
    origin: Airport | None = None
    destination: Airport | None = None
    source: str = "adsbdb"


class AircraftInfo(BaseModel):
    hex: str
    registration: str | None = None
    type: str | None = None
    icao_type: str | None = None
    manufacturer: str | None = None
    owner: str | None = None
    country: str | None = None
    photo: str | None = None
    photo_thumbnail: str | None = None
    photo_credit: str | None = None
    photo_link: str | None = None


class ProviderReport(BaseModel):
    name: str
    ok: bool
    aircraft: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    skipped: bool = False


class FlightsResponse(BaseModel):
    count: int
    aircraft: list[Aircraft]
    providers: list[ProviderReport] = Field(default_factory=list)
    cached: bool = False
    timestamp: float = 0.0
