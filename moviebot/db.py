import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 10
SCHEMA_FILE = Path(__file__).with_name("schema.sql")

def _add_column(table: str, declaration: str):
    """Migration step that adds a column unless it is already there – a database that jumps
    several versions at once gets the new tables from schema.sql first."""
    name = declaration.split()[0]

    def step(conn: sqlite3.Connection) -> None:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")

    return step


def _split_lists_and_filters(conn: sqlite3.Connection) -> None:
    """v7: dynamic lists became saved filters. Databases that jump here from an older version
    never had those columns, so there is nothing to move."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(lists)")}
    if "kind" not in columns:
        return
    conn.execute("""INSERT INTO saved_filters (name, filters, position, created_at, updated_at)
                    SELECT name, filters, position, created_at, updated_at
                    FROM lists WHERE kind = 'dynamic' AND filters IS NOT NULL""")
    conn.execute("DELETE FROM lists WHERE kind = 'dynamic'")
    conn.execute("ALTER TABLE lists DROP COLUMN filters")
    conn.execute("ALTER TABLE lists DROP COLUMN kind")


# Upgrade steps for existing databases: target version -> SQL statements or functions.
# New tables/indexes also come from schema.sql (CREATE ... IF NOT EXISTS); only changes to
# existing tables need to be listed here.
MIGRATIONS: dict[int, list] = {
    3: [
        _add_column("titles", "watch_link TEXT"),
        _add_column("titles", "offers_fetched_at TEXT"),
    ],
    4: [
        _add_column("services", "free INTEGER NOT NULL DEFAULT 0"),
    ],
    5: [
        _add_column("titles", "age_rating INTEGER"),
        _add_column("titles", "age_rating_source TEXT"),
        _add_column("titles", "age_rating_raw TEXT"),
        _add_column("titles", "ratings_fetched_at TEXT"),
    ],
    6: [],  # lists/list_items come from schema.sql
    7: [_split_lists_and_filters],  # lists hold titles, saved_filters hold searches
    9: [_add_column("titles", "manual INTEGER NOT NULL DEFAULT 0")],
    8: [  # "no German data" was stored as contradicted; re-check those
        """UPDATE availability SET verified = NULL WHERE verified = 0 AND removed_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.title_id = availability.title_id)""",
    ],
    10: [],  # season_state comes from schema.sql
}


class SchemaMismatch(Exception):
    pass


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn, path)
    return conn


def _current_version(conn: sqlite3.Connection) -> int | None:
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not has_table:
        return None
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row[0] if row else None


def init_schema(conn: sqlite3.Connection, path: Path | str = ":memory:") -> None:
    version = _current_version(conn)
    if version is not None and version > SCHEMA_VERSION:
        raise SchemaMismatch(f"Datenbank {path} hat Schema-Version {version}, dieser Code kennt "
                             f"nur bis {SCHEMA_VERSION}. Code aktualisieren.")
    if version is not None and version < SCHEMA_VERSION:
        missing = [v for v in range(version + 1, SCHEMA_VERSION + 1) if v not in MIGRATIONS]
        if missing:
            raise SchemaMismatch(f"Keine Migration von Version {version} auf {SCHEMA_VERSION}.")
        if str(path) != ":memory:":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup = Path(f"{path}.bak-v{version}")
            shutil.copy2(path, backup)
            log.info("Sicherung vor Migration: %s", backup)
        with conn:
            # new tables first, so migrations can move data into them
            conn.executescript(SCHEMA_FILE.read_text())
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for step in MIGRATIONS[v]:
                    step(conn) if callable(step) else conn.execute(step)
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        log.info("Datenbank von Version %d auf %d migriert", version, SCHEMA_VERSION)
    with conn:
        conn.executescript(SCHEMA_FILE.read_text())
        if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
