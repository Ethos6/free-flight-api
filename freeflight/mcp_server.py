"""MCP server: live flight tracking tools for Claude Desktop, Cursor, Windsurf and other agents.

freeflight mcp                                  # stdio
freeflight mcp --transport streamable-http      # http://localhost:8001/mcp
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]

from .core import FreeFlight, NoData

_ff: FreeFlight | None = None


def _get() -> FreeFlight:
    global _ff
    if _ff is None:
        _ff = FreeFlight()
    return _ff


def _line(a: Any) -> str:
    alt = "ground" if a.on_ground else f"{a.altitude_ft} ft"
    extra = f" ⚠️ {a.emergency}" if a.emergency else ""
    dist = f" · {a.distance_nm} nm away" if a.distance_nm is not None else ""
    return (
        f"- {a.callsign or '?'} ({a.hex}) {a.type or ''} {a.registration or ''} · {alt} · "
        f"{a.ground_speed_kt or '?'} kt · hdg {a.track or '?'} · {a.lat:.3f},{a.lon:.3f}{dist}{extra}"
    )


def build_server() -> Any:
    server = MCPServer("freeflight", instructions="Live flight tracking. Use flights_near for an area and track_flight for one flight.")

    @server.tool()
    async def flights_near(lat: float, lon: float, radius_nm: float = 30, limit: int = 25) -> str:
        """Aircraft currently flying near a location (closest first).

        Args:
            lat: latitude
            lon: longitude
            radius_nm: search radius in nautical miles (max 250)
            limit: max aircraft to list
        """
        try:
            res = await _get().nearby(lat, lon, radius_nm)
        except NoData as exc:
            return f"No data: {exc}"
        return f"{res.count} aircraft within {radius_nm} nm:\n" + "\n".join(_line(a) for a in res.aircraft[:limit])

    @server.tool()
    async def track_flight(callsign_or_hex: str) -> str:
        """Live position, route (origin/destination) and aircraft details for one flight, e.g. BAW117 or 4ca7b5."""
        t = await _get().track(callsign_or_hex)
        lines = []
        if t["route"]:
            r = t["route"]
            o, d = r.get("origin") or {}, r.get("destination") or {}
            lines.append(
                f"Route: {r.get('airline') or ''} {r['callsign']}: {o.get('name')} ({o.get('iata')}) -> {d.get('name')} ({d.get('iata')})"
            )
        if t["aircraft"]:
            i = t["aircraft"]
            lines.append(
                f"Aircraft: {i.get('manufacturer') or ''} {i.get('type') or ''} {i.get('registration') or ''} owner {i.get('owner') or '?'}"
            )
        if t["live"]:
            from .models import Aircraft

            lines.append("Live: " + _line(Aircraft(**t["live"])))
        else:
            lines.append("Not currently tracked (on the ground, out of coverage, or not flying).")
        return "\n".join(lines)

    @server.tool()
    async def emergencies() -> str:
        """Aircraft currently squawking an emergency code (7500 hijack, 7600 radio failure, 7700 emergency)."""
        res = await _get().emergencies()
        return "\n".join(_line(a) for a in res.aircraft) or "No aircraft squawking emergency codes right now."

    @server.tool()
    async def military_aircraft(limit: int = 30) -> str:
        """Military aircraft currently tracked."""
        res = await _get().military()
        return f"{res.count} military aircraft:\n" + "\n".join(_line(a) for a in res.aircraft[:limit])

    return server


def run(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001) -> None:
    server = build_server()
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)
