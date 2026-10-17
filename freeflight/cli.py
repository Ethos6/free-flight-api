"""Command line: `freeflight serve | nearby | track | world | check | mcp`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="freeflight", description="Free live flight tracking API")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the HTTP API + live map")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)

    n = sub.add_parser("nearby", help="aircraft near a point")
    n.add_argument("lat", type=float)
    n.add_argument("lon", type=float)
    n.add_argument("-r", "--radius", type=float, default=30, help="nautical miles (max 250)")
    n.add_argument("-n", "--limit", type=int, default=25)
    n.add_argument("--json", action="store_true")

    t = sub.add_parser("track", help="track one flight by callsign or ICAO hex")
    t.add_argument("ident")

    w = sub.add_parser("world", help="global snapshot summary (OpenSky)")
    w.add_argument("--json", action="store_true")

    sub.add_parser("check", help="query every provider once and print a health table (Markdown)")

    m = sub.add_parser("mcp", help="run as an MCP server")
    m.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8001)

    a = p.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("freeflight.api:app", host=a.host, port=a.port)
        return
    if a.cmd == "mcp":
        from .mcp_server import run

        run(a.transport, a.host, a.port)
        return
    sys.exit(asyncio.run(_run(a)))


async def _run(a: argparse.Namespace) -> int:
    from .core import FreeFlight, NoData

    async with FreeFlight() as ff:
        if a.cmd == "nearby":
            res = await ff.nearby(a.lat, a.lon, a.radius)
            if a.json:
                print(res.model_dump_json(indent=2))
            else:
                for x in res.aircraft[: a.limit]:
                    alt = "GND" if x.on_ground else f"{x.altitude_ft} ft"
                    print(
                        f"{x.callsign or '-':<9} {x.hex}  {x.type or '':<5} {alt:>9}  {x.ground_speed_kt or '':>6} kt  {x.distance_nm:>6} nm"
                    )
                print(
                    f"\n{res.count} aircraft · " + ", ".join(f"{r.name}:{'ok' if r.ok else r.error}" for r in res.providers),
                    file=sys.stderr,
                )
        elif a.cmd == "track":
            print(json.dumps(await ff.track(a.ident), indent=2, ensure_ascii=False))
        elif a.cmd == "world":
            res = await ff.world()
            if a.json:
                print(res.model_dump_json())
            else:
                airborne = sum(1 for x in res.aircraft if not x.on_ground)
                print(f"{res.count:,} aircraft tracked worldwide ({airborne:,} airborne)")
        elif a.cmd == "check":
            return await _check(ff, NoData)
    return 0


async def _check(ff, NoData) -> int:  # noqa: N803
    rows = []
    tests = [
        ("point (London, 100 nm)", "point", {"lat": 51.47, "lon": -0.45, "radius": 100}),
        ("point (New York, 100 nm)", "point", {"lat": 40.64, "lon": -73.78, "radius": 100}),
        ("military", "military", {}),
    ]
    for prov in ff.providers.values():
        for label, kind, params in tests:
            if kind not in prov.kinds:
                continue
            res, rep = await ff._ask(prov, kind, params)
            sample = next((x.callsign for x in res if x.callsign), "")
            rows.append(
                f"| {prov.name} | {label} | {'✅' if rep.ok else '❌'} | {rep.aircraft} | {rep.elapsed_ms} | {sample} | {rep.error or ''} |"
            )
            await asyncio.sleep(1.2)
    print("| provider | query | status | aircraft | ms | sample | error |\n|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    ok_world = False
    try:
        w = await ff.world()
        ok_world = w.count > 0
        print(f"\n**OpenSky world snapshot:** {w.count:,} aircraft ({sum(1 for x in w.aircraft if not x.on_ground):,} airborne)")
    except NoData as exc:
        print(f"\n**OpenSky world snapshot:** unavailable from this IP ({exc})")
    near = await ff.nearby(51.47, -0.45, 60)
    print(f"**Merged nearby (Heathrow, 60 nm):** {near.count} aircraft from {[r.name for r in near.providers if r.ok]}")
    pick = next((x for x in near.aircraft if x.callsign and not x.on_ground), None)
    t = await ff.track(pick.callsign) if pick else None
    if t:
        print(
            f"**Track {t['query']}:** callsign={t['live'] and t['live'].get('callsign')} route={bool(t['route'])} aircraft_info={bool(t['aircraft'])}"
        )
    ok = sum(1 for r in rows if "✅" in r)
    return 0 if (ok > 0 or ok_world) else 1


if __name__ == "__main__":
    main()
