from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from .domain import Observation, Schedule, pace_seconds


STALE_SECONDS = 30 * 60
LOOKBACK = timedelta(days=7)
MINIMUM_SAMPLES = 3
MINIMUM_SECONDS = 60 * 60


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


def estimate_usage(
    observations: list[Observation],
    window_start: datetime,
    schedule: Schedule,
    zone: ZoneInfo,
    at: datetime,
) -> UsageForecast:
    """Project the recent average consumption rate within one allowance period.

    Use actual readings, never an assumed zero at the period's start. Changes
    in allocation or a falling counter start a new trend. Sampling more often
    does not give those intervals extra weight: rate is net increase / time.
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
        and max(start, last_at - LOOKBACK)
        <= item.observed_at.astimezone(timezone.utc)
        <= last_at
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
    if len(samples) < MINIMUM_SAMPLES:
        return UsageForecast(ForecastState.WAITING)
    elapsed = pace_seconds(samples[0][0], last_at, schedule, zone)
    if elapsed < MINIMUM_SECONDS:
        return UsageForecast(ForecastState.WAITING)
    details = {"sample_count": len(samples), "elapsed_seconds": elapsed}
    increase = latest.used - samples[0][1].used
    if increase <= 0:
        return UsageForecast(ForecastState.FLAT, **details)
    elapsed_decimal = Decimal(str(elapsed))
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
