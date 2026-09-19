-- SQLite schema: single source of truth for moviebot.
-- Dates (first_seen, last_seen, removed_at, event_date, released_at) are local ISO dates
-- (YYYY-MM-DD), timestamps (*_at otherwise) are ISO datetimes in UTC.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Raw watch providers TMDB/JustWatch knows for the region (for looking up IDs).
CREATE TABLE IF NOT EXISTS providers (
    provider_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    logo_path   TEXT,
    updated_at  TEXT NOT NULL
);

-- A streaming service as the user sees it, e.g. "Prime Video" = providers 9 + 2100 (with ads).
-- Defined in config.toml; `subscribed` ("Meine Abos") is user state and lives only here.
CREATE TABLE IF NOT EXISTS services (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    provider_ids TEXT NOT NULL,                -- JSON list of TMDB provider IDs
    tracked      INTEGER NOT NULL DEFAULT 1,   -- 0 = removed from config, history kept
    free         INTEGER NOT NULL DEFAULT 0,   -- 1 = free / with ads, no subscription needed
    subscribed   INTEGER NOT NULL DEFAULT 0,   -- only meaningful for free = 0
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS titles (
    id                 INTEGER PRIMARY KEY,
    media_type         TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    tmdb_id            INTEGER NOT NULL,       -- TMDB IDs are only unique per media type
    title              TEXT NOT NULL,
    original_title     TEXT,
    original_language  TEXT,
    release_date       TEXT,                   -- tv: first air date
    year               INTEGER,
    last_air_date      TEXT,                   -- tv only
    tv_status          TEXT,                   -- tv only: Returning Series / Ended / Canceled / ...
    number_of_seasons  INTEGER,                -- tv only
    next_episode_date  TEXT,                   -- tv only
    genres             TEXT NOT NULL DEFAULT '[]',  -- JSON list of genre names
    keywords           TEXT NOT NULL DEFAULT '[]',
    directors          TEXT NOT NULL DEFAULT '[]',  -- tv: creators
    cast_members       TEXT NOT NULL DEFAULT '[]',  -- top billed first
    overview           TEXT,
    runtime            INTEGER,                -- minutes; tv: typical episode length
    vote_average       REAL,
    vote_count         INTEGER,
    popularity         REAL,
    poster_path        TEXT,
    imdb_id            TEXT,
    watch_link         TEXT,                   -- TMDB/JustWatch page listing where to watch
    details_fetched_at TEXT,                   -- NULL = only discover data so far
    offers_fetched_at  TEXT,                   -- NULL = `offers` not stored yet for this title
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (media_type, tmdb_id)
);

CREATE TABLE IF NOT EXISTS seasons (
    title_id      INTEGER NOT NULL REFERENCES titles(id),
    season_number INTEGER NOT NULL,
    name          TEXT,
    air_date      TEXT,
    episode_count INTEGER,
    released_at   TEXT,                        -- day we first saw it released (air_date <= today)
    PRIMARY KEY (title_id, season_number)
);

-- Every offer of a title in the region, as reported by the title's watch/providers
-- (the authoritative source). Replaced completely on each detail fetch.
CREATE TABLE IF NOT EXISTS offers (
    title_id     INTEGER NOT NULL REFERENCES titles(id),
    provider_id  INTEGER NOT NULL,
    monetization TEXT NOT NULL,              -- flatrate / free / ads / rent / buy
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (title_id, provider_id, monetization)
);
CREATE INDEX IF NOT EXISTS idx_offers_provider ON offers (provider_id, monetization);

CREATE TABLE IF NOT EXISTS snapshot_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    service       TEXT NOT NULL REFERENCES services(key),
    media_type    TEXT NOT NULL,
    min_year      INTEGER NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN
                  ('running', 'baseline', 'rescoped', 'ok', 'suspicious', 'failed')),
    title_count   INTEGER,
    added_count   INTEGER,
    removed_count INTEGER,
    message       TEXT
);

CREATE TABLE IF NOT EXISTS availability (
    title_id         INTEGER NOT NULL REFERENCES titles(id),
    service          TEXT NOT NULL REFERENCES services(key),
    first_seen       TEXT NOT NULL,                -- first day *we* saw it, not the real start date
    last_seen        TEXT NOT NULL,
    removed_at       TEXT,                         -- NULL = currently in the subscription
    missed_runs      INTEGER NOT NULL DEFAULT 0,   -- consecutive days missing from the snapshot
    last_missed_date TEXT,
    in_baseline      INTEGER NOT NULL DEFAULT 0,   -- 1 = picked up silently (first run / scope change)
    verified         INTEGER,                      -- 1/0 = confirmed/contradicted by the title's watch/providers
    PRIMARY KEY (title_id, service)
);
CREATE INDEX IF NOT EXISTS idx_availability_service ON availability (service, removed_at);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER REFERENCES snapshot_runs(run_id),
    title_id      INTEGER NOT NULL REFERENCES titles(id),
    service       TEXT REFERENCES services(key),   -- NULL for new_season
    event         TEXT NOT NULL CHECK (event IN ('added', 'readded', 'removed', 'new_season')),
    season_number INTEGER,
    event_date    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events (event_date, event);

CREATE TABLE IF NOT EXISTS user_state (
    title_id   INTEGER PRIMARY KEY REFERENCES titles(id),
    status     TEXT NOT NULL DEFAULT 'unseen'
               CHECK (status IN ('unseen', 'seen', 'not_interested')),
    rating     INTEGER CHECK (rating BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL
);
