from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from .domain import Observation, Schedule, pace_guide_points, pace_seconds


STALE_SECONDS = 30 * 60


class ForecastState(str, Enum):
    WAITING = "waiting"
    STALE = "stale"
    RESET = "reset"
    EXHAUSTED = "exhausted"
    FLAT = "flat"
    RUNS_OUT = "runs_out"
    LASTS_UNTIL_RESET = "lasts_until_reset"


@dataclass(frozen=True)
class UsageForecast:
    state: ForecastState
    exhaustion_at: datetime | None = None
    sample_count: int = 0
    elapsed_seconds: float = 0
    usage_per_second: Decimal | None = None
    observed_days: int = 0

    def runs_out_early(self, window_start: datetime, reset_at: datetime) -> bool:
        """Allow a grace margin of 5% of the full calendar period before reset."""
        if self.state is not ForecastState.RUNS_OUT or self.exhaustion_at is None:
            return False
        start = window_start.astimezone(timezone.utc)
        end = reset_at.astimezone(timezone.utc)
        return end > start and (
            end - self.exhaustion_at.astimezone(timezone.utc) > (end - start) / 20
        )


def estimate_usage(
    observations: list[Observation],
    window_start: datetime,
    schedule: Schedule,
    zone: ZoneInfo,
    at: datetime,
) -> UsageForecast:
    """Project consumption over the last two local days with readings.

    Use actual readings, never an assumed zero at the period's start. Changes
    in allocation or a falling counter restart the history. Attribute increases
    to the day they are observed, retaining a baseline before the oldest selected
    day's first reading. Include idle time through the latest reading so reducing
    activity slows the rate. Days with unchanged usage count too. Use the
    available history if fewer than two days have readings.
    Work mode measures and projects weekday working time; Personal uses UTC
    elapsed time. A reset bounds every projection.
    """
    if not observations:
        return UsageForecast(ForecastState.WAITING)
    at = at.astimezone(timezone.utc)
    latest = sorted(
        observations, key=lambda item: item.observed_at.astimezone(timezone.utc)
    )[-1]
    end = latest.reset_at.astimezone(timezone.utc)
    last_at = latest.observed_at.astimezone(timezone.utc)
    start = window_start.astimezone(timezone.utc)
    if at >= end:
        return UsageForecast(ForecastState.RESET)
    if last_at > at or last_at < start or latest.used < 0:
        return UsageForecast(ForecastState.WAITING)
    if (at - last_at).total_seconds() >= STALE_SECONDS:
        return UsageForecast(ForecastState.STALE)
    if latest.used >= latest.limit:
        return UsageForecast(ForecastState.EXHAUSTED)

    # Equal-time refreshes count once; retain the last recorded value.
    unique = {
        item.observed_at.astimezone(timezone.utc): item
        for item in observations
        if item.account_key == latest.account_key
        and item.metric is latest.metric
        and item.reset_at == latest.reset_at
        and item.window_start == latest.window_start
        and start <= item.observed_at.astimezone(timezone.utc) <= last_at
    }
    samples: list[tuple[datetime, Observation]] = []
    for observed_at, item in sorted(unique.items()):
        if samples and (
            item.limit != samples[-1][1].limit or item.used < samples[-1][1].used
        ):
            samples.clear()
        if item.used < 0:
            samples.clear()
            continue
        samples.append((observed_at, item))
    if len(samples) < 2:
        return UsageForecast(ForecastState.WAITING)
    observed_days = sorted(
        {observed_at.astimezone(zone).date() for observed_at, _ in samples}
    )[-2:]
    if observed_days:
        first_index = next(
            index
            for index, (observed_at, _) in enumerate(samples)
            if observed_at.astimezone(zone).date() >= observed_days[0]
        )
        # The preceding reading supplies a measured baseline, not an assumed
        # zero or an invented midnight value. Keep later idle readings too.
        samples = samples[max(0, first_index - 1) :]
    elapsed = pace_seconds(samples[0][0], last_at, schedule, zone)
    if elapsed <= 0:
        return UsageForecast(ForecastState.WAITING)
    increase = latest.used - samples[0][1].used
    elapsed_decimal = Decimal(str(elapsed))
    details = {
        "sample_count": len(samples),
        "elapsed_seconds": elapsed,
        "usage_per_second": increase / elapsed_decimal,
        "observed_days": len(observed_days),
    }
    if increase <= 0:
        return UsageForecast(ForecastState.FLAT, **details)
    remaining = latest.limit - latest.used
    available = pace_seconds(last_at, end, schedule, zone)
    if remaining * elapsed_decimal >= increase * Decimal(str(available)):
        return UsageForecast(ForecastState.LASTS_UNTIL_RESET, **details)

    # Invert scheduled elapsed time in UTC, including nights, weekends and DST.
    # The search is bounded by the reset, so very slow trends cannot overflow.
    needed = float(remaining * elapsed_decimal / increase)
    lower, upper = last_at, end
    while (upper - lower).total_seconds() > 1:
        middle = lower + (upper - lower) / 2
        if pace_seconds(last_at, middle, schedule, zone) >= needed:
            upper = middle
        else:
            lower = middle
    return UsageForecast(ForecastState.RUNS_OUT, upper, **details)


def forecast_points(
    observations: list[Observation],
    window_start: datetime,
    schedule: Schedule,
    zone: ZoneInfo,
    at: datetime,
) -> list[tuple[datetime, Decimal]]:
    """Return projected usage percentages, including flat non-working hours."""
    forecast = estimate_usage(observations, window_start, schedule, zone, at)
    if forecast.usage_per_second is None:
        return []
    latest = sorted(
        observations, key=lambda item: item.observed_at.astimezone(timezone.utc)
    )[-1]
    start = latest.observed_at.astimezone(timezone.utc)
    end = forecast.exhaustion_at or latest.reset_at.astimezone(timezone.utc)
    elapsed = Decimal(str(pace_seconds(start, end, schedule, zone)))
    guide = pace_guide_points(start, end, schedule, zone) or [
        (start, Decimal(0)),
        (end, Decimal(0)),
    ]
    return [
        (
            point,
            min(
                Decimal(100),
                (latest.used + forecast.usage_per_second * elapsed * percent / 100)
                / latest.limit
                * 100,
            ),
        )
        for point, percent in guide
    ]
