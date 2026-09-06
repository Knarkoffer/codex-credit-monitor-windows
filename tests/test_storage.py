import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from codex_credit_monitor_windows.domain import (
    Observation,
    PaceState,
    Schedule,
    Thresholds,
    UsageMetric,
    UsageMode,
)
from codex_credit_monitor_windows.storage import HistoryStore


class StorageTests(TestCase):
    def test_existing_database_gets_the_metric_column(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE windows(id INTEGER PRIMARY KEY, account_key TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT NOT NULL, start_minutes INTEGER NOT NULL, end_minutes INTEGER NOT NULL, completed INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO windows VALUES(1,'account','2025-01-01T00:00:00+00:00','2025-02-01T00:00:00+00:00','UTC',480,1020,1)"
            )
            connection.commit()
            connection.close()
            store = HistoryStore(path)
            columns = {
                row["name"]
                for row in store.connection.execute("PRAGMA table_info(windows)")
            }
            self.assertIn("metric", columns)
            self.assertIn("usage_mode", columns)
            self.assertEqual(store.window(1).schedule.mode, UsageMode.WORK)
            store.close()

    def test_history_and_notification_crossing_are_persisted(self):
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite")
            reset, first_at = datetime(2025, 2, 1, tzinfo=timezone.utc), datetime(
                2025, 1, 6, 12, tzinfo=timezone.utc
            )
            initial = store.commit(
                Observation(
                    "user", "workspace", Decimal(100), Decimal(90), reset, first_at
                ),
                Schedule(),
                Thresholds(),
                "UTC",
            )
            self.assertEqual(initial.evaluation.state, PaceState.CRITICAL)
            self.assertFalse(initial.notify)
            store.commit(
                Observation(
                    "user",
                    "workspace",
                    Decimal(100),
                    Decimal(0),
                    reset,
                    first_at + timedelta(minutes=15),
                ),
                Schedule(),
                Thresholds(),
                "UTC",
            )
            crossing = store.commit(
                Observation(
                    "user",
                    "workspace",
                    Decimal(100),
                    Decimal(90),
                    reset,
                    first_at + timedelta(minutes=30),
                ),
                Schedule(),
                Thresholds(),
                "UTC",
            )
            self.assertTrue(crossing.notify)
            self.assertEqual(len(store.observations(initial.window.id)), 3)
            store.close()

    def test_delete_all_removes_windows(self):
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite")
            observed = datetime(2025, 1, 6, tzinfo=timezone.utc)
            item = Observation(
                "u",
                "w",
                Decimal(100),
                Decimal(1),
                datetime(2025, 2, 1, tzinfo=timezone.utc),
                observed,
            )
            store.commit(item, Schedule(), Thresholds(), "UTC")
            store.delete_all()
            self.assertEqual(store.windows(item.account_key), [])
            store.close()

    def test_plan_usage_preserves_the_exact_weekly_window(self):
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite")
            start = datetime(2025, 1, 6, tzinfo=timezone.utc)
            reset = start + timedelta(days=7)
            item = Observation(
                "u",
                "w",
                Decimal(100),
                Decimal(8),
                reset,
                start + timedelta(days=1),
                start,
                UsageMetric.PLAN_USAGE,
            )
            result = store.commit(item, Schedule(), Thresholds(), "UTC")
            restored = store.observations(result.window.id)[0]
            self.assertEqual(result.window.start, start)
            self.assertEqual(result.window.metric, UsageMetric.PLAN_USAGE)
            self.assertEqual(restored.metric, UsageMetric.PLAN_USAGE)
            self.assertEqual(restored.window_start, start)
            store.close()

    def test_mode_switch_preserves_observations_and_resets_alert_baseline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            store = HistoryStore(path)
            self.addCleanup(store.close)
            start = datetime(2025, 1, 10, 8, tzinfo=timezone.utc)
            at = start + timedelta(hours=9)
            end = start + timedelta(days=3, hours=9)
            item = Observation("u", "w", Decimal(100), Decimal(50), end, at, start)
            work = store.commit(item, Schedule(), Thresholds(), "UTC")
            self.assertEqual(work.evaluation.state, PaceState.ON_PACE)
            personal = Schedule(mode=UsageMode.PERSONAL)
            window = store.update_pacing(
                work.window.id, personal, Thresholds(), "Europe/Stockholm"
            )
            self.assertEqual(len(store.observations(window.id)), 1)
            self.assertEqual(window.timezone_name, "Europe/Stockholm")
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT usage_mode FROM windows").fetchone()[0],
                    "personal",
                )
            for minutes, used, notify in (
                (15, 50, False),
                (30, 0, False),
                (45, 90, True),
            ):
                with self.subTest(minutes=minutes):
                    result = store.commit(
                        Observation(
                            "u",
                            "w",
                            Decimal(100),
                            Decimal(used),
                            end,
                            at + timedelta(minutes=minutes),
                            start,
                        ),
                        personal,
                        Thresholds(),
                        "Europe/Stockholm",
                    )
                    self.assertEqual(result.window.id, window.id)
                    self.assertEqual(result.notify, notify)
            self.assertEqual(result.evaluation.state, PaceState.CRITICAL)
            restored = HistoryStore(path)
            self.addCleanup(restored.close)
            self.assertEqual(restored.window(window.id).schedule, personal)
            self.assertEqual(len(restored.observations(window.id)), 4)

    def test_mode_change_on_refresh_does_not_trigger_an_alert(self):
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite")
            self.addCleanup(store.close)
            start = datetime(2025, 1, 10, 8, tzinfo=timezone.utc)
            at = start + timedelta(hours=9)
            end = start + timedelta(days=3, hours=9)
            item = Observation("u", "w", Decimal(100), Decimal(50), end, at, start)
            store.commit(item, Schedule(), Thresholds(), "UTC")
            result = store.commit(
                item, Schedule(mode=UsageMode.PERSONAL), Thresholds(), "UTC"
            )
            self.assertEqual(result.evaluation.state, PaceState.CRITICAL)
            self.assertFalse(result.notify)

    def test_completed_windows_keep_their_mode(self):
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite")
            self.addCleanup(store.close)
            start = datetime(2025, 1, 6, tzinfo=timezone.utc)
            end = start + timedelta(days=7)
            old = store.commit(
                Observation("u", "w", Decimal(100), Decimal(10), end, start, start),
                Schedule(),
                Thresholds(),
                "UTC",
            )
            current = store.commit(
                Observation(
                    "u",
                    "w",
                    Decimal(100),
                    Decimal(10),
                    end + timedelta(days=7),
                    end,
                    end,
                ),
                Schedule(),
                Thresholds(),
                "UTC",
            )
            store.update_pacing(
                current.window.id,
                Schedule(mode=UsageMode.PERSONAL),
                Thresholds(),
                "UTC",
            )
            self.assertEqual(store.window(old.window.id).schedule.mode, UsageMode.WORK)
            self.assertEqual(
                store.window(current.window.id).schedule.mode, UsageMode.PERSONAL
            )
