"""Storing title metadata (facts from TMDB only), services and seasons."""

import json
import sqlite3
from datetime import date

from .age_ratings import age_rating
from .config import Config
from .db import get_setting, now_iso, set_setting

CAST_LIMIT = 10


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _year(d: str | None) -> int | None:
    return int(d[:4]) if d and len(d) >= 4 else None


def _basic_fields(media_type: str, item: dict) -> dict:
    """Map the movie/tv specific field names onto our columns."""
    if media_type == "movie":
        title, original, released = item.get("title"), item.get("original_title"), item.get("release_date")
    else:
        title, original, released = item.get("name"), item.get("original_name"), item.get("first_air_date")
    return {
        "title": title or original or "?",
        "original_title": original,
        "original_language": item.get("original_language"),
        "release_date": released or None,
        "year": _year(released),
        "overview": item.get("overview"),
        "vote_average": item.get("vote_average"),
        "vote_count": item.get("vote_count"),
        "popularity": item.get("popularity"),
        "poster_path": item.get("poster_path"),
    }


def upsert_from_discover(conn: sqlite3.Connection, media_type: str, item: dict,
                         genre_names: dict[int, str]) -> int:
    """Insert/refresh the basic fields /discover provides. Returns titles.id."""
    now = now_iso()
    f = _basic_fields(media_type, item)
    genres = _dumps([genre_names[g] for g in item.get("genre_ids", []) if g in genre_names])
    return conn.execute(
        """
        INSERT INTO titles (media_type, tmdb_id, title, original_title, original_language,
                            release_date, year, genres, overview, vote_average, vote_count,
                            popularity, poster_path, created_at, updated_at)
        VALUES (:media_type, :tmdb_id, :title, :original_title, :original_language,
                :release_date, :year, :genres, :overview, :vote_average, :vote_count,
                :popularity, :poster_path, :now, :now)
        ON CONFLICT (media_type, tmdb_id) DO UPDATE SET
            title = excluded.title,
            original_title = excluded.original_title,
            original_language = excluded.original_language,
            release_date = excluded.release_date,
            year = excluded.year,
            -- detail data has the authoritative genre list once present
            genres = CASE WHEN titles.details_fetched_at IS NULL THEN excluded.genres ELSE titles.genres END,
            overview = COALESCE(NULLIF(excluded.overview, ''), titles.overview),
            vote_average = excluded.vote_average,
            vote_count = excluded.vote_count,
            popularity = excluded.popularity,
            poster_path = excluded.poster_path,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        {**f, "media_type": media_type, "tmdb_id": item["id"], "genres": genres, "now": now},
    ).fetchone()[0]


def upsert_details(conn: sqlite3.Connection, media_type: str, details: dict) -> tuple[int, bool]:
    """Store full details. Returns (titles.id, whether details had been fetched before)."""
    now = now_iso()
    before = conn.execute(
        "SELECT details_fetched_at FROM titles WHERE media_type = ? AND tmdb_id = ?",
        (media_type, details["id"]),
    ).fetchone()
    had_details = bool(before and before[0])

    credits = details.get("credits", {})
    cast = [c["name"] for c in sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))]
    kw = details.get("keywords", {})
    keywords = [k["name"] for k in kw.get("keywords", kw.get("results", []))]
    if media_type == "movie":
        directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
        runtime = details.get("runtime")
        tv = {"last_air_date": None, "tv_status": None, "number_of_seasons": None,
              "next_episode_date": None}
    else:
        directors = [c["name"] for c in details.get("created_by", [])]
        runtimes = details.get("episode_run_time") or []
        runtime = runtimes[0] if runtimes else (details.get("last_episode_to_air") or {}).get("runtime")
        tv = {
            "last_air_date": details.get("last_air_date"),
            "tv_status": details.get("status"),
            "number_of_seasons": details.get("number_of_seasons"),
            "next_episode_date": (details.get("next_episode_to_air") or {}).get("air_date"),
        }

    params = {
        **_basic_fields(media_type, details), **tv,
        "media_type": media_type, "tmdb_id": details["id"],
        "genres": _dumps([g["name"] for g in details.get("genres", [])]),
        "keywords": _dumps(keywords), "directors": _dumps(directors),
        "cast_members": _dumps(cast[:CAST_LIMIT]), "runtime": runtime,
        # movies carry imdb_id directly, series only in external_ids
        "imdb_id": details.get("imdb_id") or details.get("external_ids", {}).get("imdb_id"),
        "now": now,
    }
    title_id = conn.execute(
        """
        INSERT INTO titles (media_type, tmdb_id, title, original_title, original_language,
                            release_date, year, last_air_date, tv_status, number_of_seasons,
                            next_episode_date, genres, keywords, directors, cast_members, overview,
                            runtime, vote_average, vote_count, popularity, poster_path, imdb_id,
                            details_fetched_at, created_at, updated_at)
        VALUES (:media_type, :tmdb_id, :title, :original_title, :original_language,
                :release_date, :year, :last_air_date, :tv_status, :number_of_seasons,
                :next_episode_date, :genres, :keywords, :directors, :cast_members, :overview,
                :runtime, :vote_average, :vote_count, :popularity, :poster_path, :imdb_id,
                :now, :now, :now)
        ON CONFLICT (media_type, tmdb_id) DO UPDATE SET
            title = excluded.title,
            original_title = excluded.original_title,
            original_language = excluded.original_language,
            release_date = excluded.release_date,
            year = excluded.year,
            last_air_date = excluded.last_air_date,
            tv_status = excluded.tv_status,
            number_of_seasons = excluded.number_of_seasons,
            next_episode_date = excluded.next_episode_date,
            genres = excluded.genres,
            keywords = excluded.keywords,
            directors = excluded.directors,
            cast_members = excluded.cast_members,
            overview = COALESCE(NULLIF(excluded.overview, ''), titles.overview),
            runtime = excluded.runtime,
            vote_average = excluded.vote_average,
            vote_count = excluded.vote_count,
            popularity = excluded.popularity,
            poster_path = excluded.poster_path,
            imdb_id = excluded.imdb_id,
            details_fetched_at = excluded.details_fetched_at,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        params,
    ).fetchone()[0]
    return title_id, had_details


