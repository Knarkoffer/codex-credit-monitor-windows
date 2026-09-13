from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock
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
