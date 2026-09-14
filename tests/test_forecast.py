from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase
from zoneinfo import ZoneInfo

from codex_credit_monitor_windows.domain import (
    Observation,
    Schedule,
    UsageMetric,
    UsageMode,
)
from codex_credit_monitor_windows.forecast import ForecastState, estimate_usage


UTC = ZoneInfo("UTC")


class ForecastTests(TestCase):
    def setUp(self):
        self.start = datetime(2025, 1, 6, 8, tzinfo=UTC)
        self.end = self.start + timedelta(days=7)
        self.personal = Schedule(mode=UsageMode.PERSONAL)

    def reading(self, hours, used, **changes):
        return replace(
            Observation(
                "user",
                "workspace",
                Decimal(100),
                Decimal(used),
                self.end,
                self.start + timedelta(hours=hours),
                self.start,
            ),
            **changes,
        )

    def estimate(self, readings, schedule=None, at=None, zone=UTC):
        return estimate_usage(
            readings,
            self.start,
            schedule or self.personal,
            zone,
            at or max(item.observed_at for item in readings),
        )

    def test_predicts_from_observed_increase_without_assuming_zero_at_start(self):
        readings = [self.reading(10, 40), self.reading(11, 50), self.reading(12, 60)]
        result = self.estimate(readings)
        self.assertEqual(result.state, ForecastState.RUNS_OUT)
        self.assertLess(
            abs(
                (
                    result.exhaustion_at - (self.start + timedelta(hours=16))
                ).total_seconds()
            ),
            1,
        )
        self.assertEqual(result.sample_count, 3)
        self.assertEqual(result.elapsed_seconds, 7200)

    def test_irregular_sampling_does_not_change_the_average_rate(self):
        sparse = [self.reading(0, 0), self.reading(1, 30), self.reading(4, 40)]
        dense = sparse + [self.reading(1.01, 30), self.reading(1.02, 30)]
        self.assertEqual(
            self.estimate(sparse).exhaustion_at, self.estimate(dense).exhaustion_at
        )

    def test_credits_and_plan_percentages_use_the_same_projection(self):
        plan = [
            self.reading(i, 10 * i, metric=UsageMetric.PLAN_USAGE) for i in range(3)
        ]
        credits = [
            replace(
                item,
                metric=UsageMetric.CREDITS,
                limit=item.limit * 20,
                used=item.used * 20,
            )
            for item in plan
        ]
        self.assertEqual(
            self.estimate(plan).exhaustion_at, self.estimate(credits).exhaustion_at
        )

    def test_requires_three_distinct_readings_and_one_hour(self):
        for readings in (
            [self.reading(0, 0)],
            [self.reading(0, 0), self.reading(2, 20)],
            [self.reading(0, 0), self.reading(0, 1), self.reading(2, 20)],
            [self.reading(0, 0), self.reading(0.25, 5), self.reading(0.5, 10)],
        ):
            with self.subTest(readings=readings):
                self.assertEqual(self.estimate(readings).state, ForecastState.WAITING)
        self.assertEqual(self.estimate([], at=self.start).state, ForecastState.WAITING)

    def test_latest_duplicate_reading_wins(self):
        result = self.estimate(
            [
                self.reading(0, 0),
                self.reading(1, 10),
                self.reading(2, 20),
                self.reading(2, 100),
            ]
        )
        self.assertEqual(result.state, ForecastState.EXHAUSTED)

    def test_stale_expired_future_and_exhausted_readings(self):
        readings = [self.reading(i, i * 10) for i in range(3)]
        for at, state in (
            (self.start + timedelta(hours=2, minutes=30), ForecastState.STALE),
            (self.end, ForecastState.RESET),
            (self.start, ForecastState.WAITING),
        ):
            with self.subTest(state=state):
                self.assertEqual(self.estimate(readings, at=at).state, state)
        for used in (100, 105):
            self.assertEqual(
                self.estimate([self.reading(2, used)]).state, ForecastState.EXHAUSTED
            )

    def test_flat_usage_has_no_invented_run_out_date(self):
        result = self.estimate([self.reading(i, 25) for i in range(3)])
        self.assertEqual(result.state, ForecastState.FLAT)
        self.assertIsNone(result.exhaustion_at)

    def test_usage_decrease_and_allocation_change_restart_history(self):
        initial = [self.reading(0, 20), self.reading(1, 30), self.reading(2, 40)]
        for reading in (self.reading(3, 10), self.reading(3, 50, limit=Decimal(200))):
            with self.subTest(reading=reading):
                self.assertEqual(
                    self.estimate(initial + [reading]).state, ForecastState.WAITING
                )
        resumed = initial + [
            self.reading(3, 10),
            self.reading(4, 20),
            self.reading(5, 30),
        ]
        self.assertEqual(self.estimate(resumed).sample_count, 3)
        self.assertLess(
            abs(
                (
                    self.estimate(resumed).exhaustion_at
                    - (self.start + timedelta(hours=12))
                ).total_seconds()
            ),
            1,
        )

    def test_forecast_never_extends_beyond_reset_even_for_tiny_rates(self):
        for increment in (Decimal(".1"), Decimal("1e-100")):
            result = self.estimate([self.reading(i, increment * i) for i in range(3)])
            self.assertEqual(result.state, ForecastState.LASTS_UNTIL_RESET)
            self.assertIsNone(result.exhaustion_at)
        self.end = self.start + timedelta(hours=10)
        result = self.estimate([self.reading(i, i * 10) for i in range(3)])
        self.assertEqual(result.state, ForecastState.LASTS_UNTIL_RESET)

    def test_ignores_other_accounts_metrics_periods_and_out_of_window_samples(self):
        current = [self.reading(1, 10), self.reading(2, 20)]
        incompatible = (
            self.reading(0, 0, user_id="other"),
            self.reading(0, 0, workspace_id="other"),
            self.reading(0, 0, metric=UsageMetric.PLAN_USAGE),
            self.reading(0, 0, reset_at=self.end - timedelta(days=1)),
            self.reading(0, 0, window_start=self.start - timedelta(days=1)),
            self.reading(-1, 0),
        )
        for item in incompatible:
            with self.subTest(item=item):
                self.assertEqual(
                    self.estimate([item] + current).state, ForecastState.WAITING
                )

    def test_only_last_seven_days_contribute(self):
        self.end = self.start + timedelta(days=30)
        readings = [
            self.reading(0, 0),
            self.reading(24 * 10, 50),
            self.reading(24 * 10 + 1, 51),
            self.reading(24 * 10 + 2, 52),
        ]
        result = self.estimate(readings)
        self.assertEqual(result.sample_count, 3)
        self.assertLess(
            abs(
                (
                    result.exhaustion_at - (self.start + timedelta(hours=24 * 10 + 50))
                ).total_seconds()
            ),
            1,
        )

    def test_work_projection_skips_nights_and_weekends(self):
        self.start = datetime(2025, 1, 10, 14, tzinfo=UTC)  # Friday
        self.end = self.start + timedelta(days=7)
        readings = [self.reading(0, 20), self.reading(1, 30), self.reading(2, 40)]
        result = self.estimate(readings, schedule=Schedule())
        expected = datetime(2025, 1, 13, 13, tzinfo=UTC)  # Monday
        self.assertLess(abs((result.exhaustion_at - expected).total_seconds()), 1)
        personal = self.estimate(readings)
        self.assertLess(
            abs(
                (
                    personal.exhaustion_at - datetime(2025, 1, 10, 22, tzinfo=UTC)
                ).total_seconds()
            ),
            1,
        )

    def test_no_working_time_before_reset_means_allowance_lasts_until_reset(self):
        self.start = datetime(2025, 1, 10, 15, tzinfo=UTC)
        self.end = datetime(2025, 1, 12, 15, tzinfo=UTC)
        self.assertEqual(
            self.estimate(
                [self.reading(i, i * 10) for i in range(3)], schedule=Schedule()
            ).state,
            ForecastState.LASTS_UNTIL_RESET,
        )

    def test_personal_forecast_uses_elapsed_seconds_across_dst(self):
        zone = ZoneInfo("Europe/Stockholm")
        for month, day in ((3, 30), (10, 26)):
            with self.subTest(month=month):
                self.start = datetime(2025, month, day, tzinfo=zone).astimezone(
                    timezone.utc
                )
                self.end = self.start + timedelta(days=7)
                readings = [self.reading(i, 20 * i) for i in range(3)]
                result = self.estimate(readings, zone=zone)
                self.assertLess(
                    abs(
                        (
                            result.exhaustion_at - (self.start + timedelta(hours=5))
                        ).total_seconds()
                    ),
                    1,
                )

    def test_work_projection_uses_local_hours_after_dst_change(self):
        zone = ZoneInfo("Europe/Stockholm")
        self.start = datetime(2025, 3, 28, 14, tzinfo=zone).astimezone(timezone.utc)
        self.end = self.start + timedelta(days=7)
        result = self.estimate(
            [self.reading(i, 20 + i * 10) for i in range(3)],
            schedule=Schedule(),
            zone=zone,
        )
        expected = datetime(2025, 3, 31, 13, tzinfo=zone).astimezone(timezone.utc)
        self.assertLess(abs((result.exhaustion_at - expected).total_seconds()), 1)