def update_seasons(conn: sqlite3.Connection, title_id: int, seasons: list[dict], today: date,
                   emit_events: bool) -> list[int]:
    """Store the season list; return season numbers that were newly released today.

    A season counts as released once its air_date has passed. Events are only emitted for
    series we already knew (`emit_events`), otherwise every existing season of a series that
    just entered the database would show up as "new".
    """
    day = today.isoformat()
    known = {
        r["season_number"]: r["released_at"]
        for r in conn.execute("SELECT season_number, released_at FROM seasons WHERE title_id = ?",
                              (title_id,))
    }
    new_releases = []
    for s in seasons:
        number = s.get("season_number")
        if not number:  # 0 = specials
            continue
        air_date = s.get("air_date") or None
        released = air_date is not None and air_date <= day
        already_released = known.get(number) is not None
        released_at = known.get(number) or (day if released else None)
        conn.execute(
            """
            INSERT INTO seasons (title_id, season_number, name, air_date, episode_count, released_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (title_id, season_number) DO UPDATE SET
                name = excluded.name, air_date = excluded.air_date,
                episode_count = excluded.episode_count, released_at = excluded.released_at
            """,
            (title_id, number, s.get("name"), air_date, s.get("episode_count"), released_at),
        )
        if released and not already_released and emit_events:
            conn.execute(
                """INSERT INTO events (title_id, event, season_number, event_date)
                   VALUES (?, 'new_season', ?, ?)""",
                (title_id, number, day),
            )
            new_releases.append(number)
    return new_releases


def upsert_providers(conn: sqlite3.Connection, providers: list[dict]) -> None:
    now = now_iso()
    conn.executemany(
        """
        INSERT INTO providers (provider_id, name, logo_path, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (provider_id) DO UPDATE SET
            name = excluded.name, logo_path = excluded.logo_path, updated_at = excluded.updated_at
        """,
        [(p["provider_id"], p["provider_name"], p.get("logo_path"), now) for p in providers],
    )


