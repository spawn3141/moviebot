"""REST API (FastAPI). Interactive docs at /docs."""

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import queries
from .config import Config
from .db import connect
from .scheduler import SnapshotScheduler

# Built web UI (frontend/ → `npm run build`). Without it only the API is served.
WEB_DIR = Path(__file__).with_name("web")

MediaType = Literal["movie", "tv"]
Status = Literal["unseen", "seen", "not_interested"]
Sort = Literal["popularity", "rating", "newest", "added", "title"]


# --- response/request models (they also document the API under /docs) ---------------

class Availability(BaseModel):
    service: str
    name: str
    free: bool
    since: str = Field(description="Tag, an dem wir den Titel im Dienst entdeckt haben")
    baseline: bool = Field(description="true = beim ersten Abgleich gefunden, war also vermutlich "
                                       "schon länger da; zählt nicht als Neuzugang")


class UserState(BaseModel):
    status: Status
    rating: int | None


class TitleSummary(BaseModel):
    id: int
    media_type: MediaType
    title: str
    original_title: str | None
    year: int | None
    genres: list[str]
    overview: str | None
    runtime: int | None
    number_of_seasons: int | None
    vote_average: float | None
    vote_count: int | None
    poster_url: str | None
    available_on: list[Availability]
    user: UserState


class TitlePage(BaseModel):
    total: int
    page: int
    page_size: int
    services: list[str] = Field(description="Dienste, in denen gesucht wurde")
    items: list[TitleSummary]


class Season(BaseModel):
    season_number: int
    name: str | None
    air_date: str | None
    episode_count: int | None


class Offer(BaseModel):
    provider_id: int
    provider: str
    monetization: str = Field(description="flatrate (Abo), free, ads, rent, buy")
    service: str | None = Field(description="zugehöriger Dienst aus der Config, falls verfolgt")


class TitleDetail(TitleSummary):
    tmdb_id: int
    release_date: str | None
    original_language: str | None
    keywords: list[str]
    directors: list[str] = Field(description="Filme: Regie, Serien: Schöpfer")
    cast: list[str]
    tv_status: str | None
    last_air_date: str | None
    next_episode_date: str | None
    seasons: list[Season]
    offers: list[Offer]
    offers_known: bool = Field(description="false = Angebotsdaten noch nicht geladen")
    watch_link: str | None
    tmdb_url: str
    attribution: str


class StateUpdate(BaseModel):
    status: Status | None = None
    rating: int | None = Field(default=None, ge=1, le=5,
                               description="1–5, null löscht die Bewertung. Setzt 'seen', "
                                           "wenn kein Status mitgeschickt wird.")


class Service(BaseModel):
    key: str
    name: str
    free: bool
    subscribed: bool
    movies: int
    series: int


class ServiceUpdate(BaseModel):
    subscribed: bool


class Settings(BaseModel):
    include_free: bool = Field(description="Kostenlose Angebote bei 'meine Dienste' einbeziehen")


class SettingsUpdate(BaseModel):
    include_free: bool | None = None


class Genre(BaseModel):
    name: str
    count: int


class Event(BaseModel):
    event: Literal["added", "readded", "new_season"]
    date: str
    season_number: int | None
    service: str | None
    title: TitleSummary


class FailedRun(BaseModel):
    service: str
    media_type: str
    finished_at: str | None
    message: str | None


class Schedule(BaseModel):
    time: str | None = Field(description="Uhrzeit des täglichen Abgleichs, null = aus")
    running: bool
    next_run: str | None
    last_error: str | None


class Status_(BaseModel):
    first_snapshot: str | None
    has_comparison: bool = Field(description="false = bisher nur der erste Abgleich, "
                                             "Neuzugänge gibt es erst ab dem zweiten")
    schedule: Schedule | None = Field(description="null = Server ohne Zeitplan gestartet")
    last_snapshot: str | None
    failed_since_last_snapshot: list[FailedRun]
    titles: int
    titles_with_details: int
    titles_with_offers: int
    attribution: str


# --- app ---------------------------------------------------------------------------

class SnapshotStarted(BaseModel):
    started: bool


