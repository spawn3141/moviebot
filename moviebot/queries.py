"""Read/write queries behind the REST API. Plain dicts in, plain dicts out."""

import json
import sqlite3
from dataclasses import dataclass, field, replace
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
# for the "added" sorting: the day we noticed a new season
LAST_SEASON_EVENT_SQL = """(SELECT MAX(e.event_date) FROM events e
    WHERE e.title_id = t.id AND e.event = 'new_season')"""

NEWEST_SEASON_SQL = """COALESCE((SELECT MAX(s.air_date) FROM seasons s
    WHERE s.title_id = t.id AND s.season_number > 0
      AND s.air_date <= date('now', 'localtime')), t.release_date)"""

SORTS = {
    # rowid as tie-breaker: two titles added within the same second keep their order
    "list_added": "li.added_at DESC, li.rowid DESC",  # only with list_id
    "popularity": "t.popularity DESC",
    "rating": f"{WEIGHTED_RATING_SQL} DESC",
    "newest": "t.release_date DESC",
    # newest of: came to a service / got a new season
    "added": "activity DESC, t.popularity DESC",
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
    list_id: int | None = None             # only titles on this list
    filter_id: int | None = None           # apply this saved filter
    only_available: bool | None = None     # None = True, except when a list is shown
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
        LEFT JOIN availability a ON a.service = s.key AND a.removed_at IS NULL AND COALESCE(a.verified, 1) = 1
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


# Sentinels in `services`, so that saved filters keep working when services are added later
ALL_SERVICES = "all"    # every tracked service
MY_SERVICES = "mine"    # my subscriptions (+ free ones, depending on the setting) – the default


def selected_services(conn: sqlite3.Connection, services: list[str] | None,
                      include_free: bool | None) -> list[str]:
    if services and ALL_SERVICES in services:
        return [r[0] for r in conn.execute("SELECT key FROM services WHERE tracked = 1")]
    if services and MY_SERVICES in services:
        services = [s for s in services if s != MY_SERVICES] or None
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


# --- saved filters: what may be stored -----------------------------------------------

# Names as in the HTTP API. Everything else about a view – sorting, paging, "gesehene zeigen" –
# belongs to the request, not to the saved filter.
SAVED_FILTERS = {
    "services": "services", "include_free": "include_free", "media_type": "media_type",
    "genre": "genres", "year_from": "year_from", "year_to": "year_to", "q": "q",
    "min_rating": "min_rating", "max_age": "max_age", "include_unrated": "include_unrated",
    "new_days": "new_days",
}
# stored for the UI (e.g. its preferred sorting), ignored when filtering
TOLERATED_FILTER_KEYS = {"sort", "show_seen", "show_not_interested", "only_available"}

# The web UI sends numbers as strings ("2020"); SQLite would compare those as text.
FILTER_TYPES = {"year_from": int, "year_to": int, "max_age": int, "new_days": int,
                "min_rating": float, "include_free": bool, "include_unrated": bool}


def clean_filters(filters: dict) -> dict:
    unknown = set(filters) - set(SAVED_FILTERS) - TOLERATED_FILTER_KEYS
    if unknown:
        raise ValueError(f"Unbekannte Filter: {', '.join(sorted(unknown))}")
    empty = (None, "", [], {})
    cleaned = {}
    for key, value in filters.items():
        if value in empty:
            continue
        convert = FILTER_TYPES.get(key)
        if convert is bool:
            value = value not in (False, "false", "0", 0)
        elif convert:
            try:
                value = convert(value)
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' muss eine Zahl sein, nicht {value!r}") from None
        elif key in ("genre", "services"):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"'{key}' muss eine Liste von Texten sein")
        elif key == "media_type" and value not in ("movie", "tv"):
            raise ValueError("'media_type' muss 'movie' oder 'tv' sein")
        cleaned[key] = value
    if not set(cleaned) & set(SAVED_FILTERS):
        raise ValueError("Ein gespeicherter Filter braucht mindestens eine Einschränkung")
    return cleaned


def _apply_saved(f: "TitleFilter", stored: dict) -> "TitleFilter":
    """A saved filter wins over the request; filters it does not define still narrow further."""
    changes = {SAVED_FILTERS[k]: v for k, v in stored.items() if k in SAVED_FILTERS}
    return replace(f, filter_id=None, **changes)


# --- titles ------------------------------------------------------------------------

def _placeholders(values) -> str:
    return ",".join("?" * len(values))


def _lists_for(conn: sqlite3.Connection, title_ids: list[int]) -> dict[int, list[int]]:
    if not title_ids:
        return {}
    result: dict[int, list[int]] = {}
    for r in conn.execute(
        f"""SELECT title_id, list_id FROM list_items WHERE title_id IN ({_placeholders(title_ids)})
            ORDER BY list_id""", title_ids):
        result.setdefault(r["title_id"], []).append(r["list_id"])
    return result


