"""A small pretend TMDB for tests that run the whole snapshot.

It has a state – which title is offered where, which seasons a series has – that a test
changes from day to day. Only `get` is replaced, so everything above it (catalog paging,
detail requests, the change list) is the real client code.
"""

from moviebot.tmdb import TMDBClient, TMDBError, TMDBNotFound

PROVIDERS = {9: "Amazon Prime Video", 2100: "Amazon Prime Video with Ads", 30: "WOW"}
GENRES = {28: "Action"}
PAGE_SIZE = 20


class MiniTmdb(TMDBClient):
    def __init__(self):
        super().__init__(api_key="x", min_interval=0)
        self.titles: dict[tuple[str, int], dict] = {}
        self.changed_series: set[int] = set()        # what /tv/changes reports
        self.broken_providers: set[int] = set()      # catalog requests for these fail
        self.broken_details: set[tuple[str, int]] = set()
        self.deleted: set[tuple[str, int]] = set()   # TMDB answers 404
        self.crash: Exception | None = None          # raised by every request
        self.calls: list[str] = []

    # --- the state a test changes ----------------------------------------------------

    def movie(self, tmdb_id: int, title: str, providers: list[int], released: str = "2025-05-01") -> None:
        self.titles[("movie", tmdb_id)] = {"title": title, "date": released, "flatrate": providers}

    def series(self, tmdb_id: int, title: str, providers: list[int], seasons: dict[int, str]) -> None:
        """`seasons`: number -> air date."""
        self.titles[("tv", tmdb_id)] = {"title": title, "date": min(seasons.values()),
                                        "flatrate": providers, "seasons": dict(seasons)}

    def offer(self, media_type: str, tmdb_id: int, providers: list[int]) -> None:
        """Change where a title runs; an empty list takes it out of every catalog."""
        self.titles[(media_type, tmdb_id)]["flatrate"] = providers

    # --- what the client asks for ----------------------------------------------------

    def get(self, path, params=None):
        self.calls.append(path)
        if self.crash:
            raise self.crash
        params = params or {}
        parts = path.strip("/").split("/")
        if parts[0] == "watch":
            return {"results": [{"provider_id": p, "provider_name": n} for p, n in PROVIDERS.items()]}
        if parts[0] == "genre":
            return {"genres": [{"id": i, "name": n} for i, n in GENRES.items()]}
        if parts[0] == "discover":
            return self._discover(parts[1], params)
        if parts[1] == "changes":
            ids = sorted(self.changed_series) if parts[0] == "tv" else []
            return {"results": [{"id": i} for i in ids], "total_pages": 1}
        return self._details(parts[0], int(parts[1]))

    def _discover(self, media_type: str, params: dict) -> dict:
        providers = {int(p) for p in params["with_watch_providers"].split("|")}
        if providers & self.broken_providers:
            raise TMDBError(f"HTTP 500 für /discover/{media_type}")
        if "flatrate" not in params["with_watch_monetization_types"].split("|"):
            hits = []
        else:
            hits = sorted(tmdb_id for (m, tmdb_id), t in self.titles.items()
                          if m == media_type and providers & set(t["flatrate"]))
        page = params["page"]
        return {
            "total_results": len(hits), "total_pages": max(1, -(-len(hits) // PAGE_SIZE)),
            "results": [self._basic(media_type, i)
                        for i in hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]],
        }

    def _basic(self, media_type: str, tmdb_id: int) -> dict:
        t = self.titles[(media_type, tmdb_id)]
        names = {"title": t["title"], "release_date": t["date"]} if media_type == "movie" else \
                {"name": t["title"], "first_air_date": t["date"]}
        return {"id": tmdb_id, "genre_ids": list(GENRES), "popularity": 1.0, **names}

    def _details(self, media_type: str, tmdb_id: int) -> dict:
        key = (media_type, tmdb_id)
        if key in self.deleted or key not in self.titles:
            raise TMDBNotFound(f"404 für /{media_type}/{tmdb_id}")
        if key in self.broken_details:
            raise TMDBError(f"HTTP 500 für /{media_type}/{tmdb_id}")
        t = self.titles[key]
        d = {
            **self._basic(media_type, tmdb_id),
            "genres": [{"id": i, "name": n} for i, n in GENRES.items()],
            "credits": {"crew": [{"job": "Director", "name": "D"}], "cast": [{"name": "A", "order": 0}]},
            "watch/providers": {"results": {"DE": {
                "flatrate": [{"provider_id": p} for p in t["flatrate"]],
                "rent": [{"provider_id": 9}]}}},  # rentable somewhere: TMDB knows the title in DE
        }
        if media_type == "tv":
            seasons = t["seasons"]
            d.update(status="Returning Series", number_of_seasons=len(seasons),
                     last_air_date=max(seasons.values()),
                     seasons=[{"season_number": n, "air_date": a} for n, a in sorted(seasons.items())])
        return d
