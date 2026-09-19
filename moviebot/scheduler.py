"""Daily snapshot inside the server process.

Runs once per day at the configured local time. If that moment was missed (computer asleep,
server stopped), the run is made up as soon as the server is up again – but at most one
automatic attempt per day, so a failing run does not retry in a loop.
"""

import fcntl
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import snapshot
from .config import Config
from .db import connect
from .tmdb import TMDBClient

log = logging.getLogger(__name__)

CHECK_INTERVAL = 30  # seconds


@contextmanager
def snapshot_lock(db_path: Path) -> Iterator[bool]:
    """Exclusive lock across processes (server and CLI). Yields False if already held."""
    lock_path = Path(f"{db_path}.snapshot.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            f.write(str(os.getpid()))
            f.flush()
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def snapshot_locked(db_path: Path) -> bool:
    """Is a snapshot running right now (in this or another process)?"""
    with snapshot_lock(db_path) as acquired:
        return not acquired


def last_run_date(db_path: Path) -> date | None:
    """Local date of the most recent snapshot run (any outcome)."""
    conn = sqlite3.connect(db_path)  # plain connection: this runs every CHECK_INTERVAL
    try:
        started = conn.execute("SELECT MAX(started_at) FROM snapshot_runs").fetchone()[0]
    finally:
        conn.close()
    return datetime.fromisoformat(started).astimezone().date() if started else None


class SnapshotScheduler:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.at: time | None = cfg.snapshot_time
        self.running = False
        self.last_error: str | None = None
        self.last_finished: datetime | None = None
        self._last_auto_attempt: date | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._manual = False
        self._thread: threading.Thread | None = None

    # --- control -------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="snapshot-scheduler", daemon=True)
        self._thread.start()
        if self.at:
            log.info("Täglicher Abgleich um %s", self.at.strftime("%H:%M"))

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> bool:
        """Start a run now. False if one is already running (here or e.g. from the CLI)."""
        if self.running or self._manual or snapshot_locked(self.cfg.db_path):
            return False
        self._manual = True
        self._wake.set()
        return True

    def next_run(self, now: datetime | None = None) -> datetime | None:
        if not self.at:
            return None
        now = now or datetime.now()
        today_at = datetime.combine(now.date(), self.at)
        if self._is_due(now):
            return now
        return today_at if now < today_at else today_at + timedelta(days=1)

    def info(self) -> dict:
        running = self.running or self._manual  # a triggered run counts from the click on
        next_run = None if running else self.next_run()
        return {
            "time": self.at.strftime("%H:%M") if self.at else None,
            "running": running,
            "next_run": next_run.isoformat(timespec="minutes") if next_run else None,
            "last_error": self.last_error,
        }

    # --- internals -----------------------------------------------------------------

    def _is_due(self, now: datetime) -> bool:
        if not self.at or now.time() < self.at or self._last_auto_attempt == now.date():
            return False
        last = last_run_date(self.cfg.db_path)
        return last is None or last < now.date()

    def _loop(self) -> None:
        while not self._stop.is_set():
            manual, self._manual = self._manual, False
            now = datetime.now()
            if manual or self._is_due(now):
                if not manual:
                    self._last_auto_attempt = now.date()
                self._run()
            self._wake.wait(CHECK_INTERVAL)
            self._wake.clear()

    def _run(self) -> None:
        with snapshot_lock(self.cfg.db_path) as acquired:
            if not acquired:
                log.warning("Abgleich übersprungen – es läuft bereits einer")
                self.last_error = "Übersprungen – es lief gerade ein anderer Abgleich"
                return
            self.running = True
            log.info("Abgleich startet")
            try:
                conn = connect(self.cfg.db_path)
                try:
                    results = snapshot.run(conn, TMDBClient.from_config(self.cfg), self.cfg)
                finally:
                    conn.close()
                failed = [f"{s}/{m}" for (s, m), r in results.items() if r is None]
                self.last_error = f"Fehlgeschlagen: {', '.join(failed)}" if failed else None
                log.info("Abgleich fertig%s", f" ({self.last_error})" if failed else "")
            except Exception as e:  # keep the server alive, show the problem in the UI
                log.exception("Abgleich fehlgeschlagen")
                self.last_error = str(e)
            finally:
                self.running = False
                self.last_finished = datetime.now()
