from __future__ import annotations

import tkinter as tk
from zoneinfo import ZoneInfo

from .domain import Observation, working_guide_points
from .storage import Window


class UsageGraph(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, height=240, background="#1f2329", highlightthickness=0)
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
        width, height = max(self.winfo_width(), 520), max(self.winfo_height(), 240)
        left, right, top, bottom = 48, 14, 12, 30
        plot_width, plot_height = width - left - right, height - top - bottom
        for percent in range(0, 101, 25):
            y = top + plot_height * (1 - percent / 100)
            self.create_line(left, y, left + plot_width, y, fill="#4a4f55")
            self.create_text(8, y, text=f"{percent}%", fill="#d6dbe1", anchor="w")
        window = self.window_data
        if window is None or not self.observations or window.end <= window.start:
            self.create_text(
                left + 20,
                top + plot_height / 2,
                text="Usage history will appear after a successful refresh.",
                fill="#d6dbe1",
                anchor="w",
            )
            return
        duration, latest_limit = (
            window.end - window.start
        ).total_seconds(), self.observations[-1].limit

        def point(at, fraction):
            x = left + plot_width * max(
                0, min(1, (at - window.start).total_seconds() / duration)
            )
            y = top + plot_height * (1 - max(0, min(1, float(fraction))))
            return x, y

        guide = [
            point(at, value / 100)
            for at, value in working_guide_points(
                window.start,
                window.end,
                window.schedule,
                ZoneInfo(window.timezone_name),
            )
        ]
        if len(guide) > 1:
            self.create_line(
                *[coordinate for item in guide for coordinate in item],
                fill="#36c95d",
                width=2,
            )
        actual = [
            point(item.observed_at, item.used / latest_limit)
            for item in self.observations
        ]
        if len(actual) > 1:
            self.create_line(
                *[coordinate for item in actual for coordinate in item],
                fill="#4594f3",
                width=2,
            )
        for x, y in actual:
            self.create_oval(
                x - 3, y - 3, x + 3, y + 3, outline="#4594f3", fill="#4594f3"
            )
        self.create_text(
            left,
            height - 9,
            text=window.start.astimezone().strftime("%d %b"),
            fill="#d6dbe1",
            anchor="w",
        )
        self.create_text(
            left + plot_width,
            height - 9,
            text=window.end.astimezone().strftime("%d %b"),
            fill="#d6dbe1",
            anchor="e",
        )
