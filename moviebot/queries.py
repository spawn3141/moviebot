"""Read/write queries behind the REST API. Plain dicts in, plain dicts out."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import catalog, genres
from .db import get_setting, now_iso, set_setting

POSTER_BASE = "https://image.tmdb.org/t/p/w342"
ATTRIBUTION = "Film- und Seriendaten: TMDB. Streaming-Verfügbarkeit: JustWatch."

# Weighted rating: pulls titles with few votes towards the average so that a 9.0 from
# 3 votes does not beat a 7.8 from 5000 votes.
RATING_PRIOR_VOTES = 50
RATING_PRIOR_MEAN = 6.5
WEIGHTED_RATING_SQL = (
    f"((COALESCE(t.vote_count, 0) * COALESCE(t.vote_average, 0) + "
    f"{RATING_PRIOR_VOTES} * {RATING_PRIOR_MEAN}) / (COALESCE(t.vote_count, 0) + {RATING_PRIOR_VOTES}))"
)

# "Neueste zuerst": for series either the start of the newest released season (default) or,
# like movies, the first release.
NEWEST_SEASON_SQL = """COALESCE((SELECT MAX(s.air_date) FROM seasons s
    WHERE s.title_id = t.id AND s.season_number > 0
      AND s.air_date <= date('now', 'localtime')), t.release_date)"""

SORTS = {
    "popularity": "t.popularity DESC",
    "rating": f"{WEIGHTED_RATING_SQL} DESC",
    "newest": "t.release_date DESC",
    "added": "added DESC, t.popularity DESC",
    "title": "t.title COLLATE NOCASE ASC",
}

SERIES_NEWEST = ("season", "first")  # setting values for the "newest" sort
MAX_FILTER_JSON = 4000  # stored filter state is small; guard against junk


def sort_sql(sort: str, series_newest: str) -> str:
    if sort == "newest" and series_newest == "season":
        return f"{NEWEST_SEASON_SQL} DESC"
    return SORTS[sort]


@dataclass
class TitleFilter:
    services: list[str] | None = None      # None = "meine" (Abos + ggf. kostenlose)
    include_free: bool | None = None       # None = Einstellung verwenden
    media_type: str | None = None
    genres: list[str] = field(default_factory=list)  # any of these (unified names)
    year_from: int | None = None
    year_to: int | None = None
    q: str | None = None
    min_rating: float | None = None
    max_age: int | None = None             # age rating <= this
    include_unrated: bool = False          # with max_age: also titles without any age rating
    new_days: int | None = None            # only titles new in the selected services / new season
    show_seen: bool = False
    show_not_interested: bool = False
    sort: str = "popularity"
    page: int = 1
    page_size: int = 50


# --- settings & services ---------------------------------------------------------

def get_settings(conn: sqlite3.Connection) -> dict:
    return {
        "include_free": get_setting(conn, "include_free") == "1",
        "series_newest": get_setting(conn, "series_newest") or "season",
        # last used filters/sorting of the Discover page, so navigating away does not lose them
        "discover_filters": json.loads(get_setting(conn, "discover_filters") or "null"),
    }


def update_settings(conn: sqlite3.Connection, include_free: bool | None = None,
                    series_newest: str | None = None,
                    discover_filters: dict | None = None) -> dict:
    if series_newest is not None and series_newest not in SERIES_NEWEST:
        raise ValueError(f"series_newest muss {' oder '.join(SERIES_NEWEST)} sein")
    filters_json = None
    if discover_filters is not None:
        filters_json = json.dumps(discover_filters, ensure_ascii=False)
        if len(filters_json) > MAX_FILTER_JSON:
            raise ValueError("discover_filters ist zu groß")
    with conn:
        if include_free is not None:
            set_setting(conn, "include_free", "1" if include_free else "0")
        if series_newest is not None:
            set_setting(conn, "series_newest", series_newest)
        if filters_json is not None:
            set_setting(conn, "discover_filters", filters_json)
    return get_settings(conn)


def list_services(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.key, s.name, s.free, s.subscribed,
               COUNT(CASE WHEN t.media_type = 'movie' THEN 1 END) AS movies,
               COUNT(CASE WHEN t.media_type = 'tv' THEN 1 END) AS series
        FROM services s
        LEFT JOIN availability a ON a.service = s.key AND a.removed_at IS NULL AND a.verified = 1
        LEFT JOIN titles t ON t.id = a.title_id
        WHERE s.tracked = 1
        GROUP BY s.key ORDER BY s.free, s.name
        """
    ).fetchall()
    return [
        {"key": r["key"], "name": r["name"], "free": bool(r["free"]),
         "subscribed": bool(r["subscribed"]), "movies": r["movies"], "series": r["series"]}
        for r in rows
    ]


