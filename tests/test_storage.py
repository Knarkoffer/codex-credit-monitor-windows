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
)
from codex_credit_monitor_windows.storage import HistoryStore


class StorageTests(TestCase):
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
