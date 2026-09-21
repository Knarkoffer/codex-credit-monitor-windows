from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from codex_credit_monitor_windows.domain import Observation, Schedule, UsageMode
from codex_credit_monitor_windows.graph import USAGE, UsageGraph, daily_observations
from codex_credit_monitor_windows.storage import Window


UTC = ZoneInfo("UTC")


def observation(at: datetime, used: str) -> Observation:
    return Observation(
        "user",
        "workspace",
        Decimal("100"),
        Decimal(used),
        datetime(2025, 2, 1, tzinfo=UTC),
        at,
    )


class DailyObservationTests(TestCase):
    def test_graph_uses_only_the_latest_observation_for_each_local_day(self):
        earlier = observation(datetime(2025, 1, 8, 9, tzinfo=UTC), "20")
        latest = observation(datetime(2025, 1, 8, 17, tzinfo=UTC), "30")
        next_day = observation(datetime(2025, 1, 9, 9, tzinfo=UTC), "40")

        self.assertEqual(
            daily_observations([next_day, earlier, latest], UTC), [latest, next_day]
        )


class GraphWindowTests(TestCase):
    def setUp(self):
        self.start = datetime(2025, 1, 8, tzinfo=UTC)
        self.end = self.start + timedelta(days=7)
        self.graph = Mock(spec=UsageGraph)
        self.graph.winfo_width.return_value = 520
        self.graph.winfo_height.return_value = 300
        self.graph.window_data = Window(
            1,
            "account",
            self.start,
            self.end,
            "UTC",
            Schedule(mode=UsageMode.PERSONAL),
        )

    def usage_lines(self):
        return [
            call
            for call in self.graph.create_line.call_args_list
            if call.kwargs.get("fill") == USAGE
        ]

    def test_old_readings_are_not_clamped_onto_the_start_of_the_graph(self):
        old = observation(self.start - timedelta(days=1), "19")
        first = observation(self.start, "10")
        latest = observation(self.start + timedelta(days=1), "16")
        outside = replace(observation(self.end, "90"), limit=Decimal(200))
        self.graph.observations = [old, first, latest, outside]

        UsageGraph.draw(self.graph)

        self.graph.create_oval.assert_called_once()
        self.graph.create_rectangle.assert_called_once()
        lines = self.usage_lines()
        self.assertEqual(len(lines), 1)
        # A 520 x 300 canvas has a 448 x 224 plot starting at (54, 42).
        expected = (54, 42 + 224 * 0.9, 118, 42 + 224 * 0.84)
        for actual, value in zip(lines[0].args, expected):
            self.assertAlmostEqual(actual, value)

    def test_window_filter_runs_before_selecting_the_last_reading_of_each_day(self):
        self.graph.window_data = replace(
            self.graph.window_data,
            start=self.start + timedelta(hours=12),
            end=self.end + timedelta(hours=12),
        )
        valid = observation(self.end + timedelta(hours=11), "16")
        at_reset = observation(self.end + timedelta(hours=12), "0")
        after_reset = observation(self.end + timedelta(hours=13), "1")
        self.graph.observations = [valid, at_reset, after_reset]

        UsageGraph.draw(self.graph)

        self.graph.create_oval.assert_not_called()
        self.graph.create_rectangle.assert_called_once()
        _, top, _, bottom = self.graph.create_rectangle.call_args.args
        self.assertAlmostEqual((top + bottom) / 2, 42 + 224 * 0.84)

    def test_no_usage_is_drawn_when_all_readings_are_outside_the_window(self):
        self.graph.observations = [
            observation(self.start - timedelta(microseconds=1), "19"),
            observation(self.end, "0"),
        ]

        UsageGraph.draw(self.graph)

        self.assertEqual(self.usage_lines(), [])
        self.graph.create_oval.assert_not_called()
        self.graph.create_rectangle.assert_not_called()
        self.assertTrue(
            any(
                "history" in call.kwargs.get("text", "").lower()
                for call in self.graph.create_text.call_args_list
            )
        )

    def test_date_labels_use_the_window_timezone(self):
        window = replace(
            self.graph.window_data,
            timezone_name="America/New_York",
        )

        UsageGraph._draw_time_grid(
            self.graph,
            window,
            lambda at, _fraction: ((at - window.start).total_seconds(), 0),
            42,
            224,
        )

        labels = [
            call.kwargs.get("text") for call in self.graph.create_text.call_args_list
        ]
        self.assertIn("07 Jan", labels)

    def test_graph_fits_reduced_height_in_a_compact_window(self):
        self.graph.winfo_height.return_value = 200
        self.graph.observations = [observation(self.start, "10")]

        UsageGraph.draw(self.graph)

        # Leave room for the header and date labels inside the actual canvas.
        self.assertEqual(self.graph._draw_time_grid.call_args.args[2:], (42, 124))
        _, top, _, bottom = self.graph.create_rectangle.call_args.args
        self.assertGreaterEqual(top, 42)
        self.assertLess(bottom, 200 - 34)

    def test_dotted_forecast_uses_same_day_readings_and_expires_when_stale(self):
        self.graph.observations = [
            replace(observation(self.start, "20"), reset_at=self.end),
            replace(
                observation(self.start + timedelta(minutes=15), "30"),
                reset_at=self.end,
            ),
        ]
        with patch("codex_credit_monitor_windows.graph.datetime") as clock:
            clock.now.return_value = self.start + timedelta(minutes=15)
            UsageGraph.draw(self.graph)
            forecasts = [
                call
                for call in self.usage_lines()
                if call.kwargs.get("tags") == "forecast"
            ]
            self.assertEqual(len(forecasts), 1)
            self.assertEqual(forecasts[0].kwargs["dash"], (2, 5))
            start_x, start_y, end_x, end_y = forecasts[0].args
            self.assertAlmostEqual(start_x, 54 + 448 * 0.25 / 168)
            self.assertAlmostEqual(start_y, 42 + 224 * 0.7)
            self.assertAlmostEqual(end_x, 54 + 448 * 2 / 168, places=3)
            self.assertEqual(end_y, 42)

            clock.now.return_value += timedelta(minutes=30)
            self.graph.create_line.reset_mock()
            UsageGraph.draw(self.graph)
            self.assertFalse(
                any(
                    call.kwargs.get("tags") == "forecast" for call in self.usage_lines()
                )
            )