def set_service_subscribed(conn: sqlite3.Connection, key: str, subscribed: bool) -> dict:
    with conn:
        catalog.set_subscribed(conn, key, subscribed)
    return next(s for s in list_services(conn) if s["key"] == key)


def selected_services(conn: sqlite3.Connection, services: list[str] | None,
                      include_free: bool | None) -> list[str]:
    if services:
        known = {r[0] for r in conn.execute("SELECT key FROM services WHERE tracked = 1")}
        unknown = [s for s in services if s not in known]
        if unknown:
            raise ValueError(f"Unbekannte Dienste: {', '.join(unknown)}")
        return services
    if include_free is None:
        include_free = get_settings(conn)["include_free"]
    return [
        r[0] for r in conn.execute(
            "SELECT key FROM services WHERE tracked = 1 AND (subscribed = 1 OR (free = 1 AND ?))",
            (int(include_free),),
        )
    ]


# --- titles ------------------------------------------------------------------------

def _placeholders(values) -> str:
    return ",".join("?" * len(values))


def _summary(row: sqlite3.Row, available: list[dict]) -> dict:
    return {
        "id": row["id"],
        "media_type": row["media_type"],
        "title": row["title"],
        "original_title": row["original_title"],
        "year": row["year"],
        "genres": genres.unify(json.loads(row["genres"])),
        "overview": row["overview"],
        "runtime": row["runtime"],
        "number_of_seasons": row["number_of_seasons"],
        "vote_average": row["vote_average"],
        "vote_count": row["vote_count"],
        "poster_url": f"{POSTER_BASE}{row['poster_path']}" if row["poster_path"] else None,
        "age_rating": row["age_rating"],
        "age_rating_source": row["age_rating_source"],
        "age_rating_raw": row["age_rating_raw"],
        "available_on": available,
        "user": {"status": row["status"] or "unseen", "rating": row["rating"]},
    }


def _availability_for(conn: sqlite3.Connection, title_ids: list[int],
                      services: list[str] | None = None) -> dict[int, list[dict]]:
    if not title_ids:
        return {}
    sql = f"""
        SELECT a.title_id, s.key, s.name, s.free, a.first_seen, a.in_baseline
        FROM availability a JOIN services s ON s.key = a.service
        WHERE a.title_id IN ({_placeholders(title_ids)})
          AND a.removed_at IS NULL AND a.verified = 1 AND s.tracked = 1
    """
    params: list = list(title_ids)
    if services is not None:
        sql += f" AND s.key IN ({_placeholders(services)})"
        params += services
    result: dict[int, list[dict]] = {}
    for r in conn.execute(sql + " ORDER BY s.free, s.name", params):
        result.setdefault(r["title_id"], []).append({
            "service": r["key"], "name": r["name"], "free": bool(r["free"]),
            "since": r["first_seen"],
            # picked up by the first scan: the title was there already, `since` is just that day
            "baseline": bool(r["in_baseline"]),
        })
    return result


