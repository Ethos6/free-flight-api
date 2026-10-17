"""HTTP API: live flights, lookups, routes, world snapshot, SSE stream, OpenSky- and ADSBx-v2-compatible endpoints."""

import asyncio
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from . import __version__
from .config import Settings
from .core import FreeFlight, NoData
from .models import FT_PER_M, KT_PER_MS, Aircraft, FlightsResponse

STATIC = Path(__file__).with_name("static")


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, list] = defaultdict(lambda: [0.0, 0])

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        w = self.hits[key]
        if now - w[0] >= 60:
            w[0], w[1] = now, 0
        w[1] += 1
        if len(self.hits) > 50000:
            self.hits.clear()
        return w[1] <= self.per_minute


def apply_filters(
    res: FlightsResponse,
    *,
    min_alt: int | None = None,
    max_alt: int | None = None,
    on_ground: bool | None = None,
    military: bool | None = None,
    type: str | None = None,  # noqa: A002
    limit: int | None = None,
) -> FlightsResponse:
    ac = res.aircraft
    if min_alt is not None:
        ac = [a for a in ac if (a.altitude_ft or 0) >= min_alt]
    if max_alt is not None:
        ac = [a for a in ac if (a.altitude_ft or 0) <= max_alt]
    if on_ground is not None:
        ac = [a for a in ac if a.on_ground == on_ground]
    if military is not None:
        ac = [a for a in ac if a.military == military]
    if type:
        types = {t.strip().upper() for t in type.split(",")}
        ac = [a for a in ac if (a.type or "").upper() in types]
    total = len(ac)
    if limit:
        ac = ac[:limit]
    return res.model_copy(update={"aircraft": ac, "count": total})


def to_v2(a: Aircraft) -> dict[str, Any]:
    """ADSBExchange v2 / readsb record."""
    d: dict[str, Any] = {
        "hex": a.hex,
        "type": {"adsb": "adsb_icao", "mlat": "mlat", "tisb": "tisb_icao", "adsc": "adsc"}.get(a.position_source or "", "other"),
        "flight": (a.callsign or "").ljust(8) if a.callsign else None,
        "r": a.registration,
        "t": a.type,
        "desc": a.description,
        "alt_baro": "ground" if a.on_ground else a.altitude_ft,
        "alt_geom": a.geo_altitude_ft,
        "gs": a.ground_speed_kt,
        "track": a.track,
        "baro_rate": a.vertical_rate_fpm,
        "squawk": a.squawk,
        "emergency": a.emergency or "none",
        "category": a.category,
        "lat": a.lat,
        "lon": a.lon,
        "seen_pos": a.seen_pos_s,
        "dbFlags": 1 if a.military else 0,
        "dst": a.distance_nm,
    }
    return {k: v for k, v in d.items() if v is not None}


def to_opensky_state(a: Aircraft, now: float) -> list[Any]:
    """OpenSky state vector (17 fields + category)."""
    t = int(now - (a.seen_pos_s or 0))
    src = {"adsb": 0, "mlat": 2, "flarm": 3}.get(a.position_source or "", 1)
    return [
        a.hex,
        (a.callsign or "").ljust(8) if a.callsign else None,
        a.origin_country or "",
        t,
        t,
        a.lon,
        a.lat,
        round(a.altitude_ft / FT_PER_M, 2) if a.altitude_ft is not None else None,
        a.on_ground,
        round(a.ground_speed_kt / KT_PER_MS, 2) if a.ground_speed_kt is not None else None,
        a.track,
        round(a.vertical_rate_fpm / FT_PER_M / 60, 2) if a.vertical_rate_fpm is not None else None,
        None,
        round(a.geo_altitude_ft / FT_PER_M, 2) if a.geo_altitude_ft is not None else None,
        a.squawk,
        False,
        src,
        0,
    ]


