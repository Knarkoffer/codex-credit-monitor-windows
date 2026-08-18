from datetime import datetime
from decimal import Decimal
from unittest import TestCase
from zoneinfo import ZoneInfo

from codex_credit_monitor_windows.domain import (
    Observation,
    PaceState,
    Schedule,
    Thresholds,
    evaluate,
    working_guide_points,
    working_seconds,
)


UTC = ZoneInfo("UTC")


class WorkingTimeTests(TestCase):
    def test_weekday_work_and_remaining_percent(self):
        start = datetime(2025, 1, 6, 8, tzinfo=UTC)
        midpoint = datetime(2025, 1, 8, 12, 30, tzinfo=UTC)
        end = datetime(2025, 1, 10, 17, tzinfo=UTC)
        self.assertEqual(working_seconds(start, end, Schedule(), UTC), 45 * 60 * 60)
        result = evaluate(
            Observation("user", "workspace", Decimal(100), Decimal(50), end, midpoint),
            midpoint,
            start,
            Schedule(),
            Thresholds(),
            UTC,
        )
        self.assertAlmostEqual(float(result.remaining_working_percent), 50.0)
        self.assertEqual(result.state, PaceState.ON_PACE)

    def test_graph_guide_is_flat_over_a_weekend(self):
        start, end = datetime(2025, 1, 10, 8, tzinfo=UTC), datetime(
            2025, 1, 13, 17, tzinfo=UTC
        )
        self.assertEqual(
            [value for _, value in working_guide_points(start, end, Schedule(), UTC)],
            [Decimal(0), Decimal(50), Decimal(50), Decimal(100)],
        )

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            Thresholds(Decimal(6), Decimal(5), Decimal(10))