def _recent_events(conn: sqlite3.Connection, title_ids: list[int], since: str,
                   services: list[str]) -> dict[int, dict]:
    """Newest relevant event per title: arrival in a service or a new season."""
    if not title_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT e.title_id, e.event, e.event_date, e.season_number, s.name AS service_name
        FROM events e LEFT JOIN services s ON s.key = e.service
        WHERE e.title_id IN ({_placeholders(title_ids)}) AND e.event_date >= ?
          AND (e.event = 'new_season' OR (e.event IN ('added', 'readded')
               AND e.service IN ({_placeholders(services)})))
        ORDER BY e.event_date, e.id
        """,
        [*title_ids, since, *services],
    )
    result: dict[int, dict] = {}
    for r in rows:  # ordered oldest first, so the newest wins
        result[r["title_id"]] = {"event": r["event"], "date": r["event_date"],
                                 "season_number": r["season_number"], "service": r["service_name"]}
    return result


def _summary(row: sqlite3.Row, available: list[dict], in_lists: list[int] | None = None,
             recent: dict | None = None) -> dict:
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
        "in_lists": in_lists or [],
        "manual": bool(row["manual"]),
        "recent": recent,
        "user": {"status": row["status"] or "unseen", "rating": row["rating"]},
    }


def _availability_for(conn: sqlite3.Connection, title_ids: list[int],
                      services: list[str] | None = None) -> dict[int, list[dict]]:
    if not title_ids:
        return {}
    sql = f"""
        SELECT a.title_id, s.key, s.name, s.free, a.first_seen, a.in_baseline, a.verified
        FROM availability a JOIN services s ON s.key = a.service
        WHERE a.title_id IN ({_placeholders(title_ids)})
          AND a.removed_at IS NULL AND COALESCE(a.verified, 1) = 1 AND s.tracked = 1
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
            "verified": None if r["verified"] is None else bool(r["verified"]),
            # picked up by the first scan: the title was there already, `since` is just that day
            "baseline": bool(r["in_baseline"]),
        })
    return result


def search_titles(conn: sqlite3.Connection, f: TitleFilter, today: date | None = None) -> dict:
    today = today or date.today()
    if f.list_id is not None:
        _one_list(conn, f.list_id)  # 404 instead of an empty page for an unknown list
    if f.filter_id is not None:
        f = _apply_saved(f, _one_filter(conn, f.filter_id)["filters"])
    services = selected_services(conn, f.services, f.include_free)
    if f.sort not in SORTS:
        raise ValueError(f"Unbekannte Sortierung '{f.sort}'. Möglich: {', '.join(SORTS)}")
    if f.sort == "list_added" and not f.list_id:
        raise ValueError("Sortierung 'list_added' gibt es nur innerhalb einer Liste")
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
    since = (today - timedelta(days=f.new_days or 0)).isoformat()
    if f.new_days is not None:
        where.append(f"""(avail.added >= ? OR EXISTS (
            SELECT 1 FROM events e WHERE e.title_id = t.id AND e.event = 'new_season'
              AND e.event_date >= ?))""")
        params += [since, since]

    # A list may contain titles that are currently in none of the services; only then is the
    # availability join optional.
    require_available = f.only_available if f.only_available is not None else f.list_id is None
    avail_join = "JOIN" if require_available else "LEFT JOIN"
    list_join = "JOIN list_items li ON li.title_id = t.id AND li.list_id = ?" if f.list_id else ""
    list_params = [f.list_id] if f.list_id else []

    # "added" = newest first_seen among the selected services, ignoring baseline entries
    base = f"""
        FROM titles t
        {list_join}
        {avail_join} (SELECT a.title_id,
                     MAX(CASE WHEN a.in_baseline = 0 THEN a.first_seen END) AS added
              FROM availability a
              WHERE a.removed_at IS NULL AND COALESCE(a.verified, 1) = 1
                AND a.service IN ({_placeholders(services)})
              GROUP BY a.title_id) avail ON avail.title_id = t.id
        LEFT JOIN user_state us ON us.title_id = t.id
        WHERE {" AND ".join(where)}
    """
    avail_params = list_params + avail_params
    total = conn.execute(f"SELECT COUNT(*) {base}", avail_params + params).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.*, us.status, us.rating, avail.added, "
        f"MAX(COALESCE(avail.added, ''), COALESCE({LAST_SEASON_EVENT_SQL}, '')) AS activity "
        f"{base} ORDER BY {order_by} "
        "LIMIT ? OFFSET ?",
        avail_params + params + [f.page_size, (f.page - 1) * f.page_size],
    ).fetchall()
    ids = [r["id"] for r in rows]
    # inside a list also show services that are not currently selected
    available = _availability_for(conn, ids, None if f.list_id else services)
    lists = _lists_for(conn, ids)
    recent = _recent_events(conn, ids, since, services) if f.new_days is not None else {}
    return {
        "total": total, "page": f.page, "page_size": f.page_size, "services": services,
        "items": [_summary(r, available.get(r["id"], []), lists.get(r["id"], []), recent.get(r["id"]))
                  for r in rows],
    }


