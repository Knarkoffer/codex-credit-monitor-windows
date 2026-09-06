from __future__ import annotations

import tkinter as tk
from datetime import datetime
from zoneinfo import ZoneInfo

from .domain import Observation, UsageMode, pace_guide_points
from .storage import Window


BACKGROUND = "#111315"
GRID = "#383d42"
MUTED = "#9ba3ad"
TEXT = "#e7ebf0"
USAGE = "#2797ff"
GUIDE = "#35d56a"


def daily_observations(
    observations: list[Observation], zone: ZoneInfo
) -> list[Observation]:
    """Return the latest observation for each local calendar day."""
    latest_by_day: dict[object, Observation] = {}
    for observation in observations:
        day = observation.observed_at.astimezone(zone).date()
        previous = latest_by_day.get(day)
        if previous is None or observation.observed_at > previous.observed_at:
            latest_by_day[day] = observation
    return sorted(latest_by_day.values(), key=lambda item: item.observed_at)


class UsageGraph(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master, height=300, background=BACKGROUND, highlightthickness=0
        )
        self.window_data: Window | None = None
        self.observations: list[Observation] = []
        self.bind("<Configure>", lambda _: self.draw())

    def update(self, window: Window, observations: list[Observation]) -> None:
        self.window_data, self.observations = window, observations
        self.draw()

    def clear(self) -> None:
        self.window_data, self.observations = None, []
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 520), max(self.winfo_height(), 300)
        left, right, top, bottom = 54, 18, 42, 34
        plot_width, plot_height = width - left - right, height - top - bottom
        self._draw_header(left)
        self._draw_horizontal_grid(left, top, plot_width, plot_height)
        window = self.window_data
        if window is None or not self.observations or window.end <= window.start:
            self.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="Usage history will appear after a successful refresh.",
                fill=MUTED,
                anchor="center",
                font=("Segoe UI", 10),
            )
            return

        duration = (window.end - window.start).total_seconds()
        latest_limit = self.observations[-1].limit
        zone = ZoneInfo(window.timezone_name)

        def point(at: datetime, fraction: float) -> tuple[float, float]:
            x = left + plot_width * max(
                0, min(1, (at - window.start).total_seconds() / duration)
            )
            y = top + plot_height * (1 - max(0, min(1, fraction)))
            return x, y

        self._draw_time_grid(window, point, top, plot_height)
        guide = [
            point(at, float(value / 100))
            for at, value in pace_guide_points(
                window.start, window.end, window.schedule, zone
            )
        ]
        if len(guide) > 1:
            self.create_line(
                *[coordinate for item in guide for coordinate in item],
                fill=GUIDE,
                width=3,
                joinstyle="round",
            )

        actual_observations = daily_observations(self.observations, zone)
        actual = [
            point(item.observed_at, float(item.used / latest_limit))
            for item in actual_observations
        ]
        if actual:
            first_x, first_y = actual[0]
            start_x, start_y = point(window.start, 0)
            if first_x > start_x:
                self.create_line(
                    start_x,
                    start_y,
                    first_x,
                    first_y,
                    fill=USAGE,
                    width=2,
                    dash=(2, 5),
                )
        for index, ((start_x, start_y), (end_x, end_y)) in enumerate(
            zip(actual, actual[1:])
        ):
            gap = (
                actual_observations[index + 1].observed_at
                - actual_observations[index].observed_at
            ).total_seconds()
            line_options = {
                "fill": USAGE,
                "width": 3,
                "joinstyle": "round",
            }
            if gap > 4 * 24 * 60 * 60:
                line_options["dash"] = (2, 5)
            self.create_line(start_x, start_y, end_x, end_y, **line_options)
        for index, (x, y) in enumerate(actual):
            is_current = index == len(actual) - 1
            radius = 4 if is_current else 3
            if is_current:
                self.create_rectangle(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    outline=USAGE,
                    fill=USAGE,
                )
            else:
                self.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    outline=USAGE,
                    fill=USAGE,
                )

    def _draw_header(self, left: int) -> None:
        self.create_text(
            left,
            15,
            text="USAGE OVER TIME",
            fill=TEXT,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )
        self.create_line(left, 30, left + 14, 30, fill=USAGE, width=3)
        self.create_text(
            left + 20,
            30,
            text="Usage",
            fill=USAGE,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )
        self.create_line(left + 80, 30, left + 94, 30, fill=GUIDE, width=3)
        self.create_text(
            left + 100,
            30,
            text=(
                "Working-time guide"
                if self.window_data and self.window_data.schedule.mode is UsageMode.WORK
                else "Calendar-time guide"
            ),
            fill=GUIDE,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )

    def _draw_horizontal_grid(
        self, left: int, top: int, plot_width: int, plot_height: int
    ) -> None:
        for percent in range(0, 101, 25):
            y = top + plot_height * (1 - percent / 100)
            self.create_line(left, y, left + plot_width, y, fill=GRID, width=1)
            self.create_text(
                left - 8,
                y,
                text=f"{percent}%",
                fill=MUTED,
                anchor="e",
                font=("Segoe UI", 9),
            )

    def _draw_time_grid(
        self, window: Window, point, top: int, plot_height: int
    ) -> None:
        duration = window.end - window.start
        for index in range(5):
            at = window.start + duration * index / 4
            x, _ = point(at, 0)
            self.create_line(x, top, x, top + plot_height, fill=GRID, dash=(3, 5))
            self.create_text(
                x,
                top + plot_height + 12,
                text=at.astimezone().strftime("%d %b"),
                fill=MUTED,
                anchor="n",
                font=("Segoe UI", 9),
            )