def search_titles(conn: sqlite3.Connection, f: TitleFilter, today: date | None = None) -> dict:
    today = today or date.today()
    services = selected_services(conn, f.services, f.include_free)
    if f.sort not in SORTS:
        raise ValueError(f"Unbekannte Sortierung '{f.sort}'. Möglich: {', '.join(SORTS)}")
    order_by = sort_sql(f.sort, get_settings(conn)["series_newest"])
    if not services:
        return {"total": 0, "page": f.page, "page_size": f.page_size, "services": [], "items": []}

    where = ["1 = 1"]
    params: list = []
    avail_params: list = list(services)
    if f.media_type:
        where.append("t.media_type = ?")
        params.append(f.media_type)
    if f.genres:
        raw = sorted({r for g in f.genres for r in genres.raw_names_for(g)})
        where.append(f"EXISTS (SELECT 1 FROM json_each(t.genres) WHERE value IN ({_placeholders(raw)}))")
        params += raw
    if f.year_from is not None:
        where.append("t.year >= ?")
        params.append(f.year_from)
    if f.year_to is not None:
        where.append("t.year <= ?")
        params.append(f.year_to)
    if f.q:
        where.append("(t.title LIKE ? OR t.original_title LIKE ?)")
        params += [f"%{f.q}%", f"%{f.q}%"]
    if f.min_rating is not None:
        where.append("t.vote_average >= ?")
        params.append(f.min_rating)
    if f.max_age is not None:
        where.append("(t.age_rating <= ?" + (" OR t.age_rating IS NULL)" if f.include_unrated else ")"))
        params.append(f.max_age)
    hidden = [s for s, show in (("seen", f.show_seen), ("not_interested", f.show_not_interested))
              if not show]
    if hidden:
        where.append(f"COALESCE(us.status, 'unseen') NOT IN ({_placeholders(hidden)})")
        params += hidden
    if f.new_days is not None:
        since = (today - timedelta(days=f.new_days)).isoformat()
        where.append(f"""(avail.added >= ? OR EXISTS (
            SELECT 1 FROM events e WHERE e.title_id = t.id AND e.event = 'new_season'
              AND e.event_date >= ?))""")
        params += [since, since]

    # "added" = newest first_seen among the selected services, ignoring baseline entries
    base = f"""
        FROM titles t
        JOIN (SELECT a.title_id,
                     MAX(CASE WHEN a.in_baseline = 0 THEN a.first_seen END) AS added
              FROM availability a
              WHERE a.removed_at IS NULL AND a.verified = 1
                AND a.service IN ({_placeholders(services)})
              GROUP BY a.title_id) avail ON avail.title_id = t.id
        LEFT JOIN user_state us ON us.title_id = t.id
        WHERE {" AND ".join(where)}
    """
    total = conn.execute(f"SELECT COUNT(*) {base}", avail_params + params).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.*, us.status, us.rating, avail.added {base} ORDER BY {order_by} "
        "LIMIT ? OFFSET ?",
        avail_params + params + [f.page_size, (f.page - 1) * f.page_size],
    ).fetchall()
    available = _availability_for(conn, [r["id"] for r in rows], services)
    return {
        "total": total, "page": f.page, "page_size": f.page_size, "services": services,
        "items": [_summary(r, available.get(r["id"], [])) for r in rows],
    }


def get_title(conn: sqlite3.Connection, title_id: int) -> dict | None:
    row = conn.execute(
        """SELECT t.*, us.status, us.rating FROM titles t
           LEFT JOIN user_state us ON us.title_id = t.id WHERE t.id = ?""",
        (title_id,),
    ).fetchone()
    if row is None:
        return None
    result = _summary(row, _availability_for(conn, [title_id]).get(title_id, []))
    service_by_provider = {
        p: {"key": s["key"], "free": bool(s["free"])}
        for s in conn.execute("SELECT key, provider_ids, free FROM services WHERE tracked = 1")
        for p in json.loads(s["provider_ids"])
    }
    offers = [
        {"provider_id": o["provider_id"], "provider": o["name"] or str(o["provider_id"]),
         "monetization": o["monetization"],
         "service": service_by_provider.get(o["provider_id"], {}).get("key")}
        for o in conn.execute(
            """SELECT o.provider_id, o.monetization, p.name FROM offers o
               LEFT JOIN providers p ON p.provider_id = o.provider_id
               WHERE o.title_id = ? ORDER BY o.monetization, p.name""",
            (title_id,),
        )
    ]
    seasons = [
        {"season_number": s["season_number"], "name": s["name"], "air_date": s["air_date"],
         "episode_count": s["episode_count"]}
        for s in conn.execute(
            "SELECT * FROM seasons WHERE title_id = ? ORDER BY season_number", (title_id,))
    ]
    result.update({
        "tmdb_id": row["tmdb_id"],
        "release_date": row["release_date"],
        "original_language": row["original_language"],
        "keywords": json.loads(row["keywords"]),
        "directors": json.loads(row["directors"]),
        "cast": json.loads(row["cast_members"]),
        "tv_status": row["tv_status"],
        "last_air_date": row["last_air_date"],
        "next_episode_date": row["next_episode_date"],
        "seasons": seasons,
        "offers": offers,
        "offers_known": row["offers_fetched_at"] is not None,
        "watch_link": row["watch_link"],
        "tmdb_url": f"https://www.themoviedb.org/{row['media_type']}/{row['tmdb_id']}",
        "imdb_url": f"https://www.imdb.com/title/{row['imdb_id']}/" if row["imdb_id"] else None,
        "attribution": ATTRIBUTION,
    })
    return result


