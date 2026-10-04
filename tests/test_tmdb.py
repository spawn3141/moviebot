"""The real request code of the TMDB client, with the network replaced."""

import io
import json
import unittest
from email.message import Message
from unittest import mock
from urllib.error import HTTPError, URLError

from moviebot.tmdb import TMDBClient, TMDBError, TMDBNotFound

SECRET = "geheimer-schluessel"


def answer(data: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(data).encode())


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.client = TMDBClient(api_key=SECRET, min_interval=0, max_retries=3)
        patch_open = mock.patch("moviebot.tmdb.urlopen")
        patch_sleep = mock.patch("moviebot.tmdb.time.sleep")
        self.urlopen, self.sleep = patch_open.start(), patch_sleep.start()
        self.addCleanup(patch_open.stop)
        self.addCleanup(patch_sleep.stop)

    def http_error(self, code: int, retry_after: str | None = None) -> HTTPError:
        headers = Message()
        if retry_after:
            headers["Retry-After"] = retry_after
        error = HTTPError(f"https://example.invalid/?api_key={SECRET}", code, "x", headers,
                          io.BytesIO(b'{"status_message": "nope"}'))
        self.addCleanup(error.close)
        return error

    def waits(self) -> list[float]:
        return [c.args[0] for c in self.sleep.call_args_list]

    def test_request_carries_key_and_drops_empty_parameters(self):
        self.urlopen.return_value = answer({"ok": True})
        self.assertEqual(self.client.get("/movie/1", {"language": "de-DE", "page": None}), {"ok": True})
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url,
                         f"https://api.themoviedb.org/3/movie/1?language=de-DE&api_key={SECRET}")
        self.assertEqual(self.client.request_count, 1)

    def test_token_goes_into_the_header_not_the_address(self):
        client = TMDBClient(read_access_token=SECRET, min_interval=0)
        self.urlopen.return_value = answer({})
        client.get("/movie/1")
        request = self.urlopen.call_args.args[0]
        self.assertNotIn(SECRET, request.full_url)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")

    def test_access_is_required(self):
        with self.assertRaises(TMDBError):
            TMDBClient()

    def test_too_many_requests_waits_as_told_and_tries_again(self):
        self.urlopen.side_effect = [self.http_error(429, retry_after="7"), answer({"ok": True})]
        with self.assertLogs("moviebot.tmdb", level="WARNING"):
            self.assertEqual(self.client.get("/movie/1"), {"ok": True})
        self.assertEqual(self.waits(), [7.0])

    def test_server_errors_are_retried_with_growing_pauses_then_given_up(self):
        self.urlopen.side_effect = [self.http_error(503) for _ in range(4)]
        with self.assertLogs("moviebot.tmdb", level="WARNING"), self.assertRaises(TMDBError) as raised:
            self.client.get("/movie/1")
        self.assertEqual(self.urlopen.call_count, 4)  # first try + max_retries
        self.assertEqual(self.waits(), [1.0, 2.0, 4.0])
        self.assertIn("HTTP 503 für /movie/1", str(raised.exception))
        self.assertNotIn(SECRET, str(raised.exception))  # the address holds the key

    def test_not_found_is_its_own_error_and_not_retried(self):
        self.urlopen.side_effect = self.http_error(404)
        with self.assertRaises(TMDBNotFound):
            self.client.get("/movie/1")
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(self.waits(), [])

    def test_wrong_key_fails_at_once(self):
        self.urlopen.side_effect = self.http_error(401)
        with self.assertRaises(TMDBError) as raised:
            self.client.get("/movie/1")
        self.assertNotIsInstance(raised.exception, TMDBNotFound)
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_network_trouble_is_retried(self):
        self.urlopen.side_effect = [URLError("no route"), TimeoutError(), answer({"ok": True})]
        self.assertEqual(self.client.get("/movie/1"), {"ok": True})
        self.assertEqual(self.waits(), [1, 2])

    def test_network_down_ends_in_a_clear_error(self):
        self.urlopen.side_effect = URLError("no route")
        with self.assertRaises(TMDBError) as raised:
            self.client.get("/movie/1")
        self.assertEqual(self.urlopen.call_count, 4)
        self.assertIn("Netzwerkfehler für /movie/1", str(raised.exception))
        self.assertNotIn(SECRET, str(raised.exception))


class EndpointTest(unittest.TestCase):
    """What the client makes of TMDB's answers."""

    def client_answering(self, pages: dict) -> TMDBClient:
        client = TMDBClient(api_key="x", min_interval=0)
        self.asked: list[tuple[str, dict]] = []

        def get(path, params=None):
            self.asked.append((path, params))
            return pages[(path, (params or {}).get("page", 1))]

        client.get = get
        return client

    def test_change_list_is_read_page_by_page(self):
        from datetime import date

        client = self.client_answering({
            ("/tv/changes", 1): {"results": [{"id": 1}, {"id": 2}], "total_pages": 3},
            ("/tv/changes", 2): {"results": [{"id": 3}], "total_pages": 3},
            ("/tv/changes", 3): {"results": [{"id": 2}], "total_pages": 3},
        })
        self.assertEqual(client.changed_ids("tv", date(2026, 9, 1), date(2026, 9, 3)), {1, 2, 3})
        self.assertEqual(self.asked[0][1], {"start_date": "2026-09-01", "end_date": "2026-09-03", "page": 1})
        with self.assertLogs("moviebot.tmdb", level="WARNING"):  # more pages than we are willing to read
            self.assertEqual(client.changed_ids("tv", date(2026, 9, 1), date(2026, 9, 3), max_pages=2),
                             {1, 2, 3})

    def test_search_keeps_only_films_and_series(self):
        client = self.client_answering({("/search/multi", 1): {"results": [
            {"id": 1, "media_type": "movie"}, {"id": 2, "media_type": "person"},
            {"id": 3, "media_type": "tv"}, {"id": 4, "media_type": "movie"}]}})
        self.assertEqual([r["id"] for r in client.search("x", limit=2)], [1, 3])

    def test_providers_and_genres(self):
        client = self.client_answering({
            ("/watch/providers/movie", 1): {"results": [{"provider_id": 9}]},
            ("/genre/movie/list", 1): {"genres": [{"id": 28, "name": "Action"}]},
        })
        self.assertEqual(client.watch_providers("movie"), [{"provider_id": 9}])
        self.assertEqual(client.genres("movie"), {28: "Action"})
        self.assertEqual(self.asked[0][1], {"watch_region": "DE", "language": "de-DE"})
