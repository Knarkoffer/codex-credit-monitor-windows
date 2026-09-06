from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase
from zoneinfo import ZoneInfo

from codex_credit_monitor_windows.domain import (
    Observation,
    PaceState,
    Schedule,
    Thresholds,
    UsageMode,
    evaluate,
    pace_guide_points,
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
        self.assertAlmostEqual(float(result.remaining_time_percent), 50.0)
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


class PersonalTimeTests(TestCase):
    def evaluation(self, start, end, at, mode=UsageMode.PERSONAL):
        return evaluate(
            Observation("u", "w", Decimal(100), Decimal(50), end, at),
            at,
            start,
            Schedule(mode=mode),
            Thresholds(),
            UTC,
        )

    def test_weekend_only_window_has_a_personal_pace(self):
        start = datetime(2025, 1, 11, tzinfo=UTC)
        end = start + timedelta(days=2)
        at = start + timedelta(days=1)
        personal = self.evaluation(start, end, at)
        self.assertEqual(personal.remaining_time_percent, 50)
        self.assertEqual(personal.state, PaceState.ON_PACE)
        self.assertEqual(
            self.evaluation(start, end, at, UsageMode.WORK).state, PaceState.UNAVAILABLE
        )

    def test_evening_window_is_independent_of_working_hours(self):
        start = datetime(2025, 1, 6, 18, tzinfo=UTC)
        end = start + timedelta(hours=5)
        result = self.evaluation(start, end, start + timedelta(hours=2, minutes=30))
        self.assertEqual(result.state, PaceState.ON_PACE)
        self.assertEqual(result.remaining_time_percent, 50)

    def test_weekly_window_uses_all_seven_days(self):
        start = datetime(2025, 1, 6, tzinfo=UTC)
        end = start + timedelta(days=7)
        result = self.evaluation(start, end, start + timedelta(days=3, hours=12))
        self.assertEqual(result.remaining_time_percent, 50)

    def test_elapsed_time_clamps_to_window_boundaries(self):
        start = datetime(2025, 1, 6, tzinfo=UTC)
        end = start + timedelta(days=7)
        for at, remaining in (
            (start - timedelta(days=1), 100),
            (end + timedelta(days=1), 0),
        ):
            with self.subTest(at=at):
                self.assertEqual(
                    self.evaluation(start, end, at).remaining_time_percent, remaining
                )

    def test_personal_guide_is_straight_through_weekend(self):
        start = datetime(2025, 1, 10, 8, tzinfo=UTC)
        end = start + timedelta(days=3)
        self.assertEqual(
            pace_guide_points(start, end, Schedule(mode=UsageMode.PERSONAL), UTC),
            [(start, Decimal(0)), (end, Decimal(100))],
        )
        self.assertEqual(
            pace_guide_points(end, start, Schedule(mode=UsageMode.PERSONAL), UTC), []
        )

    def test_dst_uses_actual_elapsed_seconds(self):
        zone = ZoneInfo("Europe/Stockholm")
        for month, day, hours in ((3, 30, 23), (10, 26, 25)):
            with self.subTest(month=month):
                start = datetime(2025, month, day, tzinfo=zone)
                end = start + timedelta(days=1)
                at = start.astimezone(timezone.utc) + timedelta(hours=hours / 2)
                self.assertEqual(
                    self.evaluation(start, end, at).remaining_time_percent, 50
                )