def set_user_state(conn: sqlite3.Connection, title_id: int, changes: dict) -> dict:
    """`changes` may contain 'status' and/or 'rating'. A rating implies 'seen';
    marking a title unseen clears its rating."""
    if conn.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone() is None:
        raise LookupError(title_id)
    current = conn.execute("SELECT status, rating FROM user_state WHERE title_id = ?",
                           (title_id,)).fetchone()
    status = current["status"] if current else "unseen"
    rating = current["rating"] if current else None
    if "rating" in changes:
        rating = changes["rating"]
        if rating is not None and "status" not in changes:
            status = "seen"
    if "status" in changes:
        status = changes["status"]
        if status == "unseen" and "rating" not in changes:
            rating = None
    with conn:
        conn.execute(
            """INSERT INTO user_state (title_id, status, rating, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT (title_id) DO UPDATE SET status = excluded.status,
                   rating = excluded.rating, updated_at = excluded.updated_at""",
            (title_id, status, rating, now_iso()),
        )
    return {"status": status, "rating": rating}


def list_genres(conn: sqlite3.Connection, media_type: str | None = None) -> list[dict]:
    sql = """SELECT t.genres FROM titles t WHERE EXISTS (
                 SELECT 1 FROM availability a WHERE a.title_id = t.id
                 AND a.removed_at IS NULL AND a.verified = 1)"""
    params: list = []
    if media_type:
        sql += " AND t.media_type = ?"
        params.append(media_type)
    counts: dict[str, int] = {}
    for (raw,) in conn.execute(sql, params):
        for g in genres.unify(json.loads(raw)):
            counts[g] = counts.get(g, 0) + 1
    return [{"name": g, "count": n} for g, n in sorted(counts.items(), key=lambda x: -x[1])]


def list_events(conn: sqlite3.Connection, days: int, services: list[str] | None,
                include_free: bool | None, today: date | None = None) -> list[dict]:
    """News feed: arrivals in the selected services and new seasons of series available there."""
    today = today or date.today()
    selected = selected_services(conn, services, include_free)
    if not selected:
        return []
    since = (today - timedelta(days=days)).isoformat()
    ph = _placeholders(selected)
    rows = conn.execute(
        f"""
        SELECT e.id AS event_id, e.event, e.event_date, e.season_number, e.service AS event_service,
               t.*, us.status, us.rating
        FROM events e
        JOIN titles t ON t.id = e.title_id
        LEFT JOIN user_state us ON us.title_id = t.id
        WHERE e.event_date >= ?
          AND (
            (e.event IN ('added', 'readded') AND e.service IN ({ph}) AND EXISTS (
                SELECT 1 FROM availability a WHERE a.title_id = e.title_id AND a.service = e.service
                  AND a.removed_at IS NULL AND a.verified = 1))
            OR (e.event = 'new_season' AND EXISTS (
                SELECT 1 FROM availability a WHERE a.title_id = e.title_id AND a.service IN ({ph})
                  AND a.removed_at IS NULL AND a.verified = 1))
          )
        ORDER BY e.event_date DESC, t.popularity DESC
        """,
        [since, *selected, *selected],
    ).fetchall()
    available = _availability_for(conn, list({r["id"] for r in rows}), selected)
    return [
        {"event": r["event"], "date": r["event_date"], "season_number": r["season_number"],
         "service": r["event_service"], "title": _summary(r, available.get(r["id"], []))}
        for r in rows
    ]


def status(conn: sqlite3.Connection) -> dict:
    last = conn.execute(
        "SELECT MAX(finished_at) FROM snapshot_runs WHERE status != 'failed'").fetchone()[0]
    failed = conn.execute(
        """SELECT service, media_type, finished_at, message FROM snapshot_runs
           WHERE status = 'failed' AND finished_at >= COALESCE(?, '') ORDER BY run_id DESC""",
        (last,),
    ).fetchall()
    counts = conn.execute(
        """SELECT COUNT(*) AS titles, SUM(details_fetched_at IS NOT NULL) AS with_details,
                  SUM(offers_fetched_at IS NOT NULL) AS with_offers,
                  SUM(ratings_fetched_at IS NOT NULL) AS ratings_checked,
                  SUM(age_rating IS NOT NULL) AS with_age FROM titles"""
    ).fetchone()
    first = conn.execute(
        "SELECT MIN(started_at) FROM snapshot_runs WHERE status != 'failed'").fetchone()[0]
    compared = conn.execute(
        "SELECT 1 FROM snapshot_runs WHERE status IN ('ok', 'suspicious') LIMIT 1").fetchone()
    return {
        "first_snapshot": first,
        "has_comparison": compared is not None,
        "last_snapshot": last,
        "failed_since_last_snapshot": [dict(r) for r in failed],
        "titles": counts["titles"],
        "titles_with_details": counts["with_details"] or 0,
        "titles_with_offers": counts["with_offers"] or 0,
        "titles_ratings_checked": counts["ratings_checked"] or 0,
        "titles_with_age_rating": counts["with_age"] or 0,
        "attribution": ATTRIBUTION,
    }