def sync_services(conn: sqlite3.Connection, cfg: Config) -> None:
    """Mirror the services from config.toml into the DB. Subscriptions are only seeded once;
    afterwards they belong to the user (CLI now, web UI later)."""
    now = now_iso()
    keys = [s.key for s in cfg.services]
    for s in cfg.services:
        conn.execute(
            """
            INSERT INTO services (key, name, provider_ids, free, tracked, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT (key) DO UPDATE SET
                name = excluded.name, provider_ids = excluded.provider_ids, free = excluded.free,
                tracked = 1, updated_at = excluded.updated_at
            """,
            (s.key, s.name, _dumps(s.provider_ids), int(s.free), now),
        )
    conn.execute(
        f"UPDATE services SET tracked = 0 WHERE key NOT IN ({','.join('?' * len(keys))})", keys
    )
    if get_setting(conn, "subscriptions_seeded") is None:
        for key in cfg.initial_subscriptions:
            cfg.service(key)  # validates the key
            conn.execute("UPDATE services SET subscribed = 1 WHERE key = ?", (key,))
        set_setting(conn, "subscriptions_seeded", "1")


def set_subscribed(conn: sqlite3.Connection, key: str, subscribed: bool) -> None:
    row = conn.execute("SELECT free FROM services WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ValueError(f"Unbekannter Dienst '{key}'")
    if row["free"]:
        raise ValueError(f"'{key}' ist kostenlos und braucht kein Abo "
                         "(Einstellung 'kostenlose Angebote einbeziehen')")
    conn.execute("UPDATE services SET subscribed = ? WHERE key = ?", (int(subscribed), key))


def store_offers(conn: sqlite3.Connection, title_id: int, details: dict, region: str) -> None:
    """Replace the title's offers with the watch/providers data from a detail response."""
    now = now_iso()
    region_data = details.get("watch/providers", {}).get("results", {}).get(region, {})
    offers = {
        (p["provider_id"], monetization)
        for monetization, providers in region_data.items()
        if isinstance(providers, list)
        for p in providers
    }
    conn.execute("DELETE FROM offers WHERE title_id = ?", (title_id,))
    conn.executemany(
        "INSERT INTO offers (title_id, provider_id, monetization, updated_at) VALUES (?, ?, ?, ?)",
        [(title_id, provider_id, monetization, now) for provider_id, monetization in sorted(offers)],
    )
    conn.execute("UPDATE titles SET watch_link = ?, offers_fetched_at = ? WHERE id = ?",
                 (region_data.get("link"), now, title_id))


def store_age_rating(conn: sqlite3.Connection, title_id: int, media_type: str, details: dict) -> None:
    rating = age_rating(details, media_type)
    age, source, raw = rating if rating else (None, None, None)
    conn.execute(
        """UPDATE titles SET age_rating = ?, age_rating_source = ?, age_rating_raw = ?,
                  ratings_fetched_at = ? WHERE id = ?""",
        (age, source, raw, now_iso(), title_id),
    )


DEFAULT_LIST_NAME = "Merkliste"


def ensure_default_list(conn: sqlite3.Connection) -> None:
    """Create the default watchlist on first start."""
    if conn.execute("SELECT 1 FROM lists LIMIT 1").fetchone():
        return
    now = now_iso()
    conn.execute(
        "INSERT INTO lists (name, is_default, created_at, updated_at) VALUES (?, 1, ?, ?)",
        (DEFAULT_LIST_NAME, now, now),
    )


DEFAULT_FILTER = ("Neu", {"new_days": 14, "services": ["mine"], "sort": "added"})


def ensure_default_filter(conn: sqlite3.Connection) -> None:
    """Offer a ready-made "what's new" filter once; the user may rename or delete it."""
    if get_setting(conn, "default_filter_created"):
        return
    name, filters = DEFAULT_FILTER
    now = now_iso()
    conn.execute(
        "INSERT INTO saved_filters (name, filters, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, _dumps(filters), now, now),
    )
    set_setting(conn, "default_filter_created", "1")


def bootstrap(conn: sqlite3.Connection, cfg: Config) -> None:
    """Bring the database in line with the config before serving or scanning."""
    with conn:
        sync_services(conn, cfg)
        ensure_default_list(conn)
        ensure_default_filter(conn)
