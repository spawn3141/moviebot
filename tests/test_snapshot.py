import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from moviebot import catalog, db, queries, snapshot
from moviebot.config import Config, Service
from moviebot.db import connect, now_iso
from moviebot.tmdb import MAX_DISCOVER_RESULTS, TMDBClient, TMDBError

D0 = date(2026, 9, 1)
SERVICES = [Service("prime", "Prime Video", [9, 2100]), Service("wow", "WOW", [30])]


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def make_db(testcase) -> "sqlite3.Connection":
    conn = connect(":memory:")
    testcase.addCleanup(conn.close)
    with conn:
        catalog.sync_services(conn, Config(services=SERVICES, initial_subscriptions=["prime"]))
    return conn


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db(self)
        self.ids: dict[int, int] = {}  # tmdb_id -> titles.id

    def run_day(self, n: int, tmdb_ids: set[int], *, min_year: int = 2025, years=None,
                media_type: str = "movie", **kwargs) -> snapshot.DiffResult:
        years = years or {}
        with self.conn:
            for tmdb_id in tmdb_ids:
                item = {"id": tmdb_id, "title": f"T{tmdb_id}", "name": f"S{tmdb_id}",
                        "release_date": f"{years.get(tmdb_id, 2025)}-05-01"}
                self.ids[tmdb_id] = catalog.upsert_from_discover(self.conn, media_type, item, {})
            run_id = self.conn.execute(
                "INSERT INTO snapshot_runs (service, media_type, min_year, started_at, status) "
                "VALUES ('prime', ?, ?, ?, 'running')", (media_type, min_year, now_iso()),
            ).lastrowid
            result = snapshot.apply_snapshot(
                self.conn, run_id=run_id, service="prime", media_type=media_type,
                current_ids={self.ids[t] for t in tmdb_ids}, today=day(n), min_year=min_year,
                **kwargs,
            )
            self.conn.execute("UPDATE snapshot_runs SET status = ? WHERE run_id = ?",
                              (result.status, run_id))
        return result

    def availability(self, tmdb_id: int):
        return self.conn.execute(
            "SELECT * FROM availability WHERE title_id = ? AND service = 'prime'", (self.ids[tmdb_id],)
        ).fetchone()

    def events(self) -> list[tuple]:
        tmdb_by_id = {v: k for k, v in self.ids.items()}
        return [(r["event_date"], r["event"], tmdb_by_id[r["title_id"]]) for r in self.conn.execute(
            "SELECT event_date, event, title_id FROM events ORDER BY id")]

    def test_first_run_is_silent_baseline(self):
        r = self.run_day(0, {1, 2, 3})
        self.assertEqual((r.status, r.added), ("baseline", []))
        self.assertEqual(self.events(), [])
        self.assertEqual(self.availability(1)["in_baseline"], 1)

    def test_new_title_after_baseline(self):
        self.run_day(0, {1, 2, 3})
        r = self.run_day(1, {1, 2, 3, 4})
        self.assertEqual((r.status, len(r.added)), ("ok", 1))
        self.assertEqual(self.availability(4)["first_seen"], day(1).isoformat())
        self.assertEqual(self.events(), [(day(1).isoformat(), "added", 4)])

    def test_movie_and_series_with_same_tmdb_id_are_separate(self):
        self.run_day(0, {1})
        movie_id = self.ids[1]
        r = self.run_day(0, {1}, media_type="tv")
        self.assertNotEqual(self.ids[1], movie_id)
        self.assertEqual(r.status, "baseline")  # own baseline per media type

    def test_removal_needs_grace_runs(self):
        self.run_day(0, {1, 2, 3, 4, 5})
        self.assertEqual(self.run_day(1, {1, 2, 3, 4}).removed, [])
        r = self.run_day(2, {1, 2, 3, 4})
        self.assertEqual(r.removed, [self.ids[5]])
        self.assertEqual(self.availability(5)["removed_at"], day(2).isoformat())

    def test_flicker_resets_missed_counter(self):
        self.run_day(0, {1, 2, 3, 4, 5})
        self.run_day(1, {1, 2, 3, 4})
        self.run_day(2, {1, 2, 3, 4, 5})
        r = self.run_day(3, {1, 2, 3, 4})
        self.assertEqual(r.removed, [])
        self.assertEqual(self.availability(5)["missed_runs"], 1)
        self.assertEqual(self.events(), [])

    def test_same_day_rerun_does_not_count_twice(self):
        self.run_day(0, {1, 2, 3, 4, 5})
        self.run_day(1, {1, 2, 3, 4})
        self.assertEqual(self.run_day(1, {1, 2, 3, 4}).removed, [])
        self.assertEqual(self.availability(5)["missed_runs"], 1)

    def test_readded_keeps_first_seen(self):
        self.run_day(0, {1, 2, 3, 4, 5}, grace_runs=1)
        self.run_day(1, {1, 2, 3, 4}, grace_runs=1)
        r = self.run_day(5, {1, 2, 3, 4, 5}, grace_runs=1)
        self.assertEqual(r.readded, [self.ids[5]])
        self.assertEqual(self.availability(5)["first_seen"], day(0).isoformat())
        self.assertEqual([e[1] for e in self.events()], ["removed", "readded"])

    def test_big_drop_is_suspicious_and_removes_nothing(self):
        self.run_day(0, set(range(1, 101)))
        r = self.run_day(1, set(range(1, 51)) | {500})
        self.assertEqual((r.status, len(r.added)), ("suspicious", 1))
        self.run_day(2, set(range(1, 51)))
        self.assertIsNone(self.availability(99)["removed_at"])
        self.assertEqual(self.availability(99)["missed_runs"], 0)

    def test_failed_run_does_not_count_as_baseline(self):
        self.conn.execute(
            "INSERT INTO snapshot_runs (service, media_type, min_year, started_at, status) "
            "VALUES ('prime', 'movie', 2025, ?, 'failed')", (now_iso(),))
        self.assertEqual(self.run_day(0, {1}).status, "baseline")

    def test_widening_scope_adds_old_titles_silently(self):
        self.run_day(0, {1, 2}, min_year=2025)
        r = self.run_day(1, {1, 2, 3, 4}, min_year=2015, years={3: 2018, 4: 2016})
        self.assertEqual((r.status, r.added), ("rescoped", []))
        self.assertEqual(self.availability(3)["in_baseline"], 1)
        # next day with unchanged scope: back to normal detection
        r = self.run_day(2, {1, 2, 3, 4, 5}, min_year=2015, years={3: 2018, 4: 2016})
        self.assertEqual((r.status, r.added), ("ok", [self.ids[5]]))

    def test_narrowing_scope_does_not_mark_old_titles_removed(self):
        new = set(range(10, 20))
        years = {1: 2016, 2: 2018} | {i: 2025 for i in new}
        self.run_day(0, {1, 2} | new, min_year=2015, years=years)
        for n in (1, 2, 3):
            self.run_day(n, new, min_year=2025, years=years)
        self.assertIsNone(self.availability(1)["removed_at"])
        self.assertEqual(self.events(), [])
        # an in-scope title that disappears is still detected
        self.run_day(4, new - {19}, min_year=2025, years=years)
        self.run_day(5, new - {19}, min_year=2025, years=years)
        self.assertEqual(self.events(), [(day(5).isoformat(), "removed", 19)])


class SeasonTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db(self)
        with self.conn:
            self.title_id = catalog.upsert_from_discover(self.conn, "tv", {"id": 7, "name": "Show"}, {})

    def update(self, n: int, seasons: list[dict], emit: bool = True) -> list[int]:
        with self.conn:
            return catalog.update_seasons(self.conn, self.title_id, seasons, day(n), emit_events=emit)

    def test_new_season_detected_when_air_date_passes(self):
        s1 = {"season_number": 1, "air_date": "2024-01-01", "episode_count": 8}
        s2 = {"season_number": 2, "air_date": day(3).isoformat(), "episode_count": 8}
        specials = {"season_number": 0, "air_date": "2023-01-01"}
        # first time: existing seasons are not "new"
        self.assertEqual(self.update(0, [specials, s1], emit=False), [])
        self.assertEqual(self.update(1, [specials, s1, s2]), [])  # announced, not yet aired
        self.assertEqual(self.update(3, [specials, s1, s2]), [2])
        self.assertEqual(self.update(4, [specials, s1, s2]), [])  # only once
        events = self.conn.execute("SELECT event, season_number, event_date FROM events").fetchall()
        self.assertEqual([tuple(e) for e in events], [("new_season", 2, day(3).isoformat())])


class FakeDiscoverClient(TMDBClient):
    """Pretends the catalog holds `n` titles, one date per title."""

    def __init__(self, n: int):
        super().__init__(api_key="x", min_interval=0)
        self.dates = {i: D0 - timedelta(days=i) for i in range(n)}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        field = "primary_release_date" if "movie" in path else "first_air_date"
        lo, hi = params.get(f"{field}.gte"), params.get(f"{field}.lte")
        ids = sorted(i for i, d in self.dates.items()
                     if (lo is None or d.isoformat() >= lo) and (hi is None or d.isoformat() <= hi))
        page = params["page"]
        chunk = ids[(page - 1) * 20: page * 20] if page <= 500 else []
        return {"total_results": len(ids), "total_pages": -(-len(ids) // 20),
                "results": [{"id": i} for i in chunk]}


class DiscoverTest(unittest.TestCase):
    def test_small_catalog_paginates(self):
        client = FakeDiscoverClient(45)
        self.assertEqual(len(client.discover_catalog("movie", [9, 2100], ["flatrate"], 1900)), 45)
        path, params = client.calls[0]
        self.assertEqual(path, "/discover/movie")
        self.assertEqual(params["with_watch_providers"], "9|2100")
        self.assertEqual(params["primary_release_date.gte"], "1900-01-01")

    def test_series_scope_uses_air_date(self):
        client = FakeDiscoverClient(5)
        client.discover_catalog("tv", [30], ["flatrate"], 2025)
        path, params = client.calls[0]
        self.assertEqual(path, "/discover/tv")
        self.assertEqual(params["air_date.gte"], "2025-01-01")
        self.assertNotIn("first_air_date.gte", params)

    def test_large_catalog_is_split_by_date(self):
        n = MAX_DISCOVER_RESULTS + 2500
        self.assertEqual(len(FakeDiscoverClient(n).discover_catalog("tv", [8], ["flatrate"], 2025)), n)


def fake_details(media_type: str, tmdb_id: int, flatrate: list[int], seasons=None) -> dict:
    d = {
        "id": tmdb_id, "genres": [{"id": 28, "name": "Action"}],
        "credits": {"crew": [{"job": "Director", "name": "D"}],
                    "cast": [{"name": "B", "order": 1}, {"name": "A", "order": 0}]},
        "watch/providers": {"results": {"DE": {
            "flatrate": [{"provider_id": p} for p in flatrate], "rent": [{"provider_id": 9}]}}},
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]}]},
        "content_ratings": {"results": [{"iso_3166_1": "DE", "rating": "6"}]},
    }
    if media_type == "movie":
        d.update(title=f"T{tmdb_id}", release_date="2025-05-01", imdb_id="tt0000001",
                 keywords={"keywords": [{"name": "heist"}]})
    else:
        d.update(name=f"S{tmdb_id}", first_air_date="2019-03-01", last_air_date="2026-08-01",
                 status="Returning Series", number_of_seasons=len(seasons or []),
                 created_by=[{"name": "C"}], episode_run_time=[45],
                 keywords={"results": [{"name": "spy"}]}, seasons=seasons or [],
                 external_ids={"imdb_id": "tt0000002"})
    return d


class DetailsTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db(self)
        self.cfg = Config(services=SERVICES)

    def add(self, media_type: str, tmdb_id: int, service: str = "prime",
            first_seen: str = "2026-09-01") -> int:
        with self.conn:
            title_id = catalog.upsert_from_discover(self.conn, media_type, {"id": tmdb_id}, {})
            self.conn.execute("INSERT INTO availability (title_id, service, first_seen, last_seen)"
                              " VALUES (?, ?, ?, ?)", (title_id, service, first_seen, first_seen))
        return title_id

    def user_state(self, title_id: int) -> tuple:
        row = self.conn.execute("SELECT status, rating FROM user_state WHERE title_id = ?",
                                (title_id,)).fetchone()
        return (row["status"], row["rating"]) if row else ("unseen", None)

    def test_verification_flags_rent_only_titles(self):
        a, b = self.add("movie", 1), self.add("movie", 2)

        class FakeClient:
            def details(self, media_type, tmdb_id):
                # title 1: flatrate via the "with ads" variant of Prime; title 2: only rentable
                return fake_details(media_type, tmdb_id, [2100] if tmdb_id == 1 else [8])

        snapshot.update_details(self.conn, FakeClient(), self.cfg, D0)
        verified = dict(self.conn.execute("SELECT title_id, verified FROM availability").fetchall())
        self.assertEqual(verified, {a: 1, b: 0})
        t = self.conn.execute("SELECT * FROM titles WHERE id = ?", (a,)).fetchone()
        self.assertEqual((t["year"], t["genres"], t["cast_members"], t["directors"], t["keywords"]),
                         (2025, '["Action"]', '["A", "B"]', '["D"]', '["heist"]'))
        offers = {tuple(r) for r in self.conn.execute(
            "SELECT title_id, provider_id, monetization FROM offers")}
        self.assertEqual(offers, {(a, 2100, "flatrate"), (a, 9, "rent"),
                                  (b, 8, "flatrate"), (b, 9, "rent")})
        self.assertIsNotNone(t["offers_fetched_at"])
        self.assertEqual((t["age_rating"], t["age_rating_source"], t["age_rating_raw"]), (12, "us", "PG-13"))

    def test_backfill_only_when_asked(self):
        title_id = self.add("movie", 1)
        with self.conn:  # details from before offers were stored
            self.conn.execute("UPDATE titles SET details_fetched_at = 'x' WHERE id = ?", (title_id,))
            self.conn.execute("UPDATE availability SET verified = 1")

        class FakeClient:
            calls = 0

            def details(self, media_type, tmdb_id):
                FakeClient.calls += 1
                return fake_details(media_type, tmdb_id, [9])

        self.assertEqual(snapshot.update_details(self.conn, FakeClient(), self.cfg, D0), 0)
        self.assertEqual(snapshot.update_details(self.conn, FakeClient(), self.cfg, D0,
                                                 backfill=10), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 2)
        self.assertEqual(snapshot.update_details(self.conn, FakeClient(), self.cfg, D0,
                                                 backfill=10), 0)

    def test_missing_german_data_counts_as_unknown_not_as_contradiction(self):
        today = date.today()
        self.add("movie", 1, first_seen=today.isoformat())

        class NoDataClient:
            def details(self, media_type, tmdb_id):
                d = fake_details(media_type, tmdb_id, [])
                d["watch/providers"] = {"results": {}}  # TMDB knows nothing for DE yet
                return d

        snapshot.update_details(self.conn, NoDataClient(), self.cfg, today)
        verified = self.conn.execute("SELECT verified FROM availability").fetchone()[0]
        self.assertIsNone(verified)  # unknown: the title stays visible

        # ... but not forever: after the grace period it counts as not available
        later = today + timedelta(days=snapshot.UNVERIFIED_GRACE_DAYS + 1)
        snapshot.update_details(self.conn, NoDataClient(), self.cfg, later)
        self.assertEqual(self.conn.execute("SELECT verified FROM availability").fetchone()[0], 0)

    def test_unverified_titles_are_not_refetched_twice_a_day(self):
        self.add("movie", 1, first_seen=date.today().isoformat())  # stays "unknown"

        class CountingClient:
            calls = 0

            def details(self, media_type, tmdb_id):
                CountingClient.calls += 1
                d = fake_details(media_type, tmdb_id, [])
                d["watch/providers"] = {"results": {}}
                return d

        client = CountingClient()
        today = date.today()
        snapshot.update_details(self.conn, client, self.cfg, today)
        snapshot.update_details(self.conn, client, self.cfg, today)
        self.assertEqual(client.calls, 1)
        snapshot.update_details(self.conn, client, self.cfg, today + timedelta(days=1))
        self.assertEqual(client.calls, 2)

    def test_running_series_refreshed_when_tmdb_reports_a_change(self):
        title_id = self.add("tv", 7, service="wow")
        seasons = [{"season_number": 1, "air_date": "2019-03-01"}]

        class FakeClient:
            calls = 0

            def details(self, media_type, tmdb_id):
                FakeClient.calls += 1
                return fake_details(media_type, tmdb_id, [30], seasons)

        client = FakeClient()
        snapshot.update_details(self.conn, client, self.cfg, date.today())
        t = self.conn.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
        self.assertEqual((t["directors"], t["keywords"], t["runtime"], t["year"]),
                         ('["C"]', '["spy"]', 45, 2019))
        self.assertEqual((t["age_rating"], t["age_rating_source"]), (6, "fsk"))
        self.assertEqual(t["imdb_id"], "tt0000002")  # series: only in external_ids
        # same day: nothing to do
        snapshot.update_details(self.conn, client, self.cfg, date.today())
        self.assertEqual(client.calls, 1)
        # next day, but TMDB reports no change for this series: no request
        tomorrow = date.today() + timedelta(days=1)
        snapshot.update_details(self.conn, client, self.cfg, tomorrow, changed_tv_ids={999})
        self.assertEqual(client.calls, 1)
        # TMDB reports the change, season 2 has aired
        seasons.append({"season_number": 2, "air_date": date.today().isoformat()})
        snapshot.update_details(self.conn, client, self.cfg, tomorrow, changed_tv_ids={7})
        self.assertEqual(client.calls, 2)
        events = self.conn.execute("SELECT event, season_number FROM events").fetchall()
        self.assertEqual([tuple(e) for e in events], [("new_season", 2)])


    def test_new_season_makes_a_finished_series_unseen_again(self):
        """Eine abgehakte Serie taucht wieder auf, sobald es weitergeht – ohne dass die
        Bewertung oder die Haken der alten Staffeln verloren gehen."""
        title_id = self.add("tv", 7, service="wow")
        seasons = [{"season_number": 1, "air_date": "2019-03-01"}]

        class FakeClient:
            def details(self, media_type, tmdb_id):
                return fake_details(media_type, tmdb_id, [30], seasons)

        client = FakeClient()
        snapshot.update_details(self.conn, client, self.cfg, date.today())
        queries.set_user_state(self.conn, title_id, {"status": "seen", "rating": 4})
        self.assertEqual(self.user_state(title_id), ("seen", 4))

        seasons.append({"season_number": 2, "air_date": date.today().isoformat()})
        snapshot.update_details(self.conn, client, self.cfg, date.today() + timedelta(days=1),
                                changed_tv_ids={7})

        self.assertEqual(self.user_state(title_id), ("unseen", 4))  # Bewertung bleibt
        marked = [r[0] for r in self.conn.execute(
            "SELECT season_number FROM season_state WHERE title_id = ?", (title_id,))]
        self.assertEqual(marked, [1])  # Staffel 1 bleibt abgehakt, Staffel 2 ist offen

    def test_a_season_that_has_not_aired_yet_leaves_the_series_seen(self):
        title_id = self.add("tv", 7, service="wow")
        seasons = [{"season_number": 1, "air_date": "2019-03-01"}]

        class FakeClient:
            def details(self, media_type, tmdb_id):
                return fake_details(media_type, tmdb_id, [30], seasons)

        client = FakeClient()
        snapshot.update_details(self.conn, client, self.cfg, date.today())
        queries.set_user_state(self.conn, title_id, {"status": "seen"})

        seasons.append({"season_number": 2, "air_date": "2099-01-01"})  # angekündigt, nicht gelaufen
        snapshot.update_details(self.conn, client, self.cfg, date.today() + timedelta(days=1),
                                changed_tv_ids={7})
        self.assertEqual(self.user_state(title_id)[0], "seen")

    def test_series_are_refreshed_at_least_weekly(self):
        self.add("tv", 7, service="wow")

        class FakeClient:
            calls = 0

            def details(self, media_type, tmdb_id):
                FakeClient.calls += 1
                return fake_details(media_type, tmdb_id, [30], [{"season_number": 1, "air_date": "2019-03-01"}])

        client = FakeClient()
        snapshot.update_details(self.conn, client, self.cfg, date.today())
        # without a reported change nothing happens for days ...
        snapshot.update_details(self.conn, client, self.cfg, date.today() + timedelta(days=3),
                                changed_tv_ids=set())
        self.assertEqual(client.calls, 1)
        # ... until the safety net kicks in
        snapshot.update_details(self.conn, client, self.cfg,
                                date.today() + timedelta(days=self.cfg.series_refresh_days + 1),
                                changed_tv_ids=set())
        self.assertEqual(client.calls, 2)


class ChangedSeriesTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db(self)
        self.cfg = Config(services=SERVICES)

    def test_asks_tmdb_only_for_the_days_since_the_last_check(self):
        asked = []

        class FakeClient:
            def changed_ids(self, media_type, start, end, max_pages=60):
                asked.append((media_type, start, end))
                return {1, 2}

        today = date(2026, 9, 20)
        self.assertEqual(snapshot.changed_series(self.conn, FakeClient(), self.cfg, today), {1, 2})
        self.assertEqual(asked[-1], ("tv", date(2026, 9, 19), today))  # first run: yesterday
        snapshot.changed_series(self.conn, FakeClient(), self.cfg, date(2026, 9, 25))
        self.assertEqual(asked[-1][1], today)  # continues where it stopped
        # after a long pause TMDB only serves 14 days
        snapshot.changed_series(self.conn, FakeClient(), self.cfg, date(2026, 12, 1))
        self.assertEqual(asked[-1][1], date(2026, 11, 18))

    def test_falls_back_to_checking_every_series(self):
        class BrokenClient:
            def changed_ids(self, *args, **kwargs):
                raise TMDBError("kaputt")

        self.assertIsNone(snapshot.changed_series(self.conn, BrokenClient(), self.cfg, date.today()))


class SubscriptionTest(unittest.TestCase):
    def test_initial_subscriptions_seeded_only_once(self):
        conn = make_db(self)  # seeds prime
        with conn:
            catalog.set_subscribed(conn, "prime", False)
            catalog.sync_services(conn, Config(services=SERVICES, initial_subscriptions=["prime"]))
        subscribed = [r[0] for r in conn.execute("SELECT key FROM services WHERE subscribed = 1")]
        self.assertEqual(subscribed, [])


