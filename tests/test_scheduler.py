import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path

from moviebot.config import Config, load_config
from moviebot.db import connect
from moviebot.scheduler import SnapshotScheduler, snapshot_lock


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
