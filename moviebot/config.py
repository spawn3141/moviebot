import os
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DEFAULT_CONFIG = Path("config.toml")
MEDIA_TYPES = ("movie", "tv")
FREE_MONETIZATION = ["free", "ads"]


@dataclass
class Service:
    key: str
    name: str
    provider_ids: list[int]
    free: bool = False  # free / with ads instead of a subscription


@dataclass
class Config:
    api_key: str | None = None
    read_access_token: str | None = None
    region: str = "DE"
    language: str = "de-DE"
    min_year: int = 2025
    media_types: list[str] = field(default_factory=lambda: list(MEDIA_TYPES))
    monetization: list[str] = field(default_factory=lambda: ["flatrate"])
    services: list[Service] = field(default_factory=list)
    initial_subscriptions: list[str] = field(default_factory=list)
    series_refresh_days: int = 7   # refresh every running series at least this often
    removal_grace_runs: int = 2
    max_drop_ratio: float = 0.2
    snapshot_time: time | None = None  # daily snapshot in `serve`; None = off
    db_path: Path = Path("data/moviebot.db")

    def monetization_for(self, service: Service) -> list[str]:
        return FREE_MONETIZATION if service.free else self.monetization

    def service(self, key: str) -> Service:
        for s in self.services:
            if s.key == key:
                return s
        raise ValueError(f"Unbekannter Dienst '{key}'. Konfiguriert: "
                         + ", ".join(s.key for s in self.services))


def load_config(path: Path | None = None) -> Config:
    path = Path(path or os.environ.get("MOVIEBOT_CONFIG") or DEFAULT_CONFIG)
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    tmdb = data.get("tmdb", {})
    scope = data.get("scope", {})
    snapshot = data.get("snapshot", {})
    database = data.get("database", {})

    db_path = Path(os.environ.get("MOVIEBOT_DB_PATH") or database.get("path", "data/moviebot.db"))
    if not db_path.is_absolute():
        db_path = path.resolve().parent / db_path

    services = [
        Service(key=s["key"], name=s["name"], provider_ids=[int(p) for p in s["provider_ids"]],
                free=bool(s.get("free", False)))
        for s in data.get("services", [])
    ]
    media_types = list(scope.get("media_types", MEDIA_TYPES))
    for m in media_types:
        if m not in MEDIA_TYPES:
            raise ValueError(f"Unbekannter Medientyp '{m}' in [scope] media_types")

    raw_time = os.environ.get("MOVIEBOT_SNAPSHOT_TIME", data.get("schedule", {}).get("time", "06:00"))
    try:
        snapshot_time = time.fromisoformat(raw_time) if raw_time else None
    except ValueError:
        raise ValueError(f"Ungültige Uhrzeit '{raw_time}' in [schedule] time (Format HH:MM)") from None

    return Config(
        # Environment variables win, so secrets can stay out of the file.
        api_key=os.environ.get("TMDB_API_KEY") or tmdb.get("api_key") or None,
        read_access_token=os.environ.get("TMDB_READ_ACCESS_TOKEN")
        or tmdb.get("read_access_token")
        or None,
        region=tmdb.get("region", "DE"),
        language=tmdb.get("language", "de-DE"),
        min_year=int(scope.get("min_year", 2025)),
        media_types=media_types,
        monetization=list(scope.get("monetization", ["flatrate"])),
        services=services,
        initial_subscriptions=list(data.get("subscriptions", {}).get("initial", [])),
        series_refresh_days=int(snapshot.get("series_refresh_days", 7)),
        removal_grace_runs=int(snapshot.get("removal_grace_runs", 2)),
        max_drop_ratio=float(snapshot.get("max_drop_ratio", 0.2)),
        snapshot_time=snapshot_time,
        db_path=db_path,
    )
