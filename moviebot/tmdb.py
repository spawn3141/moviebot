"""Minimal TMDB v3 client (stdlib; certifi for TLS certificates if installed).

Watch-provider data on TMDB is supplied by JustWatch. Attribution is required when
displaying it ("Streaming-Daten: JustWatch").
"""

import json
import logging
import ssl
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
# /discover returns at most 500 pages of 20 results; bigger catalogs must be split.
MAX_PAGES = 500
MAX_DISCOVER_RESULTS = MAX_PAGES * 20
EARLIEST_DATE = date(1870, 1, 1)

# Per media type: date field to sort/split by, and the filter that implements `min_year`.
# For series the scope filter is `air_date` (any episode aired since), so a show that started
# in 2019 but has a new season in 2025 is still included.
DISCOVER = {
    "movie": {"date_field": "primary_release_date", "scope_filter": "primary_release_date.gte"},
    "tv": {"date_field": "first_air_date", "scope_filter": "air_date.gte"},
}


def _ssl_context() -> ssl.SSLContext:
    # certifi's CA bundle avoids CERTIFICATE_VERIFY_FAILED on python.org builds for macOS.
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class TMDBError(Exception):
    pass


class TMDBNotFound(TMDBError):
    pass


class TMDBClient:
    def __init__(
        self,
        api_key: str | None = None,
        read_access_token: str | None = None,
        *,
        region: str = "DE",
        language: str = "de-DE",
        min_interval: float = 0.03,
        max_retries: int = 5,
        timeout: float = 20.0,
    ):
        if not api_key and not read_access_token:
            raise TMDBError(
                "Kein TMDB-Zugang konfiguriert: TMDB_API_KEY oder TMDB_READ_ACCESS_TOKEN setzen "
                "(oder in config.toml eintragen)."
            )
        self.api_key = api_key
        self.read_access_token = read_access_token
        self.region = region
        self.language = language
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.request_count = 0
        self._last_request = 0.0
        self._ssl = _ssl_context()

    # --- transport -------------------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Accept": "application/json"}
        if self.read_access_token:
            headers["Authorization"] = f"Bearer {self.read_access_token}"
        else:
            params["api_key"] = self.api_key
        url = f"{BASE_URL}{path}?{urlencode(params)}"

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=self.timeout, context=self._ssl) as resp:
                    return json.load(resp)
            except HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < self.max_retries:
                    wait = float(e.headers.get("Retry-After") or 2**attempt)
                    log.warning("TMDB %s für %s, neuer Versuch in %.0fs", e.code, path, wait)
                    time.sleep(wait)
                    continue
                # Never include the URL: it may contain the api_key.
                detail = e.read()[:200].decode(errors="replace")
                if e.code == 404:
                    raise TMDBNotFound(f"404 für {path}") from None
                raise TMDBError(f"HTTP {e.code} für {path}: {detail}") from None
            except (URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise TMDBError(f"Netzwerkfehler für {path}: {e}") from None
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        self.request_count += 1

    # --- endpoints -------------------------------------------------------------

    def watch_providers(self, media_type: str) -> list[dict]:
        data = self.get(
            f"/watch/providers/{media_type}", {"watch_region": self.region, "language": self.language}
        )
        return data.get("results", [])

    def genres(self, media_type: str) -> dict[int, str]:
        data = self.get(f"/genre/{media_type}/list", {"language": self.language})
        return {g["id"]: g["name"] for g in data.get("genres", [])}

    def details(self, media_type: str, tmdb_id: int) -> dict:
        return self.get(
            f"/{media_type}/{tmdb_id}",
            {"language": self.language, "append_to_response": "keywords,credits,watch/providers"},
        )

    def discover_catalog(self, media_type: str, provider_ids: list[int],
                         monetization: list[str], min_year: int) -> dict[int, dict]:
        """Complete current catalog of one service as {tmdb_id: discover item}."""
        spec = DISCOVER[media_type]
        base = {
            "watch_region": self.region,
            "with_watch_providers": "|".join(str(p) for p in provider_ids),  # | = OR
            "with_watch_monetization_types": "|".join(monetization),
            "include_adult": "false",
            "language": self.language,
            "sort_by": f"{spec['date_field']}.asc",
            spec["scope_filter"]: f"{min_year}-01-01",
        }
        results: dict[int, dict] = {}
        first = self._discover_page(media_type, base, 1)
        total = first.get("total_results", 0)
        if total <= MAX_DISCOVER_RESULTS:
            self._collect_pages(media_type, base, first, results)
        else:
            # Date filters drop titles without a date; that loss is logged below.
            log.info("%s %s: %d Titel > %d, teile nach Datum auf",
                     media_type, provider_ids, total, MAX_DISCOVER_RESULTS)
            start = date(min_year, 1, 1) if media_type == "movie" else EARLIEST_DATE
            self._discover_range(media_type, base, start, date.today() + timedelta(days=730), results)
        if len(results) < total:
            log.warning("%s %s: TMDB meldet %d Titel, geladen wurden %d",
                        media_type, provider_ids, total, len(results))
        return results

    def _discover_page(self, media_type: str, params: dict, page: int) -> dict:
        return self.get(f"/discover/{media_type}", {**params, "page": page})

    def _collect_pages(self, media_type: str, params: dict, first: dict,
                       results: dict[int, dict]) -> None:
        for item in first.get("results", []):
            results[item["id"]] = item
        for page in range(2, min(first.get("total_pages", 1), MAX_PAGES) + 1):
            for item in self._discover_page(media_type, params, page).get("results", []):
                results[item["id"]] = item

    def _discover_range(self, media_type: str, base: dict, start: date, end: date,
                        results: dict[int, dict]) -> None:
        field = DISCOVER[media_type]["date_field"]
        # For movies this overrides the scope filter (same field); the range starts at min_year.
        params = {**base, f"{field}.gte": start.isoformat(), f"{field}.lte": end.isoformat()}
        first = self._discover_page(media_type, params, 1)
        if first.get("total_results", 0) > MAX_DISCOVER_RESULTS and start < end:
            mid = start + (end - start) // 2
            self._discover_range(media_type, base, start, mid, results)
            self._discover_range(media_type, base, mid + timedelta(days=1), end, results)
        else:
            self._collect_pages(media_type, params, first, results)


def provider_ids_with(details: dict, region: str, monetization: list[str]) -> set[int]:
    """Provider IDs that offer this title with one of the monetization types in the region."""
    region_data = details.get("watch/providers", {}).get("results", {}).get(region, {})
    return {p["provider_id"] for m in monetization for p in region_data.get(m, [])}