def create_app(settings: Settings | None = None, ff: FreeFlight | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    limiter = RateLimiter(settings.rate_limit)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ff = ff or FreeFlight(settings)
        yield
        await app.state.ff.aclose()

    app = FastAPI(
        title="FreeFlight API",
        version=__version__,
        description=(
            "Free, self-hosted live flight tracking API. Merges the adsb.lol, airplanes.live and adsb.fi community "
            "ADS-B networks plus the OpenSky Network global snapshot. Includes routes, aircraft details, photos, an SSE "
            "stream and OpenSky / ADSBExchange-v2 compatible endpoints."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    async def auth(request: Request) -> None:
        h = request.headers
        token = None
        authz = h.get("authorization", "")
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
        token = token or h.get("x-api-key") or request.query_params.get("api_key")
        if settings.api_keys and token not in settings.api_keys:
            raise HTTPException(401, "invalid or missing API key")
        if not limiter.allow(token or (request.client.host if request.client else "anon")):
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})

    def get_ff(request: Request) -> FreeFlight:
        return request.app.state.ff

    Auth = Depends(auth)
    FF = Annotated[FreeFlight, Depends(get_ff)]
    Lat = Annotated[float, Query(ge=-90, le=90)]
    Lon = Annotated[float, Query(ge=-180, le=180)]

    class Filters:
        def __init__(
            self,
            min_alt: Annotated[int | None, Query(description="Minimum altitude, feet")] = None,
            max_alt: Annotated[int | None, Query(description="Maximum altitude, feet")] = None,
            on_ground: bool | None = None,
            military: bool | None = None,
            type: Annotated[str | None, Query(description="ICAO type codes, comma-separated (A320,B738)")] = None,  # noqa: A002
            limit: Annotated[int | None, Query(ge=1, le=20000)] = None,
        ) -> None:
            self.kw = {"min_alt": min_alt, "max_alt": max_alt, "on_ground": on_ground, "military": military, "type": type, "limit": limit}

    F = Annotated[Filters, Depends()]

    async def guard(coro):
        try:
            return await coro
        except NoData as exc:
            raise HTTPException(503, str(exc)) from exc

    # ------------------------------------------------------------- meta
    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/providers", tags=["meta"], dependencies=[Auth])
    async def providers(ff_: FF) -> dict[str, Any]:
        return ff_.status()

    # ------------------------------------------------------------- live flights
    @app.get("/v1/flights/nearby", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def nearby(ff_: FF, f: F, lat: Lat, lon: Lon, radius_nm: Annotated[float, Query(gt=0, le=250)] = 50) -> FlightsResponse:
        return apply_filters(await guard(ff_.nearby(lat, lon, radius_nm)), **f.kw)

    @app.get("/v1/flights/bbox", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def bbox(ff_: FF, f: F, lamin: Lat, lomin: Lon, lamax: Lat, lomax: Lon) -> FlightsResponse:
        return apply_filters(await guard(ff_.bbox(lamin, lomin, lamax, lomax)), **f.kw)

    @app.get("/v1/flights/world", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def world(ff_: FF, f: F) -> FlightsResponse:
        return apply_filters(await guard(ff_.world()), **f.kw)

    @app.get("/v1/flights/military", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def military(ff_: FF, f: F) -> FlightsResponse:
        return apply_filters(await guard(ff_.military()), **f.kw)

    @app.get("/v1/flights/emergencies", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def emergencies(ff_: FF) -> FlightsResponse:
        return await guard(ff_.emergencies())

    @app.get("/v1/flights/squawk/{code}", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def squawk(ff_: FF, code: str) -> FlightsResponse:
        if not (len(code) == 4 and code.isdigit()):
            raise HTTPException(400, "squawk must be 4 digits")
        return await guard(ff_.squawk(code))

    @app.get("/v1/flights/type/{icao_type}", response_model=FlightsResponse, tags=["flights"], dependencies=[Auth])
    async def by_type(ff_: FF, f: F, icao_type: str) -> FlightsResponse:
        return apply_filters(await guard(ff_.lookup("type", icao_type)), **f.kw)

    # ------------------------------------------------------------- one aircraft / flight
    @app.get("/v1/aircraft/{hex}", response_model=FlightsResponse, tags=["lookup"], dependencies=[Auth])
    async def aircraft(ff_: FF, hex: str) -> FlightsResponse:  # noqa: A002
        return await guard(ff_.lookup("hex", hex))

    @app.get("/v1/callsign/{callsign}", response_model=FlightsResponse, tags=["lookup"], dependencies=[Auth])
    async def callsign(ff_: FF, callsign: str) -> FlightsResponse:
        return await guard(ff_.lookup("callsign", callsign))

    @app.get("/v1/registration/{reg}", response_model=FlightsResponse, tags=["lookup"], dependencies=[Auth])
    async def registration(ff_: FF, reg: str) -> FlightsResponse:
        return await guard(ff_.lookup("registration", reg))

    @app.get("/v1/track/{ident}", tags=["lookup"], dependencies=[Auth])
    async def track(ff_: FF, ident: str) -> dict[str, Any]:
        """Callsign (BAW117) or ICAO hex (4ca7b5): live position + route + aircraft details + photo."""
        return await ff_.track(ident)

    @app.get("/v1/route/{callsign}", tags=["lookup"], dependencies=[Auth])
    async def route(ff_: FF, callsign: str) -> dict[str, Any]:
        r = await ff_.enricher.route(ff_.client, callsign)
        if not r:
            raise HTTPException(404, "route not found")
        return r.model_dump()

    @app.get("/v1/aircraft/{hex}/info", tags=["lookup"], dependencies=[Auth])
    async def aircraft_info(ff_: FF, hex: str) -> dict[str, Any]:  # noqa: A002
        info = await ff_.enricher.aircraft(ff_.client, hex)
        if not info:
            raise HTTPException(404, "aircraft not found")
        return info.model_dump()

    # ------------------------------------------------------------- streaming
    @app.get("/v1/stream/nearby", tags=["stream"], dependencies=[Auth])
    async def stream(
        request: Request,
        ff_: FF,
        lat: Lat,
        lon: Lon,
        radius_nm: Annotated[float, Query(gt=0, le=250)] = 50,
        interval: Annotated[float, Query(ge=3, le=60)] = 5,
    ) -> StreamingResponse:
        """Server-Sent Events: a fresh snapshot of the area every `interval` seconds."""

        async def events():
            while not await request.is_disconnected():
                try:
                    res = await ff_.nearby(lat, lon, radius_nm)
                    yield f"data: {res.model_dump_json()}\n\n"
                except NoData as exc:
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                await asyncio.sleep(interval)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------- OpenSky-compatible
    @app.get("/api/states/all", tags=["compat: OpenSky"], dependencies=[Auth])
    async def opensky_states(
        ff_: FF,
        lamin: float | None = None,
        lomin: float | None = None,
        lamax: float | None = None,
        lomax: float | None = None,
        icao24: str | None = None,
    ) -> dict[str, Any]:
        if icao24:
            res = await guard(ff_.lookup("hex", icao24))
        elif None not in (lamin, lomin, lamax, lomax):
            res = await guard(ff_.bbox(lamin, lomin, lamax, lomax))  # type: ignore[arg-type]
        else:
            res = await guard(ff_.world())
        now = time.time()
        return {"time": int(now), "states": [to_opensky_state(a, now) for a in res.aircraft]}

    # ------------------------------------------------------------- ADSBExchange v2 / adsb.lol-compatible
    def v2(res: FlightsResponse) -> dict[str, Any]:
        now = int(time.time() * 1000)
        return {"ac": [to_v2(a) for a in res.aircraft], "msg": "No error", "now": now, "total": res.count, "ctime": now, "ptime": 0}

    @app.get("/v2/point/{lat}/{lon}/{radius}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    async def v2_point(ff_: FF, lat: float, lon: float, radius: float) -> dict[str, Any]:
        return v2(await guard(ff_.nearby(lat, lon, min(radius, 250))))

    @app.get("/v2/hex/{value}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    @app.get("/v2/icao/{value}", include_in_schema=False, dependencies=[Auth])
    async def v2_hex(ff_: FF, value: str) -> dict[str, Any]:
        return v2(await guard(ff_.lookup("hex", value)))

    @app.get("/v2/callsign/{value}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    async def v2_callsign(ff_: FF, value: str) -> dict[str, Any]:
        return v2(await guard(ff_.lookup("callsign", value)))

    @app.get("/v2/reg/{value}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    @app.get("/v2/registration/{value}", include_in_schema=False, dependencies=[Auth])
    async def v2_reg(ff_: FF, value: str) -> dict[str, Any]:
        return v2(await guard(ff_.lookup("registration", value)))

    @app.get("/v2/type/{value}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    async def v2_type(ff_: FF, value: str) -> dict[str, Any]:
        return v2(await guard(ff_.lookup("type", value)))

    @app.get("/v2/mil", tags=["compat: ADSBx v2"], dependencies=[Auth])
    async def v2_mil(ff_: FF) -> dict[str, Any]:
        return v2(await guard(ff_.military()))

    @app.get("/v2/sqk/{code}", tags=["compat: ADSBx v2"], dependencies=[Auth])
    @app.get("/v2/squawk/{code}", include_in_schema=False, dependencies=[Auth])
    async def v2_sqk(ff_: FF, code: str) -> dict[str, Any]:
        return v2(await guard(ff_.squawk(code)))

    return app


app = create_app()
