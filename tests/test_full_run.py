"""The snapshot from start to finish, day after day, against a pretend TMDB."""

import unittest
from datetime import date, timedelta

from moviebot import queries, snapshot
from moviebot.config import Config, Service
from moviebot.db import connect

from .fake_tmdb import MiniTmdb

SERVICES = [Service("prime", "Prime Video", [9, 2100]), Service("wow", "WOW", [30])]


def day(n: int) -> date:
    # counted from the real today: "fetched today already?" compares with the clock
    return date.today() + timedelta(days=n)


class FullRunTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        self.cfg = Config(services=SERVICES, initial_subscriptions=["prime"])
        self.tmdb = MiniTmdb()
        # enough films that one leaving does not look like a broken response
        for i, name in enumerate(["Film eins", "Film vier", "Film fünf", "Film sechs", "Film sieben"]):
            self.tmdb.movie(10 + i, name, [9])
        self.tmdb.series(2, "Serie zwei", [30], {1: "2025-03-01"})

    def run_day(self, n: int, **kwargs) -> dict:
        """One complete run; returns what the settings page would report about it."""
        since = queries.last_event_id(self.conn)
        results = snapshot.run(self.conn, self.tmdb, self.cfg, today=day(n), **kwargs)
        return queries.run_summary(self.conn, results, since)

    def counts(self, summary: dict) -> tuple:
        return (summary["added"], summary["readded"], summary["new_seasons"], summary["removed"])

    def title(self, name: str):
        return self.conn.execute("SELECT * FROM titles WHERE title = ?", (name,)).fetchone()

    def availability(self, name: str, service: str):
        return self.conn.execute(
            "SELECT a.* FROM availability a JOIN titles t ON t.id = a.title_id "
            "WHERE t.title = ? AND a.service = ?", (name, service)).fetchone()

    def test_a_week_of_runs(self):
        # day 0: the first run stores everything and announces nothing
        self.assertEqual(self.counts(self.run_day(0)), (0, 0, 0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0], 6)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        statuses = {r[0] for r in self.conn.execute("SELECT status FROM snapshot_runs")}
        self.assertEqual(statuses, {"baseline"})

        # day 1: a new film at Prime, complete with details and confirmed availability
        self.tmdb.movie(3, "Film drei", [2100])  # the "with ads" variant counts as Prime
        self.assertEqual(self.counts(self.run_day(1)), (1, 0, 0, 0))
        film = self.title("Film drei")
        self.assertEqual((film["genres"], film["directors"]), ('["Action"]', '["D"]'))
        self.assertIsNotNone(film["offers_fetched_at"])
        a = self.availability("Film drei", "prime")
        self.assertEqual((a["first_seen"], a["verified"], a["in_baseline"]), (day(1).isoformat(), 1, 0))

        # day 2: the series gets a second season, and TMDB reports the series as changed
        self.tmdb.series(2, "Serie zwei", [30], {1: "2025-03-01", 2: day(2).isoformat()})
        self.tmdb.changed_series = {2}
        self.assertEqual(self.counts(self.run_day(2)), (0, 0, 1, 0))
        self.assertEqual(self.title("Serie zwei")["number_of_seasons"], 2)
        self.tmdb.changed_series = set()

        # day 3: the film is missing – might be a flicker, so nothing is reported yet
        self.tmdb.offer("movie", 3, [])
        self.assertEqual(self.counts(self.run_day(3)), (0, 0, 0, 0))
        self.assertIsNone(self.availability("Film drei", "prime")["removed_at"])

        # day 4: still missing – now it counts as gone
        self.assertEqual(self.counts(self.run_day(4)), (0, 0, 0, 1))
        self.assertEqual(self.availability("Film drei", "prime")["removed_at"], day(4).isoformat())
        found = queries.search_titles(self.conn, queries.TitleFilter(services=["all"]), today=day(4))
        self.assertNotIn("Film drei", [i["title"] for i in found["items"]])

        # day 5: it is back; the day it first appeared stays, the availability is confirmed anew
        self.tmdb.offer("movie", 3, [9])
        self.assertEqual(self.counts(self.run_day(5)), (0, 1, 0, 0))
        a = self.availability("Film drei", "prime")
        self.assertEqual((a["first_seen"], a["removed_at"], a["verified"]), (day(1).isoformat(), None, 1))

        # the "Neu" page tells the same story, newest day first
        story = [
            (d["date"], kind, [(t["title"], t["services"], t["seasons"]) for t in d[kind]])
            for d in queries.recent_changes(self.conn, days=7, today=day(5))
            for kind in ("added", "readded", "new_seasons", "removed") if d[kind]
        ]
        self.assertEqual(story, [
            (day(5).isoformat(), "readded", [("Film drei", ["Prime Video"], [])]),
            (day(4).isoformat(), "removed", [("Film drei", ["Prime Video"], [])]),
            (day(2).isoformat(), "new_seasons", [("Serie zwei", [], [2])]),
            (day(1).isoformat(), "added", [("Film drei", ["Prime Video"], [])]),
        ])

    def test_a_second_run_on_the_same_day_changes_nothing(self):
        self.run_day(0)
        self.tmdb.movie(3, "Film drei", [9])
        self.assertEqual(self.counts(self.run_day(1)), (1, 0, 0, 0))
        requests = len(self.tmdb.calls)
        self.assertEqual(self.counts(self.run_day(1)), (0, 0, 0, 0))
        details = [c for c in self.tmdb.calls[requests:] if c.startswith(("/movie/", "/tv/"))]
        self.assertEqual(details, ["/tv/changes"])  # no title is fetched twice a day

    def test_one_service_failing_does_not_stop_the_others(self):
        self.run_day(0)
        self.tmdb.broken_providers = {30}  # WOW's catalog cannot be loaded
        self.tmdb.movie(3, "Film drei", [9])
        with self.assertLogs("moviebot.snapshot", level="ERROR"):
            summary = self.run_day(1)
        self.assertEqual(self.counts(summary), (1, 0, 0, 0))  # Prime was compared as usual
        self.assertEqual(summary["failed"], ["wow/movie", "wow/tv"])
        failed = self.conn.execute(
            "SELECT service, message FROM snapshot_runs WHERE status = 'failed'").fetchall()
        self.assertEqual({r["service"] for r in failed}, {"wow"})
        self.assertIn("HTTP 500", failed[0]["message"])
        self.assertEqual(len(queries.status(self.conn)["failed_since_last_snapshot"]), 2)

        # however long the outage lasts: an unreadable catalog is not an empty one
        with self.assertLogs("moviebot.snapshot", level="ERROR"):
            self.assertEqual(self.counts(self.run_day(2)), (0, 0, 0, 0))
            self.assertEqual(self.counts(self.run_day(3)), (0, 0, 0, 0))
        a = self.availability("Serie zwei", "wow")
        self.assertEqual((a["removed_at"], a["missed_runs"]), (None, 0))

        # back to normal, and the failed runs did not turn this one into a silent first run
        self.tmdb.broken_providers = set()
        self.tmdb.series(4, "Serie vier", [30], {1: "2026-01-10"})
        self.assertEqual(self.counts(self.run_day(4)), (1, 0, 0, 0))

    def test_trouble_with_single_titles_does_not_stop_the_run(self):
        self.tmdb.broken_details = {("movie", 11)}
        self.tmdb.deleted = {("movie", 12)}
        with self.assertLogs("moviebot.snapshot", level="WARNING") as logs:
            self.run_day(0)
        self.assertEqual(len(logs.records), 2)
        fetched = {r["title"]: r["details_fetched_at"] is not None
                   for r in self.conn.execute("SELECT title, details_fetched_at FROM titles")}
        self.assertEqual(fetched, {"Film eins": True, "Film vier": False, "Film fünf": False,
                                   "Film sechs": True, "Film sieben": True, "Serie zwei": True})
        # the next run tries again
        self.tmdb.broken_details = set()
        with self.assertLogs("moviebot.snapshot", level="WARNING"):  # film 12 is still gone
            self.run_day(1)
        self.assertIsNotNone(self.title("Film vier")["details_fetched_at"])
        self.assertIsNone(self.title("Film fünf")["details_fetched_at"])

    def test_only_the_services_asked_for_and_without_details(self):
        results = snapshot.run(self.conn, self.tmdb, self.cfg, today=day(0),
                               service_keys=["wow"], fetch_details=False)
        self.assertEqual(sorted(results), [("wow", "movie"), ("wow", "tv")])
        titles = self.conn.execute("SELECT title, details_fetched_at FROM titles").fetchall()
        self.assertEqual([tuple(t) for t in titles], [("Serie zwei", None)])
        with self.assertRaises(ValueError):
            snapshot.run(self.conn, self.tmdb, self.cfg, service_keys=["nope"])
        with self.assertRaises(ValueError):
            snapshot.run(self.conn, self.tmdb, Config())  # no services configured at all

    def test_progress_covers_catalogs_and_details(self):
        steps: list[dict] = []
        self.run_day(0, progress=steps.append)
        self.assertEqual([s["phase"] for s in steps if not s["done"]], ["catalogs", "details"])
        labels = [s["label"] for s in steps if s["phase"] == "catalogs"]
        self.assertEqual(labels, ["Prime Video – Filme", "WOW – Filme", "Prime Video – Serien",
                                  "WOW – Serien", "Kataloge"])
        for phase in ("catalogs", "details"):
            last = [s for s in steps if s["phase"] == phase][-1]
            self.assertEqual(last["done"], last["total"])
        self.assertEqual(steps[-1]["total"], 6)  # every title needs details on the first run

    def test_a_catalog_larger_than_one_page(self):
        for i in range(100, 145):
            self.tmdb.movie(i, f"Film {i}", [9])
        self.run_day(0)
        count = self.conn.execute(
            "SELECT title_count FROM snapshot_runs WHERE service = 'prime' AND media_type = 'movie'"
        ).fetchone()[0]
        self.assertEqual(count, 50)
