import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4
SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# Upgrade steps for existing databases: target version -> statements.
# New tables/indexes also come from schema.sql (CREATE ... IF NOT EXISTS); only changes to
# existing tables need to be listed here.
MIGRATIONS: dict[int, list[str]] = {
    3: [
        "ALTER TABLE titles ADD COLUMN watch_link TEXT",
        "ALTER TABLE titles ADD COLUMN offers_fetched_at TEXT",
    ],
    4: [
        "ALTER TABLE services ADD COLUMN free INTEGER NOT NULL DEFAULT 0",
    ],
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
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[v]:
                    conn.execute(statement)
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
