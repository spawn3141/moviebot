"""Daily catalog snapshot + diff against the stored state.

TMDB only knows the *current* catalog per provider. New arrivals and removals are
derived by comparing today's snapshot with what is stored in `availability`, separately
per (service, media type):

- The first successful run is a baseline: everything is stored, but no 'added' events
  are created (nothing gets announced).
- If `min_year` changed since the last run, the run is 'rescoped': titles that newly
  enter the scope are stored silently too – they are not new in the subscription.
- A title only counts as removed after missing `grace_runs` consecutive days, because
  JustWatch/TMDB data occasionally flickers. Titles outside the current scope are never
  marked removed (we simply stopped looking at them).
- If a snapshot is much smaller than the stored catalog (`max_drop_ratio`), the run is
  'suspicious' and no removals are counted (likely an incomplete API response).

Afterwards, details are fetched for titles that need them. That call also verifies the
availability and, for running series, detects newly released seasons.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import catalog
from .config import FREE_MONETIZATION, Config, Service
from .db import get_setting, now_iso, set_setting
from .tmdb import TMDBClient, TMDBError, TMDBNotFound, has_region_offers, provider_ids_with

log = logging.getLogger(__name__)

COMPLETED_STATUSES = ("baseline", "rescoped", "ok", "suspicious")
# How long a title may stay "not verifiable" before we treat it as not available after all.
UNVERIFIED_GRACE_DAYS = 14
FINISHED_TV_STATUSES = ("Ended", "Canceled")


@dataclass
class DiffResult:
    status: str  # baseline / rescoped / ok / suspicious
    total: int
    added: list[int] = field(default_factory=list)    # titles.id
    readded: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)


def last_completed_run(conn: sqlite3.Connection, service: str, media_type: str,
                       exclude_run_id: int | None = None) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT * FROM snapshot_runs
        WHERE service = ? AND media_type = ? AND run_id IS NOT ?
          AND status IN ({",".join("?" * len(COMPLETED_STATUSES))})
        ORDER BY run_id DESC LIMIT 1
        """,
        (service, media_type, exclude_run_id, *COMPLETED_STATUSES),
    ).fetchone()


def in_scope(media_type: str, year: int | None, last_air_date: str | None, min_year: int) -> bool:
    """Would the discover query for `min_year` include this title? Unknown counts as yes."""
    if media_type == "movie":
        return year is None or year >= min_year
    return last_air_date is None or last_air_date >= f"{min_year}-01-01"