def create_app(cfg: Config, scheduler: SnapshotScheduler | None = None) -> FastAPI:
    # Create/migrate the schema once at startup; requests then use plain connections.
    connect(cfg.db_path).close()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop()

    app = FastAPI(
        title="moviebot",
        version="0.1.0",
        description="Filme und Serien aus deinen Streaming-Abos filtern und bewerten. "
                    + queries.ATTRIBUTION,
        lifespan=lifespan,
    )

    def db() -> Iterator[sqlite3.Connection]:
        # One connection per request, used sequentially – but FastAPI may run this dependency
        # and the endpoint in different worker threads, hence check_same_thread=False.
        conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(db)]

    def bad_request(e: ValueError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(e))

    @app.get("/api/titles", response_model=TitlePage, tags=["Titel"],
             summary="Titel filtern und sortieren")
    def list_titles(
        conn: Conn,
        services: Annotated[list[str] | None, Query(
            description="Dienste (z. B. netflix). Leer = meine Abos (+ kostenlose, falls eingestellt)")] = None,
        include_free: Annotated[bool | None, Query(
            description="Nur ohne 'services': kostenlose einbeziehen (leer = Einstellung)")] = None,
        media_type: MediaType | None = None,
        genre: Annotated[list[str] | None, Query(description="Mindestens eines dieser Genres")] = None,
        year_from: int | None = None,
        year_to: int | None = None,
        q: Annotated[str | None, Query(description="Suche im Titel")] = None,
        min_rating: Annotated[float | None, Query(ge=0, le=10)] = None,
        new_days: Annotated[int | None, Query(
            ge=1, description="Nur neu im Dienst oder neue Staffel in den letzten N Tagen")] = None,
        show_seen: bool = False,
        show_not_interested: bool = False,
        sort: Sort = "popularity",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        f = queries.TitleFilter(
            services=services, include_free=include_free, media_type=media_type,
            genres=genre or [], year_from=year_from, year_to=year_to, q=q,
            min_rating=min_rating, new_days=new_days, show_seen=show_seen,
            show_not_interested=show_not_interested, sort=sort, page=page, page_size=page_size,
        )
        try:
            return queries.search_titles(conn, f)
        except ValueError as e:
            raise bad_request(e)

    @app.get("/api/titles/{title_id}", response_model=TitleDetail, tags=["Titel"],
             summary="Alle Infos zu einem Titel")
    def title_detail(conn: Conn, title_id: int):
        result = queries.get_title(conn, title_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Titel nicht gefunden")
        return result

    @app.put("/api/titles/{title_id}/state", response_model=UserState, tags=["Titel"],
             summary="Gesehen / Bewertung / nicht interessiert setzen")
    def update_state(conn: Conn, title_id: int, body: StateUpdate):
        changes = body.model_dump(include=body.model_fields_set)
        if "status" in changes and changes["status"] is None:
            raise HTTPException(status_code=422, detail="status darf nicht null sein")
        try:
            return queries.set_user_state(conn, title_id, changes)
        except LookupError:
            raise HTTPException(status_code=404, detail="Titel nicht gefunden")

    @app.get("/api/new", response_model=list[Event], tags=["Titel"],
             summary="Neu in meinen Diensten (Neuzugänge und neue Staffeln)")
    def new_titles(
        conn: Conn,
        days: Annotated[int, Query(ge=1, le=365)] = 7,
        services: list[str] | None = Query(default=None),
        include_free: bool | None = None,
    ):
        try:
            return queries.list_events(conn, days, services, include_free)
        except ValueError as e:
            raise bad_request(e)

    @app.get("/api/genres", response_model=list[Genre], tags=["Filter"])
    def genres(conn: Conn, media_type: MediaType | None = None):
        return queries.list_genres(conn, media_type)

    @app.get("/api/services", response_model=list[Service], tags=["Dienste & Einstellungen"])
    def services(conn: Conn):
        return queries.list_services(conn)

    @app.put("/api/services/{key}", response_model=Service, tags=["Dienste & Einstellungen"],
             summary="Abo an- oder abwählen")
    def update_service(conn: Conn, key: str, body: ServiceUpdate):
        try:
            return queries.set_service_subscribed(conn, key, body.subscribed)
        except ValueError as e:
            unknown = "Unbekannter Dienst" in str(e)
            raise HTTPException(status_code=404 if unknown else 400, detail=str(e))

    @app.get("/api/settings", response_model=Settings, tags=["Dienste & Einstellungen"])
    def settings(conn: Conn):
        return queries.get_settings(conn)

    @app.put("/api/settings", response_model=Settings, tags=["Dienste & Einstellungen"])
    def update_settings(conn: Conn, body: SettingsUpdate):
        return queries.update_settings(conn, include_free=body.include_free)

    @app.get("/api/status", response_model=Status_, tags=["System"])
    def status(conn: Conn):
        return {**queries.status(conn), "schedule": scheduler.info() if scheduler else None}

    @app.post("/api/snapshot", response_model=SnapshotStarted, status_code=202, tags=["System"],
              summary="Abgleich mit TMDB jetzt starten (läuft im Hintergrund)")
    def start_snapshot():
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Server läuft ohne Zeitplan (--no-schedule)")
        if not scheduler.trigger():
            raise HTTPException(status_code=409, detail="Es läuft bereits ein Abgleich")
        return {"started": True}

    mount_web_ui(app, WEB_DIR)
    return app


def mount_web_ui(app: FastAPI, web_dir: Path) -> None:
    """Serve the single-page app: static files as they are, every other non-API path gets
    index.html so that client-side routes like /neu survive a reload."""
    index = web_dir / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"message": "Weboberfläche nicht gebaut (frontend: npm run build). "
                                            "API-Doku unter /docs."})
        return

    app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")
    root = web_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def web_ui(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unbekannter API-Pfad")
        file = (web_dir / path).resolve()
        if path and file.is_file() and root in file.parents:
            return FileResponse(file)
        return FileResponse(index)
