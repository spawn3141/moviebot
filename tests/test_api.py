import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from moviebot import catalog
from moviebot.api import create_app
from moviebot.config import Config, Service
from moviebot.db import connect

SERVICES = [
    Service("prime", "Prime Video", [9]),
    Service("wow", "WOW", [30]),
    Service("netflix", "Netflix", [8]),
    Service("ard", "ARD Mediathek", [219], free=True),
]
TODAY = date.today()
DAYS_AGO_2 = (TODAY - timedelta(days=2)).isoformat()


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(services=SERVICES, initial_subscriptions=["prime", "wow"],
                          db_path=Path(tempfile.mkdtemp()) / "api.db")
        conn = connect(self.cfg.db_path)
        with conn:
            catalog.sync_services(conn, self.cfg)
            conn.execute("INSERT INTO providers (provider_id, name, updated_at) VALUES (9, 'Amazon Prime Video', 'x')")
            self.ids = {}

            def title(key, media_type, name, genres, service, *, year=2025, votes=(7.0, 100),
                      verified=1, removed=None, baseline=1, first_seen="2026-09-19", popularity=1.0):
                tid = conn.execute(
                    """INSERT INTO titles (media_type, tmdb_id, title, year, release_date, genres,
                           vote_average, vote_count, popularity, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'x', 'x') RETURNING id""",
                    (media_type, len(self.ids) + 1, name, year, f"{year}-01-01",
                     str(genres).replace("'", '"'), *votes, popularity),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO availability (title_id, service, first_seen, last_seen,
                           removed_at, in_baseline, verified) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tid, service, first_seen, first_seen, removed, baseline, verified),
                )
                self.ids[key] = tid

            title("action", "movie", "Action A", ["Action"], "prime", votes=(7.5, 5000), popularity=50)
            title("series", "tv", "Serie B", ["Action & Adventure", "Drama"], "wow",
                  baseline=0, first_seen=DAYS_AGO_2, year=2019, popularity=40)
            title("netflix", "movie", "Netflix C", ["Komödie"], "netflix", popularity=30)
            title("free", "movie", "Frei D", ["Dokumentarfilm"], "ard", popularity=20)
            title("unverified", "movie", "Nur Leihen E", ["Action"], "prime", verified=0)
            title("removed", "movie", "Weg F", ["Action"], "prime", removed="2026-09-10")
            title("lucky", "movie", "Zufall G", ["Drama"], "prime", votes=(9.5, 3), popularity=10)
            conn.execute("INSERT INTO offers (title_id, provider_id, monetization, updated_at) "
                         "VALUES (?, 9, 'flatrate', 'x')", (self.ids["action"],))
            conn.execute("INSERT INTO events (title_id, service, event, event_date) VALUES (?, 'wow', 'added', ?)",
                         (self.ids["series"], DAYS_AGO_2))
            conn.execute("INSERT INTO events (title_id, event, season_number, event_date) "
                         "VALUES (?, 'new_season', 3, ?)", (self.ids["series"], TODAY.isoformat()))
        conn.close()
        self.client = TestClient(create_app(self.cfg))

    def titles(self, **params) -> list[str]:
        r = self.client.get("/api/titles", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return [i["title"] for i in r.json()["items"]]

    def test_default_is_my_subscriptions_verified_and_current(self):
        self.assertEqual(self.titles(), ["Action A", "Serie B", "Zufall G"])

    def test_genre_filter_uses_unified_names(self):
        self.assertEqual(self.titles(genre="Action"), ["Action A", "Serie B"])
        self.assertEqual(self.titles(genre="Abenteuer"), ["Serie B"])

    def test_filters(self):
        self.assertEqual(self.titles(media_type="tv"), ["Serie B"])
        self.assertEqual(self.titles(year_from=2020, year_to=2025), ["Action A", "Zufall G"])
        self.assertEqual(self.titles(q="serie"), ["Serie B"])
        self.assertEqual(self.titles(services="netflix"), ["Netflix C"])
        self.assertEqual(self.titles(include_free=True), ["Action A", "Serie B", "Frei D", "Zufall G"])
        self.assertEqual(self.titles(new_days=7), ["Serie B"])

    def test_rating_sort_is_weighted_by_vote_count(self):
        self.assertEqual(self.titles(sort="rating"), ["Action A", "Serie B", "Zufall G"])
        self.assertEqual(self.titles(sort="added")[0], "Serie B")

    def test_invalid_input(self):
        self.assertEqual(self.client.get("/api/titles", params={"sort": "x"}).status_code, 422)
        self.assertEqual(self.client.get("/api/titles", params={"services": "nope"}).status_code, 400)

    def test_rating_marks_seen_and_hides_title(self):
        tid = self.ids["action"]
        r = self.client.put(f"/api/titles/{tid}/state", json={"rating": 4})
        self.assertEqual(r.json(), {"status": "seen", "rating": 4})
        self.assertNotIn("Action A", self.titles())
        self.assertIn("Action A", self.titles(show_seen=True))
        r = self.client.put(f"/api/titles/{tid}/state", json={"status": "unseen"})
        self.assertEqual(r.json(), {"status": "unseen", "rating": None})
        r = self.client.put(f"/api/titles/{tid}/state", json={"rating": 9})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.put("/api/titles/99999/state", json={"status": "seen"}).status_code, 404)

    def test_not_interested_is_hidden(self):
        self.client.put(f"/api/titles/{self.ids['lucky']}/state", json={"status": "not_interested"})
        self.assertNotIn("Zufall G", self.titles())

    def test_detail(self):
        r = self.client.get(f"/api/titles/{self.ids['action']}")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["offers"], [{"provider_id": 9, "provider": "Amazon Prime Video",
                                        "monetization": "flatrate", "service": "prime"}])
        self.assertEqual([a["service"] for a in d["available_on"]], ["prime"])
        self.assertIsNone(d["available_on"][0]["since"])  # baseline
        self.assertEqual(self.client.get("/api/titles/99999").status_code, 404)

    def test_subscriptions_and_settings(self):
        r = self.client.put("/api/services/netflix", json={"subscribed": True})
        self.assertTrue(r.json()["subscribed"])
        self.assertIn("Netflix C", self.titles())
        self.assertEqual(self.client.put("/api/services/ard", json={"subscribed": True}).status_code, 400)
        self.assertEqual(self.client.put("/api/services/nope", json={"subscribed": True}).status_code, 404)
        self.client.put("/api/settings", json={"include_free": True})
        self.assertIn("Frei D", self.titles())
        services = {s["key"]: s for s in self.client.get("/api/services").json()}
        self.assertEqual((services["prime"]["movies"], services["wow"]["series"]), (2, 1))

    def test_new_feed(self):
        events = self.client.get("/api/new", params={"days": 7}).json()
        self.assertEqual([(e["event"], e["title"]["title"]) for e in events],
                         [("new_season", "Serie B"), ("added", "Serie B")])

    def test_genres_and_status(self):
        names = [g["name"] for g in self.client.get("/api/genres").json()]
        self.assertIn("Action", names)
        self.assertNotIn("Action & Adventure", names)
        self.assertEqual(self.client.get("/api/status").json()["titles"], 7)


if __name__ == "__main__":
    unittest.main()