class MigrationTest(unittest.TestCase):
    def test_v2_database_is_migrated_with_backup(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "m.db"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (2);
            CREATE TABLE titles (id INTEGER PRIMARY KEY, media_type TEXT NOT NULL,
                tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, popularity REAL,
                details_fetched_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (media_type, tmdb_id));
            INSERT INTO titles (media_type, tmdb_id, title, created_at, updated_at)
                VALUES ('movie', 1, 'Alt', 'x', 'x');
            CREATE TABLE services (key TEXT PRIMARY KEY, name TEXT NOT NULL,
                provider_ids TEXT NOT NULL, tracked INTEGER NOT NULL DEFAULT 1,
                subscribed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            INSERT INTO services (key, name, provider_ids, subscribed, updated_at)
                VALUES ('prime', 'Prime Video', '[9]', 1, 'x');
        """)
        old.close()
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0],
                         db.SCHEMA_VERSION)
        row = conn.execute("SELECT title, offers_fetched_at FROM titles").fetchone()
        self.assertEqual(tuple(row), ("Alt", None))
        conn.execute("SELECT * FROM offers")
        service = conn.execute("SELECT subscribed, free FROM services").fetchone()
        self.assertEqual(tuple(service), (1, 0))
        self.assertTrue((tmp / "m.db.bak-v2").exists())

    def test_v6_dynamic_lists_become_saved_filters(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "m6.db"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (6);
            CREATE TABLE lists (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'manual', filters TEXT,
                is_default INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE list_items (list_id INTEGER NOT NULL, title_id INTEGER NOT NULL,
                added_at TEXT NOT NULL, PRIMARY KEY (list_id, title_id));
            INSERT INTO lists (name, kind, is_default, created_at, updated_at)
                VALUES ('Merkliste', 'manual', 1, 'x', 'x');
            INSERT INTO lists (name, kind, filters, created_at, updated_at)
                VALUES ('Kinderabend', 'dynamic', '{"genre": ["Animation"]}', 'x', 'x');
        """)
        old.close()
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual([tuple(r) for r in conn.execute("SELECT name, is_default FROM lists")],
                         [("Merkliste", 1)])
        self.assertEqual([tuple(r) for r in conn.execute("SELECT name, filters FROM saved_filters")],
                         [("Kinderabend", '{"genre": ["Animation"]}')])

    def test_free_service_cannot_be_subscribed(self):
        conn = connect(":memory:")
        self.addCleanup(conn.close)
        with conn:
            catalog.sync_services(conn, Config(services=[Service("ard", "ARD", [219], free=True)]))
        with self.assertRaises(ValueError):
            catalog.set_subscribed(conn, "ard", True)


if __name__ == "__main__":
    unittest.main()
