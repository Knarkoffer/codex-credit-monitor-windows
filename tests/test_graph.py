from datetime import datetime
from decimal import Decimal
from unittest import TestCase
from zoneinfo import ZoneInfo

from codex_credit_monitor_windows.domain import Observation
from codex_credit_monitor_windows.graph import daily_observations


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
