import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path

from unittest import mock

from moviebot import queries
from moviebot.config import Config, Service, load_config
from moviebot.db import connect
from moviebot.scheduler import SnapshotScheduler, snapshot_lock, snapshot_locked
from moviebot.tmdb import TMDBClient

from .fake_tmdb import MiniTmdb


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(db_path=Path(tempfile.mkdtemp()) / "s.db", snapshot_time=time(6, 0))
        connect(self.cfg.db_path).close()
        self.scheduler = SnapshotScheduler(self.cfg)

    def add_run(self, started_at: str) -> None:
        conn = connect(self.cfg.db_path)
        with conn:
            conn.execute("INSERT INTO services (key, name, provider_ids, updated_at) "
                         "VALUES ('prime', 'Prime', '[9]', 'x') ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO snapshot_runs (service, media_type, min_year, started_at, status) "
                         "VALUES ('prime', 'movie', 2025, ?, 'ok')", (started_at,))
        conn.close()

    def local_iso(self, dt: datetime) -> str:
        return dt.astimezone().isoformat()

    def test_not_due_before_time(self):
        self.assertFalse(self.scheduler._is_due(datetime(2026, 9, 21, 5, 59)))

    def test_due_after_time_without_run_today(self):
        self.add_run(self.local_iso(datetime(2026, 9, 20, 6, 0)))
        self.assertTrue(self.scheduler._is_due(datetime(2026, 9, 21, 6, 0)))
        # made up later the same day, e.g. after the computer woke up
        self.assertTrue(self.scheduler._is_due(datetime(2026, 9, 21, 14, 30)))

    def test_not_due_when_already_ran_today(self):
        self.add_run(self.local_iso(datetime(2026, 9, 21, 5, 0)))  # manual run in the morning
        self.assertFalse(self.scheduler._is_due(datetime(2026, 9, 21, 7, 0)))

    def test_only_one_automatic_attempt_per_day(self):
        self.scheduler._last_auto_attempt = datetime(2026, 9, 21).date()  # e.g. it failed
        self.assertFalse(self.scheduler._is_due(datetime(2026, 9, 21, 7, 0)))

    def test_next_run(self):
        self.add_run(self.local_iso(datetime(2026, 9, 21, 6, 0)))
        self.assertEqual(self.scheduler.next_run(datetime(2026, 9, 21, 9, 0)), datetime(2026, 9, 22, 6, 0))
        self.assertEqual(self.scheduler.next_run(datetime(2026, 9, 22, 5, 0)), datetime(2026, 9, 22, 6, 0))

    def test_disabled(self):
        s = SnapshotScheduler(Config(db_path=self.cfg.db_path, snapshot_time=None))
        self.assertIsNone(s.next_run())
        self.assertFalse(s._is_due(datetime(2026, 9, 21, 12, 0)))

    def test_trigger_reports_running_and_blocks_second_trigger(self):
        self.assertTrue(self.scheduler.trigger())
        self.assertTrue(self.scheduler.info()["running"])
        self.assertFalse(self.scheduler.trigger())

    def test_trigger_refused_while_another_process_holds_the_lock(self):
        with snapshot_lock(self.cfg.db_path):
            self.assertFalse(self.scheduler.trigger())
        self.assertTrue(self.scheduler.trigger())

    def test_info_reports_the_last_result(self):
        self.assertIsNone(self.scheduler.info()["last_result"])
        self.scheduler.last_result = {"added": 3, "readded": 0, "removed": 1, "new_seasons": 2,
                                      "failed": [], "finished_at": "2026-09-20T06:04"}
        self.assertEqual(self.scheduler.info()["last_result"]["added"], 3)

    def test_progress_is_reported_with_an_estimate(self):
        self.assertIsNone(self.scheduler.info()["progress"])
        self.scheduler._on_progress({"phase": "details", "done": 0, "total": 100, "label": "Titeldaten"})
        self.scheduler._phase_started -= 10  # pretend 10 seconds have passed
        self.scheduler._on_progress({"phase": "details", "done": 25, "total": 100, "label": "Titeldaten"})
        progress = self.scheduler.info()["progress"]
        self.assertEqual((progress["phase"], progress["done"], progress["total"]), ("details", 25, 100))
        self.assertGreaterEqual(progress["eta_seconds"], 29)  # ~30s left at that rate

    def test_lock_is_exclusive(self):
        with snapshot_lock(self.cfg.db_path) as first:
            with snapshot_lock(self.cfg.db_path) as second:
                self.assertEqual((first, second), (True, False))
        with snapshot_lock(self.cfg.db_path) as again:
            self.assertTrue(again)


