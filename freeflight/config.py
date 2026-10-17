"""Configuration from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    providers: list[str] = field(default_factory=lambda: ["adsb.lol", "airplanes.live", "adsb.fi"])
    opensky_enabled: bool = True
    opensky_client_id: str | None = None
    opensky_client_secret: str | None = None
    # How long live area results are shared between clients (seconds)
    cache_ttl: float = 5.0
    # World snapshot refresh (seconds). Default depends on OpenSky credentials.
    world_ttl: float | None = None
    enrich_ttl: float = 86400.0
    timeout: float = 6.0
    api_keys: list[str] = field(default_factory=list)
    rate_limit: int = 0
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    photos: bool = True

    @property
    def world_refresh(self) -> float:
        if self.world_ttl:
            return self.world_ttl
        # anonymous: 400 credits/day, 4 per global query -> ~1 snapshot / 15 min
        # with free credentials: 4000 credits/day -> ~1 snapshot / 90 s
        return 90.0 if self.opensky_client_id and self.opensky_client_secret else 900.0

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv(os.getenv("FREEFLIGHT_ENV_FILE", ".env"))
        return cls(
            providers=_list("FREEFLIGHT_PROVIDERS", "adsb.lol,airplanes.live,adsb.fi"),
            opensky_enabled=os.getenv("FREEFLIGHT_OPENSKY", "true").lower() not in ("0", "false", "no"),
            opensky_client_id=os.getenv("FREEFLIGHT_OPENSKY_CLIENT_ID") or None,
            opensky_client_secret=os.getenv("FREEFLIGHT_OPENSKY_CLIENT_SECRET") or None,
            cache_ttl=_num("FREEFLIGHT_CACHE_TTL", 5),
            world_ttl=_num("FREEFLIGHT_WORLD_TTL", 0) or None,
            enrich_ttl=_num("FREEFLIGHT_ENRICH_TTL", 86400),
            timeout=_num("FREEFLIGHT_TIMEOUT", 6),
            api_keys=_list("FREEFLIGHT_API_KEYS"),
            rate_limit=int(_num("FREEFLIGHT_RATE_LIMIT", 0)),
            cors_origins=_list("FREEFLIGHT_CORS_ORIGINS", "*"),
            photos=os.getenv("FREEFLIGHT_PHOTOS", "true").lower() not in ("0", "false", "no"),
        )