def apply_snapshot(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    service: str,
    media_type: str,
    current_ids: set[int],
    today: date,
    min_year: int,
    grace_runs: int = 2,
    max_drop_ratio: float = 0.2,
) -> DiffResult:
    """Diff `current_ids` (titles.id) against the stored availability of one service."""
    day = today.isoformat()
    known = {
        r["title_id"]: r
        for r in conn.execute(
            """SELECT a.title_id, a.removed_at, a.missed_runs, a.last_missed_date,
                      t.year, t.last_air_date
               FROM availability a JOIN titles t ON t.id = a.title_id
               WHERE a.service = ? AND t.media_type = ?""",
            (service, media_type),
        )
    }
    active_in_scope = {
        title_id for title_id, r in known.items()
        if r["removed_at"] is None and in_scope(media_type, r["year"], r["last_air_date"], min_year)
    }
    previous = last_completed_run(conn, service, media_type, exclude_run_id=run_id)
    if previous is None:
        status = "baseline"
    elif previous["min_year"] != min_year:
        status = "rescoped"
    elif active_in_scope and len(current_ids) < len(active_in_scope) * (1 - max_drop_ratio):
        status = "suspicious"
    else:
        status = "ok"
    silent = status in ("baseline", "rescoped")
    result = DiffResult(status=status, total=len(current_ids))

    def event(title_id: int, kind: str) -> None:
        conn.execute(
            """INSERT INTO events (run_id, title_id, service, event, event_date)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, title_id, service, kind, day),
        )

    for title_id in current_ids:
        row = known.get(title_id)
        if row is None:
            conn.execute(
                """INSERT INTO availability (title_id, service, first_seen, last_seen, in_baseline)
                   VALUES (?, ?, ?, ?, ?)""",
                (title_id, service, day, day, int(silent)),
            )
            if not silent:
                event(title_id, "added")
                result.added.append(title_id)
        elif row["removed_at"] is not None:
            # Back after a removal: first_seen stays, availability gets re-verified.
            conn.execute(
                """UPDATE availability SET removed_at = NULL, last_seen = ?, missed_runs = 0,
                          last_missed_date = NULL, verified = NULL
                   WHERE title_id = ? AND service = ?""",
                (day, title_id, service),
            )
            if not silent:
                event(title_id, "readded")
                result.readded.append(title_id)
        else:
            conn.execute(
                """UPDATE availability SET last_seen = ?, missed_runs = 0, last_missed_date = NULL
                   WHERE title_id = ? AND service = ?""",
                (day, title_id, service),
            )

    if status == "suspicious":
        log.warning("%s/%s: Snapshot hat nur %d Titel (gespeichert aktiv: %d) – "
                    "Entfernungen übersprungen", service, media_type, len(current_ids),
                    len(active_in_scope))
        return result

    for title_id in active_in_scope - current_ids:
        row = known[title_id]
        if row["last_missed_date"] == day:
            continue  # already counted today (second run on the same day)
        missed = row["missed_runs"] + 1
        removed_at = day if missed >= grace_runs else None
        conn.execute(
            """UPDATE availability SET missed_runs = ?, last_missed_date = ?, removed_at = ?
               WHERE title_id = ? AND service = ?""",
            (missed, day, removed_at, title_id, service),
        )
        if removed_at:
            event(title_id, "removed")
            result.removed.append(title_id)
    return result


def snapshot_service(conn: sqlite3.Connection, client: TMDBClient, cfg: Config, service: Service,
                     media_type: str, genre_names: dict[int, str], today: date) -> DiffResult | None:
    with conn:
        run_id = conn.execute(
            """INSERT INTO snapshot_runs (service, media_type, min_year, started_at, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (service.key, media_type, cfg.min_year, now_iso()),
        ).lastrowid

    try:
        items = client.discover_catalog(media_type, service.provider_ids,
                                        cfg.monetization_for(service), cfg.min_year)
    except TMDBError as e:
        log.error("Snapshot %s/%s fehlgeschlagen: %s", service.key, media_type, e)
        with conn:
            conn.execute(
                "UPDATE snapshot_runs SET status = 'failed', finished_at = ?, message = ? WHERE run_id = ?",
                (now_iso(), str(e), run_id),
            )
        return None

    with conn:
        title_ids = {
            catalog.upsert_from_discover(conn, media_type, item, genre_names)
            for item in items.values()
        }
        result = apply_snapshot(
            conn, run_id=run_id, service=service.key, media_type=media_type,
            current_ids=title_ids, today=today, min_year=cfg.min_year,
            grace_runs=cfg.removal_grace_runs, max_drop_ratio=cfg.max_drop_ratio,
        )
        conn.execute(
            """UPDATE snapshot_runs SET status = ?, finished_at = ?, title_count = ?,
                      added_count = ?, removed_count = ? WHERE run_id = ?""",
            (result.status, now_iso(), result.total, len(result.added) + len(result.readded),
             len(result.removed), run_id),
        )
    return result


def refresh_manual_availability(conn: sqlite3.Connection, cfg: Config, title_id: int, today: date,
                                announce: bool) -> None:
    """Availability of a hand-picked title comes from its own offers, not from the catalogs
    (it usually lies outside the year scope). `announce` creates added/removed events; the
    very first import stays silent, because the title is not new in the service."""
    day = today.isoformat()
    offered = catalog.offered_services(conn, title_id, cfg.monetization)
    known = {r["service"]: r for r in conn.execute(
        """SELECT service, removed_at, missed_runs, last_missed_date FROM availability
           WHERE title_id = ?""", (title_id,))}

    def event(service: str, kind: str) -> None:
        if announce:
            conn.execute("""INSERT INTO events (title_id, service, event, event_date)
                            VALUES (?, ?, ?, ?)""", (title_id, service, kind, day))

    for service in offered:
        row = known.get(service)
        if row is None:
            conn.execute(
                """INSERT INTO availability (title_id, service, first_seen, last_seen,
                       in_baseline, verified) VALUES (?, ?, ?, ?, ?, 1)""",
                (title_id, service, day, day, int(not announce)),
            )
            event(service, "added")
        elif row["removed_at"] is not None:
            conn.execute(
                """UPDATE availability SET removed_at = NULL, last_seen = ?, missed_runs = 0,
                          last_missed_date = NULL, verified = 1
                   WHERE title_id = ? AND service = ?""", (day, title_id, service))
            event(service, "readded")
        else:
            conn.execute(
                """UPDATE availability SET last_seen = ?, missed_runs = 0, last_missed_date = NULL,
                          verified = 1 WHERE title_id = ? AND service = ?""",
                (day, title_id, service))

    for service, row in known.items():
        if service in offered or row["removed_at"] is not None or row["last_missed_date"] == day:
            continue
        missed = row["missed_runs"] + 1
        removed_at = day if missed >= cfg.removal_grace_runs else None
        conn.execute(
            """UPDATE availability SET missed_runs = ?, last_missed_date = ?, removed_at = ?
               WHERE title_id = ? AND service = ?""", (missed, day, removed_at, title_id, service))
        if removed_at:
            event(service, "removed")


def import_title(conn: sqlite3.Connection, client: TMDBClient, cfg: Config, media_type: str,
                 tmdb_id: int, today: date | None = None) -> int:
    """Add a title by hand, independent of the year scope. Returns titles.id."""
    today = today or date.today()
    details = client.details(media_type, tmdb_id)
    with conn:
        title_id, _ = catalog.upsert_details(conn, media_type, details)
        catalog.store_offers(conn, title_id, details, cfg.region)
        catalog.store_age_rating(conn, title_id, media_type, details)
        catalog.mark_manual(conn, title_id)
        if media_type == "tv":
            catalog.update_seasons(conn, title_id, details.get("seasons", []), today,
                                   emit_events=False)
        refresh_manual_availability(conn, cfg, title_id, today, announce=False)
    return title_id


def update_details(conn: sqlite3.Connection, client: TMDBClient, cfg: Config, today: date,
                   limit: int | None = None, backfill: int = 0,
                   changed_tv_ids: set[int] | None = None,
                   progress: Callable[[dict], None] | None = None) -> int:
    """Fetch details where needed. One request per title does three jobs:

    1. metadata (genres, keywords, directors/creators, cast) for titles that lack it,
    2. verification of unverified availability via the title's watch/providers – the discover
       filter might match a title that is only rent/buy at this service (`verified = 0`),
    3. for running series: refresh seasons to detect new ones. Instead of asking for every
       series daily, only those TMDB reports as changed (`changed_tv_ids`) are refreshed, plus
       every series that has not been refreshed for `series_refresh_days` days as a safety net.

    Every fetch also stores all offers of the title (`offers`) and its age rating. Titles from
    before these were stored only get them when they are fetched anyway; `backfill` adds up to
    that many of them (most popular first) to this run.
    """
    changed = sorted(changed_tv_ids) if changed_tv_ids else []
    stale_before = (today - timedelta(days=cfg.series_refresh_days)).isoformat()
    todo = conn.execute(
        f"""
        SELECT t.id, t.media_type, t.tmdb_id, t.manual FROM titles t
        WHERE t.details_fetched_at IS NULL
           OR (EXISTS (SELECT 1 FROM availability a
                       WHERE a.title_id = t.id AND a.removed_at IS NULL AND a.verified IS NULL)
               AND (t.details_fetched_at IS NULL OR substr(t.details_fetched_at, 1, 10) < ?))
           OR (t.manual = 1
               AND (t.details_fetched_at IS NULL OR substr(t.details_fetched_at, 1, 10) < ?))
           OR (t.media_type = 'tv'
               AND COALESCE(t.tv_status, '') NOT IN ({",".join("?" * len(FINISHED_TV_STATUSES))})
               AND substr(t.details_fetched_at, 1, 10) < ?
               AND (t.tmdb_id IN ({",".join("?" * len(changed)) or "NULL"})
                    OR substr(t.details_fetched_at, 1, 10) < ?)
               AND EXISTS (SELECT 1 FROM availability a
                           WHERE a.title_id = t.id AND a.removed_at IS NULL))
        ORDER BY t.popularity DESC
        """,
        (today.isoformat(), today.isoformat(), *FINISHED_TV_STATUSES, today.isoformat(),
         *changed, stale_before),
    ).fetchall()
    if backfill:
        planned = {r["id"] for r in todo}
        todo += [
            r for r in conn.execute(
                """SELECT id, media_type, tmdb_id FROM titles
                   WHERE offers_fetched_at IS NULL OR ratings_fetched_at IS NULL
                   ORDER BY popularity DESC"""
            ) if r["id"] not in planned
        ][:backfill]
    if limit is not None:
        todo = todo[:limit]
    log.info("Details für %d Titel", len(todo))
    if progress:
        progress({"phase": "details", "done": 0, "total": len(todo), "label": "Titeldaten"})

    # service -> (provider IDs, monetization types that count for it)
    service_offers = {
        r["key"]: (set(json.loads(r["provider_ids"])),
                   FREE_MONETIZATION if r["free"] else cfg.monetization)
        for r in conn.execute("SELECT key, provider_ids, free FROM services")
    }
    for i, row in enumerate(todo, 1):
        try:
            details = client.details(row["media_type"], row["tmdb_id"])
        except TMDBNotFound:
            log.warning("%s %s existiert bei TMDB nicht mehr", row["media_type"], row["tmdb_id"])
            continue
        except TMDBError as e:
            log.error("Details für %s %s fehlgeschlagen: %s", row["media_type"], row["tmdb_id"], e)
            continue
        with conn:
            title_id, had_details = catalog.upsert_details(conn, row["media_type"], details)
            catalog.store_offers(conn, title_id, details, cfg.region)
            catalog.store_age_rating(conn, title_id, row["media_type"], details)
            known_in_region = has_region_offers(details, cfg.region)
            for a in conn.execute(
                "SELECT service, first_seen FROM availability WHERE title_id = ? AND removed_at IS NULL",
                (title_id,),
            ).fetchall():
                providers, monetization = service_offers.get(a["service"], (set(), []))
                ok: int | None = int(bool(providers & provider_ids_with(details, cfg.region, monetization)))
                if not known_in_region:
                    # TMDB has no German data at all – unknown, not contradicted. Brand-new
                    # titles often land here for a day or two; after that we give up on them.
                    fresh = a["first_seen"] >= (today - timedelta(days=UNVERIFIED_GRACE_DAYS)).isoformat()
                    ok = None if fresh else 0
                conn.execute("UPDATE availability SET verified = ? WHERE title_id = ? AND service = ?",
                             (ok, title_id, a["service"]))
            if row["media_type"] == "tv":
                catalog.update_seasons(conn, title_id, details.get("seasons", []), today,
                                       emit_events=had_details)
            if conn.execute("SELECT manual FROM titles WHERE id = ?", (title_id,)).fetchone()[0]:
                refresh_manual_availability(conn, cfg, title_id, today, announce=True)
        if progress and (i % 10 == 0 or i == len(todo)):
            progress({"phase": "details", "done": i, "total": len(todo), "label": "Titeldaten"})
        if i % 250 == 0:
            log.info("  %d/%d", i, len(todo))
    return len(todo)


def changed_series(conn: sqlite3.Connection, client: TMDBClient, cfg: Config,
                    today: date) -> set[int] | None:
    """Series TMDB changed since our last check (None = could not be determined)."""
    last = get_setting(conn, "tv_changes_checked_until")
    # TMDB serves at most 14 days of changes
    start = max(date.fromisoformat(last) if last else today - timedelta(days=1),
                today - timedelta(days=13))
    try:
        ids = client.changed_ids("tv", start, today)
    except TMDBError as e:
        log.warning("Änderungsliste nicht abrufbar (%s) – alle laufenden Serien werden geprüft", e)
        return None
    with conn:
        set_setting(conn, "tv_changes_checked_until", today.isoformat())
    log.info("TMDB meldet %d geänderte Serien seit %s", len(ids), start)
    return ids


def check_provider_ids(conn: sqlite3.Connection, cfg: Config) -> list[str]:
    """Warnings for configured provider IDs that TMDB does not know in this region."""
    known = {r[0] for r in conn.execute("SELECT provider_id FROM providers")}
    return [
        f"Dienst '{s.key}': Provider-ID {p} ist in Region {cfg.region} unbekannt"
        for s in cfg.services for p in s.provider_ids if p not in known
    ]


def run(conn: sqlite3.Connection, client: TMDBClient, cfg: Config, *,
        service_keys: list[str] | None = None, fetch_details: bool = True,
        details_limit: int | None = None, backfill: int = 0, today: date | None = None,
        progress: Callable[[dict], None] | None = None
        ) -> dict[tuple[str, str], DiffResult | None]:
    today = today or date.today()
    if not cfg.services:
        raise ValueError("Keine Dienste konfiguriert (config.toml → [[services]]).")
    services = [cfg.service(k) for k in service_keys] if service_keys else cfg.services

    catalog.bootstrap(conn, cfg)
    with conn:
        for media_type in ("movie", "tv"):
            catalog.upsert_providers(conn, client.watch_providers(media_type))
    for warning in check_provider_ids(conn, cfg):
        log.warning(warning)

    results = {}
    total_catalogs = len(services) * len(cfg.media_types)
    for media_type in cfg.media_types:
        genre_names = client.genres(media_type)
        for service in services:
            if progress:
                progress({"phase": "catalogs", "done": len(results), "total": total_catalogs,
                          "label": f"{service.name} – {'Serien' if media_type == 'tv' else 'Filme'}"})
            results[(service.key, media_type)] = snapshot_service(
                conn, client, cfg, service, media_type, genre_names, today
            )
    if progress:
        progress({"phase": "catalogs", "done": total_catalogs, "total": total_catalogs,
                  "label": "Kataloge"})
    if fetch_details:
        update_details(conn, client, cfg, today, limit=details_limit, backfill=backfill,
                       changed_tv_ids=changed_series(conn, client, cfg, today), progress=progress)
    return results
