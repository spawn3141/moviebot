"""REST API (FastAPI). Interactive docs at /docs."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import queries
from .config import Config
from .db import connect

MediaType = Literal["movie", "tv"]
Status = Literal["unseen", "seen", "not_interested"]
Sort = Literal["popularity", "rating", "newest", "added", "title"]


# --- response/request models (they also document the API under /docs) ---------------

class Availability(BaseModel):
    service: str
    name: str
    free: bool
    since: str | None = Field(description="Tag, an dem der Titel im Dienst auftauchte "
                                          "(null = war schon beim ersten Abgleich da)")


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


class Status_(BaseModel):
    last_snapshot: str | None
    failed_since_last_snapshot: list[FailedRun]
    titles: int
    titles_with_details: int
    titles_with_offers: int
    attribution: str


# --- app ---------------------------------------------------------------------------

def create_app(cfg: Config) -> FastAPI:
    # Create/migrate the schema once at startup; requests then use plain connections.
    connect(cfg.db_path).close()

    app = FastAPI(
        title="moviebot",
        version="0.1.0",
        description="Filme und Serien aus deinen Streaming-Abos filtern und bewerten. "
                    + queries.ATTRIBUTION,
    )

    def db() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(cfg.db_path)
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
        return queries.status(conn)

    return app
