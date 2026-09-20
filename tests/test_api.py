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


class FakeTmdb:
    """Stands in for TMDB when searching and importing by hand."""

    MATRIX = {"id": 603, "media_type": "movie", "title": "Matrix", "release_date": "1999-03-30",
              "overview": "Neo …", "poster_path": "/m.jpg", "genres": [{"name": "Action"}],
              "credits": {"cast": [], "crew": []}, "keywords": {"keywords": []},
              "watch/providers": {"results": {"DE": {"flatrate": [{"provider_id": 9}],
                                                     "rent": [{"provider_id": 8}]}}}}

    def search(self, query, limit=20):
        return [self.MATRIX, {"id": 2, "media_type": "tv", "name": "Serie B",
                              "first_air_date": "2019-01-01"}]

    def details(self, media_type, tmdb_id):
        return dict(self.MATRIX)


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
            conn.execute("UPDATE titles SET age_rating = 6, age_rating_source = 'fsk', age_rating_raw = '6' "
                         "WHERE id = ?", (self.ids["series"],))
            conn.execute("UPDATE titles SET age_rating = 16, age_rating_source = 'us', age_rating_raw = 'R' "
                         "WHERE id = ?", (self.ids["action"],))
            conn.execute("INSERT INTO offers (title_id, provider_id, monetization, updated_at) "
                         "VALUES (?, 9, 'flatrate', 'x')", (self.ids["action"],))
            conn.executemany(
                "INSERT INTO seasons (title_id, season_number, air_date) VALUES (?, ?, ?)",
                [(self.ids["series"], 1, "2019-03-01"), (self.ids["series"], 2, DAYS_AGO_2),
                 (self.ids["series"], 3, "2099-01-01")])  # season 3 not aired yet
            conn.execute("INSERT INTO events (title_id, service, event, event_date) VALUES (?, 'wow', 'added', ?)",
                         (self.ids["series"], DAYS_AGO_2))
            conn.execute("INSERT INTO events (title_id, event, season_number, event_date) "
                         "VALUES (?, 'new_season', 3, ?)", (self.ids["series"], TODAY.isoformat()))
        conn.close()
        self.client = TestClient(create_app(self.cfg, client_factory=lambda cfg: FakeTmdb()))

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

    def test_newest_sort_uses_latest_season_for_series(self):
        # default: the series' newest season (2 days ago) beats the 2025 movies
        self.assertEqual(self.titles(sort="newest")[0], "Serie B")
        r = self.client.put("/api/settings", json={"series_newest": "first"})
        self.assertEqual(r.json()["series_newest"], "first")
        # now the series counts with its 2019 start and drops behind the movies
        self.assertEqual(self.titles(sort="newest")[-1], "Serie B")
        self.assertEqual(self.client.put("/api/settings", json={"series_newest": "x"}).status_code, 422)

    def test_age_filter(self):
        self.assertEqual(self.titles(max_age=12), ["Serie B"])
        self.assertEqual(self.titles(max_age=16), ["Action A", "Serie B"])
        self.assertEqual(self.titles(max_age=6, include_unrated=True), ["Serie B", "Zufall G"])
        item = self.client.get("/api/titles", params={"max_age": 16}).json()["items"][0]
        self.assertEqual((item["age_rating"], item["age_rating_source"], item["age_rating_raw"]),
                         (16, "us", "R"))

    def test_my_services_can_be_stored_in_a_filter(self):
        # "mine" behaves like an empty selection, but can be saved explicitly
        self.assertEqual(self.titles(services="mine"), self.titles())
        created = self.client.post("/api/filters", json={
            "name": "Meine Action", "filters": {"services": ["mine"], "genre": ["Action"]}}).json()
        self.assertEqual(created["count"], 2)  # Action A (prime) and Serie B (wow), not Netflix C
        self.assertEqual([i["title"] for i in
                          self.client.get("/api/titles", params={"filter_id": created["id"]}).json()["items"]],
                         ["Action A", "Serie B"])

    def test_all_services(self):
        titles = self.titles(services="all")
        self.assertEqual(titles, ["Action A", "Serie B", "Netflix C", "Frei D", "Zufall G"])
        # also works as a stored filter of a dynamic list
        created = self.client.post("/api/filters", json={
            "name": "Überall", "filters": {"services": ["all"], "genre": ["Action"]}}).json()
        self.assertEqual(created["count"], 2)

    def test_rating_sort_is_weighted_by_vote_count(self):
        self.assertEqual(self.titles(sort="rating"), ["Action A", "Serie B", "Zufall G"])
        self.assertEqual(self.titles(sort="added")[0], "Serie B")

    def test_added_sort_counts_new_seasons(self):
        # "Action A" came with the baseline, so the series added two days ago comes first ...
        self.assertEqual(self.titles(sort="added")[0], "Serie B")
        # ... until it gets a new season today (fixture has one for the series, add one here)
        conn = connect(self.cfg.db_path)
        self.addCleanup(conn.close)
        with conn:
            conn.execute("INSERT INTO events (title_id, event, season_number, event_date) "
                         "VALUES (?, 'new_season', 2, ?)", (self.ids["action"], TODAY.isoformat()))
        self.assertEqual(self.titles(sort="added")[0], "Action A")

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
        self.assertIsNone(d["imdb_url"])
        self.assertEqual([a["service"] for a in d["available_on"]], ["prime"])
        self.assertEqual(d["available_on"][0]["since"], "2026-09-19")
        self.assertTrue(d["available_on"][0]["baseline"])
        series = self.client.get(f"/api/titles/{self.ids['series']}").json()
        self.assertEqual(series["available_on"][0]["since"], DAYS_AGO_2)
        self.assertFalse(series["available_on"][0]["baseline"])
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

    def test_discover_filters_are_remembered(self):
        self.assertIsNone(self.client.get("/api/settings").json()["discover_filters"])
        filters = {"genre": ["Action"], "sort": "rating", "media_type": "tv"}
        r = self.client.put("/api/settings", json={"discover_filters": filters})
        self.assertEqual(r.json()["discover_filters"], filters)
        self.assertEqual(self.client.get("/api/settings").json()["discover_filters"], filters)
        # unrelated settings stay untouched
        self.client.put("/api/settings", json={"include_free": True})
        self.assertEqual(self.client.get("/api/settings").json()["discover_filters"], filters)
        big = {"q": "x" * 5000}
        self.assertEqual(self.client.put("/api/settings", json={"discover_filters": big}).status_code, 400)

    def test_default_list_exists(self):
        lists = self.client.get("/api/lists").json()
        self.assertEqual([(l["name"], l["is_default"], l["count"]) for l in lists],
                         [("Merkliste", True, 0)])

    def test_add_and_remove_titles(self):
        merkliste = self.client.get("/api/lists").json()[0]["id"]
        tid = self.ids["action"]
        r = self.client.put(f"/api/titles/{tid}/lists", json={"list_ids": [merkliste]})
        self.assertEqual(r.json(), [merkliste])
        self.assertEqual(self.client.get("/api/lists").json()[0]["count"], 1)
        item = self.client.get("/api/titles", params={"list_id": merkliste}).json()["items"][0]
        self.assertEqual((item["title"], item["in_lists"]), ("Action A", [merkliste]))
        self.client.put(f"/api/titles/{tid}/lists", json={"list_ids": []})
        self.assertEqual(self.client.get("/api/titles", params={"list_id": merkliste}).json()["total"], 0)
        self.assertEqual(self.client.put("/api/titles/99999/lists", json={"list_ids": []}).status_code, 404)
        self.assertEqual(self.client.put(f"/api/titles/{tid}/lists", json={"list_ids": [999]}).status_code, 400)

    def test_list_shows_titles_that_are_not_available(self):
        merkliste = self.client.get("/api/lists").json()[0]["id"]
        gone = self.ids["removed"]  # no longer in any service
        self.client.put(f"/api/titles/{gone}/lists", json={"list_ids": [merkliste]})
        items = self.client.get("/api/titles", params={"list_id": merkliste}).json()["items"]
        self.assertEqual([(i["title"], i["available_on"]) for i in items], [("Weg F", [])])
        # ... unless only available ones are wanted
        self.assertEqual(self.client.get("/api/titles", params={"list_id": merkliste, "only_available": True})
                         .json()["total"], 0)

    def test_create_rename_default_and_delete(self):
        merkliste = self.client.get("/api/lists").json()[0]["id"]
        created = self.client.post("/api/lists", json={"name": "  Heute Abend  "})
        self.assertEqual(created.status_code, 201)
        new_id = created.json()["id"]
        self.assertEqual(created.json()["name"], "Heute Abend")
        self.assertEqual(self.client.post("/api/lists", json={"name": " "}).status_code, 400)
        renamed = self.client.patch(f"/api/lists/{new_id}", json={"name": "Mit Anna"})
        self.assertEqual(renamed.json()["name"], "Mit Anna")
        # the default list cannot be deleted, but the default can move
        self.assertEqual(self.client.delete(f"/api/lists/{merkliste}").status_code, 400)
        self.client.patch(f"/api/lists/{new_id}", json={"is_default": True})
        defaults = [l["id"] for l in self.client.get("/api/lists").json() if l["is_default"]]
        self.assertEqual(defaults, [new_id])
        self.assertEqual(self.client.delete(f"/api/lists/{merkliste}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/lists/{merkliste}").status_code, 404)

    def test_list_sorting_by_added(self):
        merkliste = self.client.get("/api/lists").json()[0]["id"]
        for key in ("lucky", "action"):
            self.client.put(f"/api/titles/{self.ids[key]}/lists", json={"list_ids": [merkliste]})
        titles = self.titles(list_id=merkliste, sort="list_added")
        self.assertEqual(titles, ["Action A", "Zufall G"])  # most recently added first
        self.assertEqual(self.client.get("/api/titles", params={"sort": "list_added"}).status_code, 400)

    def test_saved_filter_is_applied(self):
        r = self.client.post("/api/filters", json={
            "name": "Kinderabend", "filters": {"genre": ["Action"], "max_age": 12, "sort": "rating"}})
        self.assertEqual(r.status_code, 201)
        dynamic = r.json()
        self.assertEqual((dynamic["count"], dynamic["error"]), (1, None))
        items = self.client.get("/api/titles", params={"filter_id": dynamic["id"]}).json()
        self.assertEqual([i["title"] for i in items["items"]], ["Serie B"])
        # the saved filter wins ...
        page = self.client.get("/api/titles", params={"filter_id": dynamic["id"], "genre": "Komödie"}).json()
        self.assertEqual([i["title"] for i in page["items"]], ["Serie B"])
        # ... but filters it does not define narrow it further
        page = self.client.get("/api/titles", params={"filter_id": dynamic["id"], "media_type": "movie"}).json()
        self.assertEqual(page["items"], [])
        # it follows the data: rating the series as seen hides it like everywhere else
        self.client.put(f"/api/titles/{self.ids['series']}/state", json={"status": "seen"})
        self.assertEqual(self.client.get("/api/titles", params={"filter_id": dynamic["id"]}).json()["total"], 0)

    def test_filter_values_from_the_web_ui_are_converted(self):
        # the UI sends numbers as strings and booleans as "true"
        created = self.client.post("/api/filters", json={
            "name": "Ab 2020",
            "filters": {"year_from": "2020", "max_age": "16", "include_unrated": "true"}}).json()
        self.assertEqual(created["filters"],
                         {"year_from": 2020, "max_age": 16, "include_unrated": True})
        titles = self.client.get("/api/titles", params={"filter_id": created["id"]}).json()
        self.assertEqual([i["title"] for i in titles["items"]], ["Action A", "Zufall G"])
        self.assertEqual(self.client.post("/api/filters", json={
            "name": "Kaputt", "filters": {"year_from": "zwanzig"}}).status_code, 400)
        self.assertEqual(self.client.post("/api/filters", json={
            "name": "Kaputt", "filters": {"genre": "Action"}}).status_code, 400)

    def test_default_filter_is_offered_once_and_can_be_deleted(self):
        filters = self.client.get("/api/filters").json()
        self.assertEqual([(f["name"], f["filters"]) for f in filters],
                         [("Neu", {"new_days": 14, "services": ["mine"], "sort": "added"})])
        self.assertEqual(self.client.delete(f"/api/filters/{filters[0]['id']}").status_code, 204)
        # a restart must not bring it back
        conn = connect(self.cfg.db_path)
        self.addCleanup(conn.close)
        catalog.bootstrap(conn, self.cfg)
        self.assertEqual(self.client.get("/api/filters").json(), [])

    def test_saved_filter_rules(self):
        self.assertEqual(self.client.post("/api/filters", json={
            "name": "Leer", "filters": {}}).status_code, 400)
        self.assertEqual(self.client.post("/api/filters", json={
            "name": "Quatsch", "filters": {"foo": 1}}).status_code, 400)
        self.client.delete("/api/filters/1")  # the ready-made "Neu" filter
        saved = self.client.post("/api/filters", json={
            "name": "Action", "filters": {"genre": ["Action"]}}).json()
        renamed = self.client.patch(f"/api/filters/{saved['id']}", json={"name": "Action pur"})
        self.assertEqual(renamed.json()["name"], "Action pur")
        # a filter that no longer works shows up as an error instead of breaking the page
        self.client.patch(f"/api/filters/{saved['id']}", json={"filters": {"services": ["weg"]}})
        entry = [f for f in self.client.get("/api/filters").json() if f["id"] == saved["id"]][0]
        self.assertIn("weg", entry["error"])
        self.assertEqual(self.client.get("/api/titles", params={"filter_id": saved["id"]}).status_code, 400)
        self.assertEqual(self.client.get("/api/titles", params={"filter_id": 999}).status_code, 404)
        self.assertEqual(self.client.get("/api/titles", params={"list_id": 999}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/filters/{saved['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/filters").json(), [])

    def test_new_filter_explains_why_a_title_is_new(self):
        items = self.client.get("/api/titles", params={"new_days": 7}).json()["items"]
        self.assertEqual([(i["title"], i["recent"]["event"], i["recent"]["season_number"])
                          for i in items], [("Serie B", "new_season", 3)])
        # without the filter there is nothing to explain
        self.assertIsNone(self.client.get("/api/titles").json()["items"][0]["recent"])

    def test_run_summary_counts_only_new_events(self):
        from moviebot import queries
        from moviebot.db import connect

        conn = connect(self.cfg.db_path)
        self.addCleanup(conn.close)
        since = queries.last_event_id(conn)  # the fixture events are older
        with conn:
            for event, season in (("added", None), ("added", None), ("readded", None),
                                  ("removed", None), ("new_season", 4)):
                conn.execute("INSERT INTO events (title_id, service, event, season_number, event_date) "
                             "VALUES (?, 'prime', ?, ?, '2026-09-20')",
                             (self.ids["action"], event, season))
        results = {("prime", "movie"): object(), ("wow", "tv"): None}  # one service failed
        summary = queries.run_summary(conn, results, since)
        self.assertEqual({k: v for k, v in summary.items() if k != "finished_at"},
                         {"added": 2, "readded": 1, "removed": 1, "new_seasons": 1, "failed": ["wow/tv"]})
        self.assertIsNotNone(summary["finished_at"])
        # a second run right after reports nothing
        self.assertEqual(queries.run_summary(conn, {}, queries.last_event_id(conn))["added"], 0)
        # the summary survives a restart, because it is stored
        queries.store_run_summary(conn, summary)
        self.assertEqual(queries.get_run_summary(conn)["added"], 2)
        self.assertEqual(self.client.get("/api/status").json()["schedule"], None)  # no scheduler here

    def test_search_and_import_by_hand(self):
        items = self.client.get("/api/search", params={"q": "Matrix"}).json()
        self.assertEqual([(i["title"], i["year"], i["title_id"]) for i in items],
                         [("Matrix", 1999, None), ("Serie B", 2019, self.ids["series"])])

        created = self.client.post("/api/titles/import",
                                   json={"media_type": "movie", "tmdb_id": 603})
        self.assertEqual(created.status_code, 201)
        title = created.json()
        self.assertEqual((title["title"], title["manual"]), ("Matrix", True))
        # availability comes from the title's own offers, not from a catalog
        self.assertEqual([a["service"] for a in title["available_on"]], ["prime"])
        # ... and the first import is silent: it is not a new arrival
        self.assertEqual(self.client.get("/api/titles", params={"new_days": 7}).json()["total"], 1)
        # the title is browsable like any other
        self.assertIn("Matrix", self.titles(q="Matrix"))
        # a second search shows that we have it now
        again = self.client.get("/api/search", params={"q": "Matrix"}).json()[0]
        self.assertEqual((again["title_id"], again["manual"]), (title["id"], True))

        # dropping it keeps the title but stops tracking it
        self.assertEqual(self.client.delete(f"/api/titles/{title['id']}/import").status_code, 204)
        self.assertNotIn("Matrix", self.titles(q="Matrix"))
        self.assertEqual(self.client.delete(f"/api/titles/{title['id']}/import").status_code, 400)
        self.assertEqual(self.client.delete("/api/titles/99999/import").status_code, 404)

    def test_genres_and_status(self):
        names = [g["name"] for g in self.client.get("/api/genres").json()]
        self.assertIn("Action", names)
        self.assertNotIn("Action & Adventure", names)
        self.assertEqual(self.client.get("/api/status").json()["titles"], 7)


class WebUiTest(unittest.TestCase):
    def test_spa_routes_and_files(self):
        from fastapi import FastAPI

        from moviebot.api import mount_web_ui

        web = Path(tempfile.mkdtemp())
        (web / "assets").mkdir()
        (web / "index.html").write_text("<html>app</html>")
        (web / "favicon.svg").write_text("<svg/>")
        (web / "assets" / "app.js").write_text("js")
        app = FastAPI()
        mount_web_ui(app, web)
        client = TestClient(app)
        self.assertEqual(client.get("/").text, "<html>app</html>")
        self.assertEqual(client.get("/neu").text, "<html>app</html>")  # client-side route
        self.assertEqual(client.get("/favicon.svg").text, "<svg/>")
        self.assertEqual(client.get("/assets/app.js").text, "js")
        self.assertEqual(client.get("/api/nope").status_code, 404)
        self.assertEqual(client.get("/../../etc/passwd").text, "<html>app</html>")


if __name__ == "__main__":
    unittest.main()