def get_title(conn: sqlite3.Connection, title_id: int) -> dict | None:
    row = conn.execute(
        """SELECT t.*, us.status, us.rating FROM titles t
           LEFT JOIN user_state us ON us.title_id = t.id WHERE t.id = ?""",
        (title_id,),
    ).fetchone()
    if row is None:
        return None
    result = _summary(row, _availability_for(conn, [title_id]).get(title_id, []),
                      _lists_for(conn, [title_id]).get(title_id, []))
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
                 AND a.removed_at IS NULL AND COALESCE(a.verified, 1) = 1)"""
    params: list = []
    if media_type:
        sql += " AND t.media_type = ?"
        params.append(media_type)
    counts: dict[str, int] = {}
    for (raw,) in conn.execute(sql, params):
        for g in genres.unify(json.loads(raw)):
            counts[g] = counts.get(g, 0) + 1
    return [{"name": g, "count": n} for g, n in sorted(counts.items(), key=lambda x: -x[1])]


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


# --- lists (collections of titles) and saved filters (stored searches) ---------------

MAX_NAME = 60


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Der Name darf nicht leer sein")
    if len(name) > MAX_NAME:
        raise ValueError(f"Der Name darf höchstens {MAX_NAME} Zeichen haben")
    return name


def list_lists(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT l.*, (SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id) AS count
           FROM lists l ORDER BY l.is_default DESC, l.position, l.name COLLATE NOCASE"""
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "is_default": bool(r["is_default"]),
             "count": r["count"]} for r in rows]


def _one_list(conn: sqlite3.Connection, list_id: int) -> dict:
    found = [entry for entry in list_lists(conn) if entry["id"] == list_id]
    if not found:
        raise LookupError(list_id)
    return found[0]


def create_list(conn: sqlite3.Connection, name: str) -> dict:
    now = now_iso()
    with conn:
        list_id = conn.execute(
            "INSERT INTO lists (name, created_at, updated_at) VALUES (?, ?, ?) RETURNING id",
            (_clean_name(name), now, now),
        ).fetchone()[0]
    return _one_list(conn, list_id)


def update_list(conn: sqlite3.Connection, list_id: int, name: str | None = None,
                is_default: bool | None = None) -> dict:
    entry = _one_list(conn, list_id)
    with conn:
        if name is not None:
            conn.execute("UPDATE lists SET name = ?, updated_at = ? WHERE id = ?",
                         (_clean_name(name), now_iso(), list_id))
        if is_default:
            conn.execute("UPDATE lists SET is_default = 0 WHERE is_default = 1")
            conn.execute("UPDATE lists SET is_default = 1, updated_at = ? WHERE id = ?",
                         (now_iso(), list_id))
        elif is_default is False and entry["is_default"]:
            raise ValueError("Es muss eine Standardliste geben – lege stattdessen eine andere fest")
    return _one_list(conn, list_id)


def delete_list(conn: sqlite3.Connection, list_id: int) -> None:
    if _one_list(conn, list_id)["is_default"]:
        raise ValueError("Die Standardliste kann nicht gelöscht werden – "
                         "lege zuerst eine andere Liste als Standard fest")
    with conn:
        conn.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
        conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))


def set_title_lists(conn: sqlite3.Connection, title_id: int, list_ids: list[int]) -> list[int]:
    """Replace the list membership of one title. Returns the list ids it is on now."""
    if conn.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone() is None:
        raise LookupError(title_id)
    known = {r[0] for r in conn.execute("SELECT id FROM lists")}
    unknown = [i for i in list_ids if i not in known]
    if unknown:
        raise ValueError(f"Unbekannte Listen: {', '.join(map(str, unknown))}")
    now = now_iso()
    with conn:
        conn.execute("DELETE FROM list_items WHERE title_id = ?", (title_id,))
        conn.executemany(
            "INSERT INTO list_items (list_id, title_id, added_at) VALUES (?, ?, ?)",
            [(list_id, title_id, now) for list_id in dict.fromkeys(list_ids)],
        )
    return _lists_for(conn, [title_id]).get(title_id, [])


