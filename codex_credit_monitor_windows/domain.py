from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo


HUNDRED = Decimal("100")


class PaceState(str, Enum):
    BEHIND = "Behind"
    ON_PACE = "On pace"
    AHEAD = "Ahead"
    CRITICAL = "Critically ahead"
    UNAVAILABLE = "Unavailable"


@dataclass(frozen=True)
class Schedule:
    start_minutes: int = 8 * 60
    end_minutes: int = 17 * 60

    def __post_init__(self) -> None:
        if not 0 <= self.start_minutes < self.end_minutes <= 24 * 60:
            raise ValueError(
                "Working hours must end after they start and remain within one day."
            )


@dataclass(frozen=True)
class Thresholds:
    tolerance: Decimal = Decimal("5")
    critical_base: Decimal = Decimal("5")
    critical_growth: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        if (
            self.tolerance < 0
            or self.critical_base < self.tolerance
            or self.critical_growth < 0
        ):
            raise ValueError("Thresholds must satisfy A >= 0, B >= A, and K >= 0.")


@dataclass(frozen=True)
class Observation:
    user_id: str
    workspace_id: str
    limit: Decimal
    used: Decimal
    reset_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.user_id.strip() or not self.workspace_id.strip():
            raise ValueError("The response did not identify a user and workspace.")
        if not self.limit.is_finite() or self.limit <= 0 or not self.used.is_finite():
            raise ValueError("The response contains invalid credit values.")
        if self.reset_at.tzinfo is None or self.observed_at.tzinfo is None:
            raise ValueError("Observation timestamps must include a timezone.")

    @property
    def account_key(self) -> str:
        return f"{len(self.user_id)}:{self.user_id}|{len(self.workspace_id)}:{self.workspace_id}"


@dataclass(frozen=True)
class Evaluation:
    state: PaceState
    difference: Decimal
    actual_remaining_percent: Decimal
    guide_remaining_percent: Decimal | None
    remaining_working_percent: Decimal | None
    critical_threshold: Decimal | None


def previous_calendar_month(value: datetime, zone: ZoneInfo) -> datetime:
    local = value.astimezone(zone)
    year, month = local.year, local.month - 1
    if month == 0:
        year, month = year - 1, 12
    return local.replace(
        year=year, month=month, day=min(local.day, calendar.monthrange(year, month)[1])
    )


def working_seconds(
    start: datetime, end: datetime, schedule: Schedule, zone: ZoneInfo
) -> float:
    if end <= start:
        return 0.0
    cursor, last, total = (
        start.astimezone(zone).date(),
        end.astimezone(zone).date(),
        0.0,
    )
    while cursor <= last:
        if cursor.weekday() < 5:
            day_start, day_end = _at_minutes(
                cursor, schedule.start_minutes, zone
            ), _at_minutes(cursor, schedule.end_minutes, zone)
            overlap_start, overlap_end = max(start, day_start), min(end, day_end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds()
        cursor += timedelta(days=1)
    return total


def working_guide_points(
    start: datetime, end: datetime, schedule: Schedule, zone: ZoneInfo
) -> list[tuple[datetime, Decimal]]:
    total = working_seconds(start, end, schedule, zone)
    if total <= 0 or end <= start:
        return []
    points = {start, end}
    cursor, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    while cursor <= last:
        if cursor.weekday() < 5:
            for minute in (schedule.start_minutes, schedule.end_minutes):
                boundary = _at_minutes(cursor, minute, zone)
                if start < boundary < end:
                    points.add(boundary)
        cursor += timedelta(days=1)
    return [
        (
            point,
            HUNDRED
            * Decimal(str(working_seconds(start, point, schedule, zone) / total)),
        )
        for point in sorted(points)
    ]


def evaluate(
    observation: Observation,
    at: datetime,
    window_start: datetime,
    schedule: Schedule,
    thresholds: Thresholds,
    zone: ZoneInfo,
) -> Evaluation:
    actual_remaining = (
        (observation.limit - observation.used) / observation.limit * HUNDRED
    )
    total = working_seconds(window_start, observation.reset_at, schedule, zone)
    if total <= 0:
        return Evaluation(
            PaceState.UNAVAILABLE, Decimal(0), actual_remaining, None, None, None
        )
    clamped = min(max(at, window_start), observation.reset_at)
    elapsed_fraction = Decimal(
        str(working_seconds(window_start, clamped, schedule, zone) / total)
    )
    remaining = max(Decimal(0), min(HUNDRED, HUNDRED * (Decimal(1) - elapsed_fraction)))
    guide_remaining = HUNDRED * (Decimal(1) - elapsed_fraction)
    difference = guide_remaining - actual_remaining
    critical = (
        thresholds.critical_base + thresholds.critical_growth * remaining / HUNDRED
    )
    state = (
        PaceState.BEHIND
        if difference < -thresholds.tolerance
        else (
            PaceState.ON_PACE
            if difference <= thresholds.tolerance
            else PaceState.CRITICAL if difference >= critical else PaceState.AHEAD
        )
    )
    return Evaluation(
        state, difference, actual_remaining, guide_remaining, remaining, critical
    )


def _at_minutes(day: date, minutes: int, zone: ZoneInfo) -> datetime:
    if minutes == 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, zone)
    return datetime.combine(day, time(minutes // 60, minutes % 60), zone)
