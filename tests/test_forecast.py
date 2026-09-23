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
from codex_credit_monitor_windows.forecast import (
    ForecastState,
    estimate_usage,
    forecast_points,
)


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
        self.assertEqual(result.observed_days, 1)

    def test_extra_readings_do_not_change_the_average_rate(self):
        sparse = [self.reading(0, 0), self.reading(1, 30), self.reading(4, 40)]
        dense = sparse + [self.reading(0.25, 10), self.reading(0.5, 25)]
        self.assertEqual(
            self.estimate(sparse).exhaustion_at, self.estimate(dense).exhaustion_at
        )

    def test_cutting_back_gradually_delays_the_forecast(self):
        readings = [self.reading(0, 0), self.reading(0.25, 50)]
        fast = self.estimate(readings)
        slow = self.estimate(readings + [self.reading(0.5, 51)])
        self.assertEqual(fast.state, ForecastState.RUNS_OUT)
        self.assertEqual(slow.state, ForecastState.RUNS_OUT)
        self.assertGreater(slow.exhaustion_at, fast.exhaustion_at)
        self.assertLess(
            abs(
                (
                    slow.exhaustion_at - (self.start + timedelta(hours=100 / 102))
                ).total_seconds()
            ),
            1,
        )
        self.assertEqual(slow.sample_count, 3)
        self.assertEqual(slow.elapsed_seconds, 1800)

    def test_latest_flat_interval_slows_but_preserves_the_daily_trend(self):
        result = self.estimate(
            [self.reading(0, 0), self.reading(0.25, 50), self.reading(0.5, 50)]
        )
        self.assertEqual(result.state, ForecastState.RUNS_OUT)
        self.assertEqual(result.usage_per_second, Decimal(50) / 1800)

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

    def test_requires_two_distinct_readings(self):
        for readings in (
            [self.reading(0, 0)],
            [self.reading(0, 0), self.reading(0, 1)],
        ):
            with self.subTest(readings=readings):
                self.assertEqual(self.estimate(readings).state, ForecastState.WAITING)
        self.assertEqual(self.estimate([], at=self.start).state, ForecastState.WAITING)

    def test_work_mode_requires_some_working_time_in_the_selected_history(self):
        readings = [self.reading(10, 20), self.reading(10.25, 25)]
        self.assertEqual(
            self.estimate(readings, schedule=Schedule()).state, ForecastState.WAITING
        )
        self.assertEqual(self.estimate(readings).state, ForecastState.RUNS_OUT)
        self.assertEqual(
            self.estimate([self.reading(0, 0)] + readings, schedule=Schedule()).state,
            ForecastState.RUNS_OUT,
        )

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
        ]
        self.assertEqual(self.estimate(resumed).sample_count, 2)
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
        current = [self.reading(2, 20)]
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

    def test_only_last_two_observed_days_contribute_in_a_monthly_period(self):
        self.end = self.start + timedelta(days=30)
        readings = [
            self.reading(0, 0),
            self.reading(10, 40),  # Monday's heavy usage, retained only as baseline.
            self.reading(16, 40),  # Tuesday midnight.
            self.reading(24, 44),
            self.reading(40, 44),  # Wednesday midnight.
            self.reading(48, 48),
        ]
        result = self.estimate(readings)
        self.assertEqual(result.sample_count, 5)
        self.assertEqual(result.observed_days, 2)
        self.assertEqual(result.elapsed_seconds, 38 * 3600)
        self.assertEqual(result.usage_per_second, Decimal(8) / (38 * 3600))
        self.assertLess(
            abs(
                (
                    result.exhaustion_at - (self.start + timedelta(hours=295))
                ).total_seconds()
            ),
            1,
        )

    def test_observed_activity_can_span_more_than_seven_days(self):
        self.end = self.start + timedelta(days=30)
        result = self.estimate([self.reading(0, 10), self.reading(24 * 10, 60)])
        self.assertEqual(result.state, ForecastState.RUNS_OUT)
        self.assertLess(
            abs(
                (
                    result.exhaustion_at - (self.start + timedelta(days=18))
                ).total_seconds()
            ),
            1,
        )

    def test_idle_days_between_and_after_activity_lower_the_rate(self):
        self.end = self.start + timedelta(days=30)
        readings = [
            self.reading(0, 0),
            self.reading(1, 20),  # Monday active.
            self.reading(24, 20),  # Tuesday idle.
            self.reading(48, 30),  # Wednesday active.
        ]
        before = self.estimate(readings)
        after = self.estimate(readings + [self.reading(72, 30)])  # Thursday idle.
        self.assertEqual(after.observed_days, 2)
        self.assertEqual(before.usage_per_second, Decimal(10) / (47 * 3600))
        self.assertEqual(after.usage_per_second, Decimal(10) / (48 * 3600))
        self.assertGreater(after.exhaustion_at, before.exhaustion_at)

    def test_observed_days_use_the_period_timezone(self):
        self.start = datetime(2025, 1, 6, 20, tzinfo=UTC)
        self.end = self.start + timedelta(days=7)
        readings = [
            self.reading(0, 0),
            self.reading(1, 10),
            self.reading(3, 20),  # Tuesday midnight in Stockholm, still Monday UTC.
            self.reading(5, 30),
            self.reading(27, 40),  # Wednesday midnight in Stockholm, Tuesday UTC.
        ]
        utc = self.estimate(readings)
        local = self.estimate(readings, zone=ZoneInfo("Europe/Stockholm"))
        self.assertEqual(utc.usage_per_second, Decimal(40) / (27 * 3600))
        self.assertEqual(local.usage_per_second, Decimal(30) / (26 * 3600))
        self.assertEqual(local.observed_days, 2)

    def test_quiet_refresh_days_replace_old_busy_days(self):
        self.end = self.start + timedelta(days=30)
        for schedule, hours in ((self.personal, 48), (Schedule(), 18)):
            for extra in (Decimal(0), Decimal("0.1")):
                with self.subTest(mode=schedule.mode, extra=extra):
                    readings = [
                        self.reading(0, 0),
                        self.reading(1, 90),  # Monday's busy session.
                        self.reading(9, 90),  # Monday's closing baseline.
                        self.reading(24, 90),  # Tuesday refresh, no usage.
                        self.reading(33, 90 + extra),
                        self.reading(48, 90 + extra),  # Wednesday refresh.
                        self.reading(57, 90 + extra * 2),
                    ]
                    result = self.estimate(readings, schedule=schedule)
                    self.assertEqual(result.observed_days, 2)
                    self.assertEqual(result.sample_count, 5)
                    self.assertEqual(result.elapsed_seconds, hours * 3600)
                    self.assertEqual(
                        result.usage_per_second, extra * 2 / (hours * 3600)
                    )
                    self.assertEqual(
                        result.state,
                        (
                            ForecastState.FLAT
                            if extra == 0
                            else ForecastState.LASTS_UNTIL_RESET
                        ),
                    )
                    self.assertIsNone(result.exhaustion_at)
                    if extra == 0:
                        points = forecast_points(
                            readings,
                            self.start,
                            schedule,
                            UTC,
                            readings[-1].observed_at,
                        )
                        self.assertTrue(points)
                        self.assertTrue(all(percent == 90 for _, percent in points))

    def test_counter_correction_clears_older_observed_days(self):
        readings = [
            self.reading(0, 0),
            self.reading(1, 20),
            self.reading(24, 40),
            self.reading(48, 10),
            self.reading(49, 12),
        ]
        result = self.estimate(readings)
        self.assertEqual(result.observed_days, 1)
        self.assertEqual(result.sample_count, 2)
        self.assertEqual(result.usage_per_second, Decimal(2) / 3600)

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

    def test_graph_projection_matches_run_out_time_and_latest_reading(self):
        readings = [self.reading(0, 0), self.reading(0.25, 50), self.reading(0.5, 51)]
        points = forecast_points(
            readings, self.start, self.personal, UTC, readings[-1].observed_at
        )
        self.assertEqual(points[0], (readings[-1].observed_at, Decimal(51)))
        self.assertEqual(
            points[-1], (self.estimate(readings).exhaustion_at, Decimal(100))
        )

    def test_graph_projection_reaches_reset_below_limit_or_flat(self):
        self.end = self.start + timedelta(hours=4)
        for used, expected in ((11, 14), (10, 10)):
            with self.subTest(used=used):
                readings = [self.reading(0, 10), self.reading(1, used)]
                points = forecast_points(
                    readings, self.start, self.personal, UTC, readings[-1].observed_at
                )
                self.assertEqual(points[-1], (self.end, Decimal(expected)))

    def test_graph_projection_pauses_over_weekend(self):
        self.start = datetime(2025, 1, 10, 14, tzinfo=UTC)
        self.end = self.start + timedelta(days=7)
        readings = [self.reading(0, 20), self.reading(1, 30), self.reading(2, 40)]
        points = dict(
            forecast_points(
                readings, self.start, Schedule(), UTC, readings[-1].observed_at
            )
        )
        self.assertAlmostEqual(
            points[datetime(2025, 1, 10, 17, tzinfo=UTC)], Decimal(50)
        )
        self.assertAlmostEqual(
            points[datetime(2025, 1, 13, 8, tzinfo=UTC)], Decimal(50)
        )

    def test_graph_projection_is_hidden_without_a_usable_forecast(self):
        for readings, at in (
            ([self.reading(0, 10)], self.start),
            (
                [self.reading(0, 10), self.reading(1, 20)],
                self.start + timedelta(hours=1.5),
            ),
            ([self.reading(0, 10), self.reading(1, 20)], self.end),
            (
                [self.reading(0, 10), self.reading(1, 100)],
                self.start + timedelta(hours=1),
            ),
            (
                [self.reading(0, 10), self.reading(1, 5)],
                self.start + timedelta(hours=1),
            ),
        ):
            with self.subTest(readings=readings, at=at):
                self.assertEqual(
                    forecast_points(readings, self.start, self.personal, UTC, at), []
                )
