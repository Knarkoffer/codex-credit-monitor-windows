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
            connection.close()
            store = HistoryStore(path)
            columns = {
                row["name"]
                for row in store.connection.execute("PRAGMA table_info(windows)")
            }
            self.assertIn("metric", columns)
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