class SchedulerRunTest(unittest.TestCase):
    """The run itself, with a pretend TMDB in place of the real one."""

    def setUp(self):
        self.cfg = Config(db_path=Path(tempfile.mkdtemp()) / "s.db", snapshot_time=time(6, 0),
                          services=[Service("prime", "Prime Video", [9]), Service("wow", "WOW", [30])])
        connect(self.cfg.db_path).close()
        self.scheduler = SnapshotScheduler(self.cfg)
        self.tmdb = MiniTmdb()
        self.tmdb.movie(1, "Film eins", [9])
        patcher = mock.patch.object(TMDBClient, "from_config", return_value=self.tmdb)
        patcher.start()
        self.addCleanup(patcher.stop)

    def stored_summary(self) -> dict | None:
        conn = connect(self.cfg.db_path)
        try:
            return queries.get_run_summary(conn)
        finally:
            conn.close()

    def test_run_reports_and_stores_what_changed(self):
        self.scheduler._run()  # first run: nothing to announce
        self.assertEqual(self.scheduler.last_result["added"], 0)
        self.tmdb.movie(2, "Film zwei", [9])
        self.scheduler._run()
        result = self.scheduler.last_result
        self.assertEqual((result["added"], result["removed"], result["failed"]), (1, 0, []))
        self.assertEqual(self.stored_summary(), result)  # survives a restart
        info = self.scheduler.info()
        self.assertEqual((info["running"], info["last_error"]), (False, None))
        self.assertIsNone(info["progress"])  # only shown while a run is going on
        self.assertFalse(snapshot_locked(self.cfg.db_path))
        self.assertFalse(self.scheduler._is_due(datetime.now().replace(hour=7)))  # done for today

    def test_failed_service_is_shown_as_error(self):
        self.tmdb.broken_providers = {30}
        with self.assertLogs("moviebot.snapshot", level="ERROR"):
            self.scheduler._run()
        self.assertEqual(self.scheduler.last_error, "Fehlgeschlagen: wow/movie, wow/tv")
        self.assertEqual(self.scheduler.last_result["failed"], ["wow/movie", "wow/tv"])
        # the next clean run clears the error
        self.tmdb.broken_providers = set()
        self.scheduler._run()
        self.assertIsNone(self.scheduler.last_error)

    def test_crash_keeps_the_server_alive(self):
        self.tmdb.crash = RuntimeError("kaputt")
        with self.assertLogs("moviebot.scheduler", level="ERROR"):
            self.scheduler._run()  # must not raise
        self.assertEqual(self.scheduler.last_error, "kaputt")
        self.assertFalse(self.scheduler.info()["running"])
        self.assertFalse(snapshot_locked(self.cfg.db_path))
        self.assertIsNone(self.stored_summary())
        # and the next run works
        self.tmdb.crash = None
        self.scheduler._run()
        self.assertIsNone(self.scheduler.last_error)
        self.assertEqual(self.scheduler.last_result["failed"], [])

    def test_run_is_skipped_while_another_one_holds_the_lock(self):
        with snapshot_lock(self.cfg.db_path):
            with self.assertLogs("moviebot.scheduler", level="WARNING"):
                self.scheduler._run()
        self.assertIn("Übersprungen", self.scheduler.last_error)
        self.assertEqual(self.tmdb.calls, [])

    def test_triggered_run_happens_in_the_background(self):
        self.scheduler.at = None  # no automatic run, only the one asked for
        self.scheduler.start()
        self.addCleanup(self.scheduler.stop)
        self.assertTrue(self.scheduler.trigger())
        self.assertFalse(self.scheduler.trigger())  # one at a time
        for _ in range(200):
            if self.scheduler.last_result and not self.scheduler.info()["running"]:
                break
            self.scheduler._stop.wait(0.05)
        self.assertEqual(self.scheduler.last_result["failed"], [])
        self.assertFalse(self.scheduler.info()["running"])
        self.scheduler.stop()
        self.scheduler._thread.join(timeout=5)
        self.assertFalse(self.scheduler._thread.is_alive())


class ScheduleConfigTest(unittest.TestCase):
    def config(self, text: str) -> Config:
        path = Path(tempfile.mkdtemp()) / "c.toml"
        path.write_text(text)
        return load_config(path)

    def test_default_and_custom_and_off(self):
        self.assertEqual(self.config("").snapshot_time, time(6, 0))
        self.assertEqual(self.config('[schedule]\ntime = "05:30"').snapshot_time, time(5, 30))
        self.assertIsNone(self.config('[schedule]\ntime = ""').snapshot_time)
        with self.assertRaises(ValueError):
            self.config('[schedule]\ntime = "6 Uhr"')


if __name__ == "__main__":
    unittest.main()