def list_saved_filters(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM saved_filters ORDER BY position, name COLLATE NOCASE").fetchall()
    result = []
    for r in rows:
        entry = {"id": r["id"], "name": r["name"], "filters": json.loads(r["filters"]),
                 "count": 0, "error": None}
        try:
            f = _apply_saved(TitleFilter(page_size=1), entry["filters"])
            entry["count"] = search_titles(conn, f)["total"]
        except ValueError as e:  # e.g. a service in the filter no longer exists
            entry["error"] = str(e)
        result.append(entry)
    return result


def _one_filter(conn: sqlite3.Connection, filter_id: int) -> dict:
    found = [entry for entry in list_saved_filters(conn) if entry["id"] == filter_id]
    if not found:
        raise LookupError(filter_id)
    return found[0]


def create_saved_filter(conn: sqlite3.Connection, name: str, filters: dict) -> dict:
    now = now_iso()
    with conn:
        filter_id = conn.execute(
            """INSERT INTO saved_filters (name, filters, created_at, updated_at)
               VALUES (?, ?, ?, ?) RETURNING id""",
            (_clean_name(name), json.dumps(clean_filters(filters), ensure_ascii=False), now, now),
        ).fetchone()[0]
    return _one_filter(conn, filter_id)


def update_saved_filter(conn: sqlite3.Connection, filter_id: int, name: str | None = None,
                        filters: dict | None = None) -> dict:
    _one_filter(conn, filter_id)
    with conn:
        if name is not None:
            conn.execute("UPDATE saved_filters SET name = ?, updated_at = ? WHERE id = ?",
                         (_clean_name(name), now_iso(), filter_id))
        if filters is not None:
            conn.execute("UPDATE saved_filters SET filters = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(clean_filters(filters), ensure_ascii=False), now_iso(), filter_id))
    return _one_filter(conn, filter_id)


def delete_saved_filter(conn: sqlite3.Connection, filter_id: int) -> None:
    _one_filter(conn, filter_id)
    with conn:
        conn.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))


# --- what a snapshot run changed -----------------------------------------------------

def last_event_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def store_run_summary(conn: sqlite3.Connection, summary: dict) -> None:
    """Keep the last summary in the database so it survives a restart."""
    with conn:
        set_setting(conn, "last_run_summary", json.dumps(summary, ensure_ascii=False))


def get_run_summary(conn: sqlite3.Connection) -> dict | None:
    stored = get_setting(conn, "last_run_summary")
    return json.loads(stored) if stored else None


def run_summary(conn: sqlite3.Connection, results: dict, since_event_id: int) -> dict:
    """Counts of what a snapshot run produced, for the UI and the log."""
    counts = dict(conn.execute(
        "SELECT event, COUNT(*) FROM events WHERE id > ? GROUP BY event", (since_event_id,)))
    return {
        "added": counts.get("added", 0),
        "readded": counts.get("readded", 0),
        "removed": counts.get("removed", 0),
        "new_seasons": counts.get("new_season", 0),
        "failed": [f"{service}/{media_type}"
                   for (service, media_type), r in results.items() if r is None],
        "finished_at": now_iso(),
    }


# --- adding titles by hand -----------------------------------------------------------

def search_results(conn: sqlite3.Connection, results: list[dict]) -> list[dict]:
    """TMDB search hits, marked with what we already know about them."""
    known = {
        (r["media_type"], r["tmdb_id"]): r
        for r in conn.execute("SELECT media_type, tmdb_id, id, manual FROM titles")
    }
    out = []
    for r in results:
        media_type = r["media_type"]
        released = (r.get("release_date") or r.get("first_air_date") or "")[:4]
        entry = known.get((media_type, r["id"]))
        out.append({
            "media_type": media_type,
            "tmdb_id": r["id"],
            "title": r.get("title") or r.get("name") or "?",
            "year": int(released) if released.isdigit() else None,
            "overview": r.get("overview") or None,
            "poster_url": f"{POSTER_BASE}{r['poster_path']}" if r.get("poster_path") else None,
            "title_id": entry["id"] if entry else None,
            "manual": bool(entry["manual"]) if entry else False,
        })
    return out


def drop_manual_title(conn: sqlite3.Connection, title_id: int) -> None:
    """Undo a manual import: the title keeps its data and ratings, but is no longer
    kept up to date and no longer counts as available anywhere."""
    row = conn.execute("SELECT manual FROM titles WHERE id = ?", (title_id,)).fetchone()
    if row is None:
        raise LookupError(title_id)
    if not row["manual"]:
        raise ValueError("Dieser Titel wurde nicht von Hand hinzugefügt")
    with conn:
        conn.execute("DELETE FROM availability WHERE title_id = ?", (title_id,))
        conn.execute("DELETE FROM events WHERE title_id = ?", (title_id,))
        catalog.mark_manual(conn, title_id, False)
