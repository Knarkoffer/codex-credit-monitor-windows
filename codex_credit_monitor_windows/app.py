from __future__ import annotations

import os
import queue
import sqlite3
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    Evaluation,
    Observation,
    PaceState,
    Schedule,
    Thresholds,
    UsageMetric,
    UsageMode,
    evaluate,
)
from .forecast import STALE_SECONDS, ForecastState, UsageForecast, estimate_usage
from .graph import UsageGraph
from .settings import Settings, SettingsStore
from .single_instance import SingleInstance
from .storage import CommitResult, HistoryStore
from .tray import TrayController, create_icon_image
from .usage import UsageError, fetch_usage, read_credentials
from .version import VERSION_LABEL
from .wsl import distributions


REFRESH_MILLISECONDS = 15 * 60 * 1000
WINDOWS_APP_USER_MODEL_ID = "CodexCreditMonitor.Windows"
AUTOMATIC_LOGIN_SOURCE = "Automatic (Windows first, then default WSL)"


def _format_clock_time(minutes: int) -> str:
    if not 0 <= minutes <= 24 * 60:
        raise ValueError("Time must remain within one day.")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_clock_time(value: str, *, allow_day_end: bool = False) -> int:
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Choose a valid time.") from error
    if not 0 <= minute < 60 or not 0 <= hour <= 24:
        raise ValueError("Choose a valid time.")
    if hour == 24 and (minute != 0 or not allow_day_end):
        raise ValueError("Choose a valid time.")
    return hour * 60 + minute


def _clock_time_choices(
    current: int, *, allow_day_end: bool = False
) -> tuple[str, ...]:
    first = 15 if allow_day_end else 0
    stop = 24 * 60 + (15 if allow_day_end else 0)
    choices = set(range(first, stop, 15))
    choices.add(current)
    return tuple(_format_clock_time(minutes) for minutes in sorted(choices))


def _login_source_choices(
    current_distro: str | None, installed_distros: list[str]
) -> tuple[str, ...]:
    distros = list(installed_distros)
    if current_distro and current_distro not in distros:
        distros.insert(0, current_distro)
    return (AUTOMATIC_LOGIN_SOURCE, *distros)


def _distro_from_login_source(value: str) -> str | None:
    selected = value.strip()
    return None if not selected or selected == AUTOMATIC_LOGIN_SOURCE else selected


def _format_in_timezone(value: datetime, timezone_name: str, pattern: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime(pattern)


def _format_pace_status(state: PaceState, *, warning: bool = False) -> str:
    if warning:
        return f"{state.value}  ⚠️"
    symbol = {
        PaceState.BEHIND: "✅",
        PaceState.ON_PACE: "✅",
        PaceState.AHEAD: "🟠",
        PaceState.CRITICAL: "⚠️",
        PaceState.UNAVAILABLE: "❔",
    }[state]
    return f"{state.value}  {symbol}"


def _format_forecast_date(value: datetime, timezone_name: str, at: datetime) -> str:
    zone = ZoneInfo(timezone_name)
    local = value.astimezone(zone)
    if local.date() != at.astimezone(zone).date():
        return f"{local:%A}, {local.day} {local:%B} at {local:%H:%M}"
    seconds = (
        value.astimezone(timezone.utc) - at.astimezone(timezone.utc)
    ).total_seconds()
    if seconds <= 0:
        relative = "estimate passed"
    elif seconds < 60:
        relative = "in less than a minute"
    else:
        hours, minutes = divmod(round(seconds / 60), 60)
        parts = []
        for amount, unit in ((hours, "hour"), (minutes, "minute")):
            if amount:
                parts.append(f"{amount} {unit}{'s' if amount != 1 else ''}")
        relative = "in " + " ".join(parts)
    return f"Today at {local:%H:%M} ({relative})"


def _forecast_message(
    forecast: UsageForecast,
    observation: Observation,
    schedule: Schedule,
    timezone_name: str,
    at: datetime,
) -> str:
    allowance = (
        "plan allowance" if observation.metric is UsageMetric.PLAN_USAGE else "credits"
    )
    if forecast.state is ForecastState.RUNS_OUT:
        assert forecast.exhaustion_at is not None
        if forecast.exhaustion_at <= at:
            message = f"The recent trend suggests your {allowance} may already be exhausted. Refresh to check."
        else:
            when = _format_in_timezone(
                forecast.exhaustion_at, timezone_name, "%A, %d %B %Y at %H:%M %Z"
            )
            message = f"If your recent usage trend continues, you'll run out of {allowance} around {when}."
    else:
        message = {
            ForecastState.WAITING: "Waiting for enough history to estimate usage (at least 3 readings spanning 1 hour in your selected mode).",
            ForecastState.STALE: "Estimate unavailable: usage data is at least 30 minutes old. Refresh to update it.",
            ForecastState.RESET: "The usage period has ended. Refresh to estimate the new allowance.",
            ForecastState.EXHAUSTED: f"You've used all of your {allowance}, according to the latest reading.",
            ForecastState.FLAT: "No usage increase was measured in the recent readings; a run-out date cannot be estimated yet.",
            ForecastState.LASTS_UNTIL_RESET: f"If your recent usage trend continues, your {allowance} should last until the reset.",
        }[forecast.state]
    if forecast.sample_count:
        mode = "working" if schedule.mode is UsageMode.WORK else "calendar"
        message += (
            f"\nBased on {forecast.sample_count} readings over "
            f"{forecast.elapsed_seconds / 3600:.1f} {mode} hours."
        )
        if schedule.mode is UsageMode.WORK:
            message += " Assumes future usage stays within your weekday working hours."
    return message


def _configure_windows_app_identity(shell32=None) -> None:
    """Give this Python-hosted app its own Windows taskbar identity."""
    if os.name != "nt":
        return
    try:
        if shell32 is None:
            from ctypes import windll

            shell32 = windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, ImportError, OSError):
        # Older/non-standard Windows environments can still use the app; only
        # taskbar grouping and icon selection fall back to Python's defaults.
        pass


class _HoverTooltip:
    """A delayed, non-focus-stealing explanation for a details row."""

    def __init__(self, row: ttk.Frame) -> None:
        self.row = row
        self.text = tk.StringVar(master=row)
        self.pending: str | None = None
        self.popup: tk.Toplevel | None = None
        for widget in (row, *row.winfo_children()):
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self.hide, add="+")
            widget.bind("<ButtonPress>", self.hide, add="+")
        row.bind("<Unmap>", self.hide, add="+")
        row.bind("<Destroy>", self.hide, add="+")

    def set_text(self, text: str) -> None:
        self.text.set(text)
        if not text:
            self.hide()

    def _schedule(self, _event=None) -> None:
        self.hide()
        if self.text.get():
            self.pending = self.row.after(400, self._show)

    def _show(self) -> None:
        self.pending = None
        if not self.text.get() or not self.row.winfo_ismapped():
            return
        popup = self.popup = tk.Toplevel(self.row)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        ttk.Label(
            popup,
            textvariable=self.text,
            wraplength=420,
            padding=10,
            relief="solid",
            borderwidth=1,
        ).pack()
        popup.update_idletasks()
        x = min(
            self.row.winfo_rootx(),
            self.row.winfo_screenwidth() - popup.winfo_reqwidth(),
        )
        y = self.row.winfo_rooty() + self.row.winfo_height() + 4
        if y + popup.winfo_reqheight() > self.row.winfo_screenheight():
            y = self.row.winfo_rooty() - popup.winfo_reqheight() - 4
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.deiconify()

    def hide(self, _event=None) -> None:
        if self.pending is not None:
            self.row.after_cancel(self.pending)
            self.pending = None
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None


class MonitorApplication:
    def __init__(self) -> None:
        _configure_windows_app_identity()
        self.root = tk.Tk()
        self.root.title("Codex Credit Monitor")
        icon_image = create_icon_image()
        from PIL import ImageTk

        self.window_icon = ImageTk.PhotoImage(icon_image, master=self.root)
        self.root.iconphoto(True, self.window_icon)
        self.root.minsize(620, 650)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Unmap>", self._window_unmapped, add="+")
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.store = HistoryStore()
        self.closing = False
        self.tray = TrayController(
            self._queue_restore, self._queue_close, image=icon_image
        )
        self.result: CommitResult | None = None
        self.last_error: str | None = None
        self.refreshing = False
        self.refresh_pending = False
        self.refresh_results: queue.SimpleQueue[
            tuple[Observation | None, str | None]
        ] = queue.SimpleQueue()
        self.selected_window: int | None = None
        self.values: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="—")
            for key in ("spent", "left", "working", "pace", "reset", "updated")
        }
        self.labels = {
            "spent": tk.StringVar(value="Credits spent"),
            "left": tk.StringVar(value="Credits left"),
            "working": tk.StringVar(),
        }
        self.status = tk.StringVar(value="Usage unavailable  ❔")
        self.message = tk.StringVar(value="Waiting for the first successful refresh.")
        self.forecast = tk.StringVar()
        self.window_choice = tk.StringVar()
        self.window_labels: dict[str, int] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Usage pace",
            foreground="#555555",
        ).grid(row=0, column=0, sticky="w")
        self.status_font = tkfont.Font(
            root=self.root,
            font=ttk.Style(self.root).lookup("TLabel", "font") or "TkDefaultFont",
        )
        self.status_font.configure(size=16, weight="normal")
        status_row = ttk.Frame(header)
        status_row.grid(row=1, column=0, sticky="w")
        self.status_text = tk.StringVar()
        self.status_icon = tk.StringVar()
        status_label = ttk.Label(
            status_row, textvariable=self.status_text, font=self.status_font
        )
        status_label.grid(row=0, column=0, sticky="w")
        default_status_color = ttk.Style(self.root).lookup("TLabel", "foreground")
        status_colors = {
            PaceState.BEHIND.value: "#228B22",
            PaceState.ON_PACE.value: "#228B22",
            PaceState.AHEAD.value: "#A67C00",
            PaceState.CRITICAL.value: "#D26900",
            "Allowance exhausted": "#B00020",
        }
        # Text symbols can follow the foreground color; color emoji cannot.
        status_symbols = {
            "✅": "✓",
            "🎯": "◎",
            "🟠": "●",
            "⚠️": "⚠\uFE0E",
            "❔": "?",
            "❌": "✕",
            "⏳": "⌛\uFE0E",
        }
        icon_label = ttk.Label(
            status_row,
            textvariable=self.status_icon,
            font=("Segoe UI Symbol", 12),
            anchor="center",
        )
        icon_label.grid(row=0, column=1, padx=(8, 0))

        def sync_status_labels(*_args: str) -> None:
            text, separator, icon = self.status.get().rpartition("  ")
            status_text = text if separator else self.status.get()
            self.status_text.set(status_text)
            self.status_icon.set(status_symbols.get(icon, icon) if separator else "")
            color = status_colors.get(status_text, default_status_color)
            status_label.configure(foreground=color)
            icon_label.configure(foreground=color)

        self.status.trace_add("write", sync_status_labels)
        sync_status_labels()
        self.refresh_button = ttk.Button(header, text="Refresh", command=self.refresh)
        self.refresh_button.grid(row=0, column=1, rowspan=2, sticky="e")
        details = ttk.Frame(outer, padding=(0, 12, 0, 4))
        details.grid(row=1, column=0, sticky="ew")
        details.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(
            (
                ("spent", self.labels["spent"]),
                ("left", self.labels["left"]),
                ("working", self.labels["working"]),
                ("pace", "Pace difference"),
                ("reset", "Reset"),
                ("updated", "Updated"),
            )
        ):
            label_options = (
                {"textvariable": label}
                if isinstance(label, tk.StringVar)
                else {"text": label}
            )
            ttk.Label(details, foreground="#555555", **label_options).grid(
                row=row, column=0, sticky="w", padx=(0, 24), pady=2
            )
            ttk.Label(details, textvariable=self.values[key]).grid(
                row=row, column=1, sticky="e", pady=2
            )
        self.forecast_row = ttk.Frame(details)
        self.forecast_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=2)
        self.forecast_row.columnconfigure(1, weight=1)
        ttk.Label(self.forecast_row, text="Expiry forecast", foreground="#b00020").grid(
            row=0, column=0, sticky="w", padx=(0, 24)
        )
        ttk.Label(
            self.forecast_row, textvariable=self.forecast, foreground="#b00020"
        ).grid(row=0, column=1, sticky="e")
        self.forecast_tooltip = _HoverTooltip(self.forecast_row)
        self.forecast_row.grid_remove()
        ttk.Label(
            outer, textvariable=self.message, wraplength=570, foreground="#9b3f00"
        ).grid(row=2, column=0, sticky="w", pady=(4, 10))
        selector = ttk.Frame(outer)
        selector.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        selector.columnconfigure(1, weight=1)
        ttk.Label(selector, text="Usage window").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.window_box = ttk.Combobox(
            selector, state="readonly", textvariable=self.window_choice
        )
        self.window_box.grid(row=0, column=1, sticky="ew")
        self.window_box.bind("<<ComboboxSelected>>", self._window_changed)
        self.graph = UsageGraph(outer)
        self.graph.grid(row=4, column=0, sticky="nsew")
        actions = ttk.Frame(outer, padding=(0, 12, 0, 0))
        actions.grid(row=5, column=0, sticky="ew")
        ttk.Button(actions, text="Settings", command=self.show_settings).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(actions, text="Delete history", command=self.delete_history).grid(
            row=0, column=1
        )
        ttk.Label(actions, text=VERSION_LABEL, foreground="#777777").grid(
            row=0, column=2, sticky="e", padx=12
        )
        ttk.Button(actions, text="Quit", command=self.close).grid(
            row=0, column=3, sticky="e"
        )
        actions.columnconfigure(2, weight=1)

    def run(self) -> None:
        self.refresh()
        self.root.after(100, self._poll_refresh_results)
        self.root.after(REFRESH_MILLISECONDS, self._scheduled_refresh)
        self.root.after(60_000, self._minute_tick)
        self.root.mainloop()

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.tray.stop()
        self.store.close()
        self.root.destroy()

    def _window_unmapped(self, event) -> None:
        if event.widget is self.root:
            self.root.after_idle(self._hide_if_minimized)

    def _hide_if_minimized(self) -> None:
        if not self.closing and self.root.state() == "iconic":
            self.tray.show()
            self.tray.show_minimize_hint()
            self.root.withdraw()

    def _queue_restore(self) -> None:
        self._queue_root_action(self._restore_from_tray)

    def _queue_close(self) -> None:
        self._queue_root_action(self.close)

    def _queue_root_action(self, action) -> None:
        if self.closing:
            return
        try:
            self.root.after(0, action)
        except (RuntimeError, tk.TclError):
            pass

    def _restore_from_tray(self) -> None:
        if self.closing:
            return
        self.tray.hide()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def refresh(self) -> None:
        if self.refreshing:
            self.refresh_pending = True
            return
        self.refreshing, self.last_error = True, None
        self.refresh_button.configure(state="disabled")
        self._refresh_display()
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self) -> None:
        try:
            observation = fetch_usage(read_credentials(self.settings.wsl_distro))
            self.refresh_results.put((observation, None))
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, UsageError)
                else "Usage refresh failed unexpectedly."
            )
            self.refresh_results.put((None, message))

    def _poll_refresh_results(self) -> None:
        while True:
            try:
                observation, error = self.refresh_results.get_nowait()
            except queue.Empty:
                break
            if observation is not None:
                self._commit_observation(observation)
            else:
                self._finish_refresh(error)
        if not self.closing:
            self.root.after(100, self._poll_refresh_results)

    def _commit_observation(self, observation: Observation) -> None:
        try:
            follow_current = (
                self.result is None or self.selected_window == self.result.window.id
            )
            self.result = self.store.commit(
                observation,
                self.settings.schedule,
                self.settings.thresholds,
                _timezone_name(self.settings.timezone_name),
            )
            if follow_current:
                self.selected_window = self.result.window.id
            self.last_error = None
            self._reload_windows()
            if self.result.notify and self.settings.notifications_enabled:
                self._notify_critical()
        except Exception:
            self.last_error = (
                "The successful response could not be saved to local history."
            )
        self._finish_refresh(None)

    def _finish_refresh(self, error: str | None) -> None:
        if error:
            self.last_error = error
        self.refreshing = False
        self.refresh_button.configure(state="normal")
        self._refresh_display()
        if self.refresh_pending:
            self.refresh_pending = False
            self.root.after(0, self.refresh)

    def _scheduled_refresh(self) -> None:
        self.refresh()
        self.root.after(REFRESH_MILLISECONDS, self._scheduled_refresh)

    def _minute_tick(self) -> None:
        self._refresh_display()
        self.root.after(60_000, self._minute_tick)

    def _current_evaluation(self) -> Evaluation | None:
        if self.result is None:
            return None
        fresh_until = self.result.observation.observed_at + timedelta(
            seconds=STALE_SECONDS
        )
        at = min(datetime.now(timezone.utc), fresh_until)
        return evaluate(
            self.result.observation,
            at,
            self.result.window.start,
            self.settings.schedule,
            self.settings.thresholds,
            ZoneInfo(self.result.window.timezone_name),
        )

    def _refresh_display(self) -> None:
        self.labels["working"].set(
            "Calendar time (Personal)"
            if self.settings.schedule.mode is UsageMode.PERSONAL
            else "Working time (Work)"
        )
        evaluation = self._current_evaluation()
        if self.result is None or evaluation is None:
            self.status.set(
                "Refreshing…  ⏳" if self.refreshing else "Usage unavailable  ❔"
            )
            self.message.set(
                self.last_error or "Waiting for the first successful refresh."
            )
            self._hide_forecast()
            return
        observation = self.result.observation
        spent = observation.used / observation.limit * Decimal(100)
        self.tray.set_utilization(spent)
        remaining_time = evaluation.remaining_time_percent
        now = datetime.now(timezone.utc)
        stale = now >= observation.observed_at + timedelta(seconds=STALE_SECONDS)
        period_ended = now >= observation.reset_at
        self.status.set(
            "Period ended  ❔"
            if period_ended
            else (
                "Allowance exhausted  ❌"
                if observation.used >= observation.limit
                else _format_pace_status(
                    evaluation.state, warning=stale or bool(self.last_error)
                )
            )
        )
        if observation.metric is UsageMetric.PLAN_USAGE:
            self.labels["spent"].set("Plan usage")
            self.labels["left"].set("Usage remaining")
            self.values["spent"].set(f"{_decimal(spent)}% used")
            self.values["left"].set(f"{_decimal(evaluation.actual_remaining_percent)}%")
        else:
            self.labels["spent"].set("Credits spent")
            self.labels["left"].set("Credits left")
            self.values["spent"].set(
                f"{_decimal(observation.used)} of {_decimal(observation.limit)} ({_decimal(spent)}%)"
            )
            self.values["left"].set(
                f"{_decimal(observation.limit - observation.used)} ({_decimal(evaluation.actual_remaining_percent)}%)"
            )
        self.values["working"].set(
            f"{_decimal(Decimal(100) - remaining_time)}% passed"
            if remaining_time is not None
            else "Unavailable"
        )
        self.values["pace"].set(
            f"{_signed(evaluation.difference)} points"
            if evaluation.guide_remaining_percent is not None
            else "Unavailable"
        )
        timezone_name = self.result.window.timezone_name
        self.values["reset"].set(
            _format_in_timezone(observation.reset_at, timezone_name, "%d %b %Y, %H:%M")
        )
        self.values["updated"].set(
            _format_in_timezone(
                observation.observed_at, timezone_name, "%d %b %Y, %H:%M"
            )
        )
        messages = (
            ["Data is stale; pace is frozen at the 30-minute freshness boundary."]
            if stale
            else []
        ) + ([self.last_error] if self.last_error else [])
        if period_ended:
            messages.insert(
                0, "The usage period has ended. Refresh to load the new allowance."
            )
        self.message.set(" ".join(messages))
        window = self.result.window
        forecast = estimate_usage(
            self.store.observations(window.id),
            window.start,
            window.schedule,
            ZoneInfo(window.timezone_name),
            now,
        )
        if forecast.state is not ForecastState.RUNS_OUT:
            self._hide_forecast()
            return
        assert forecast.exhaustion_at is not None
        self.forecast.set(
            _format_forecast_date(forecast.exhaustion_at, window.timezone_name, now)
        )
        self.forecast_tooltip.set_text(
            _forecast_message(
                forecast, observation, window.schedule, window.timezone_name, now
            )
        )
        self.forecast_row.grid()

    def _hide_forecast(self) -> None:
        self.forecast.set("")
        self.forecast_tooltip.set_text("")
        self.forecast_row.grid_remove()

    def _reload_windows(self) -> None:
        assert self.result is not None
        selected = self.selected_window
        choices: list[str] = []
        self.window_labels = {}
        for window in self.store.windows(self.result.observation.account_key):
            prefix = "Current · " if window.id == self.result.window.id else ""
            label = (
                prefix
                + _format_in_timezone(window.start, window.timezone_name, "%d %b %Y")
                + " – "
                + _format_in_timezone(window.end, window.timezone_name, "%d %b %Y")
            )
            choices.append(label)
            self.window_labels[label] = window.id
        self.window_box["values"] = choices
        target = next(
            (label for label, ident in self.window_labels.items() if ident == selected),
            choices[0],
        )
        self.window_choice.set(target)
        self._load_window(self.window_labels[target])

    def _window_changed(self, _event=None) -> None:
        window_id = self.window_labels.get(self.window_choice.get())
        if window_id is not None:
            self._load_window(window_id)

    def _load_window(self, window_id: int) -> None:
        self.selected_window = window_id
        try:
            self.graph.update(
                self.store.window(window_id), self.store.observations(window_id)
            )
        except KeyError:
            self.graph.clear()

    def delete_history(self) -> None:
        if messagebox.askyesno(
            "Delete history",
            "Delete all stored observations and notification state?\n\nYour settings and Codex login will be preserved.",
            parent=self.root,
        ):
            self.store.delete_all()
            self.result = None
            self.selected_window = None
            self.graph.clear()
            self.window_box["values"] = []
            self.window_choice.set("")
            self._refresh_display()

    def _apply_settings(self, settings: Settings) -> None:
        self.settings_store.save(settings)
        self.settings = settings
        if self.result is not None:
            window = self.store.update_pacing(
                self.result.window.id,
                settings.schedule,
                settings.thresholds,
                _timezone_name(settings.timezone_name),
            )
            self.result = replace(self.result, window=window, notify=False)
            self._reload_windows()
        self._refresh_display()

    def show_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.grab_set()
        installed_distros = distributions()
        initial_distro = self.settings.wsl_distro or AUTOMATIC_LOGIN_SOURCE
        fields = {
            "mode": tk.StringVar(value=self.settings.schedule.mode.value.title()),
            "start": tk.StringVar(
                value=_format_clock_time(self.settings.schedule.start_minutes)
            ),
            "end": tk.StringVar(
                value=_format_clock_time(self.settings.schedule.end_minutes)
            ),
            "tolerance": tk.StringVar(value=str(self.settings.thresholds.tolerance)),
            "base": tk.StringVar(value=str(self.settings.thresholds.critical_base)),
            "growth": tk.StringVar(value=str(self.settings.thresholds.critical_growth)),
            "distro": tk.StringVar(value=initial_distro),
            "timezone": tk.StringVar(
                value=self.settings.timezone_name or _timezone_name(None)
            ),
            "notify": tk.BooleanVar(value=self.settings.notifications_enabled),
        }
        ttk.Label(dialog, text="Usage mode").grid(
            row=0, column=0, padx=16, pady=5, sticky="w"
        )
        mode_box = ttk.Combobox(
            dialog,
            textvariable=fields["mode"],
            values=("Personal", "Work"),
            state="readonly",
            width=21,
        )
        mode_box.grid(row=0, column=1, padx=16, pady=5)
        mode_help = tk.StringVar()
        ttk.Label(dialog, textvariable=mode_help, wraplength=460).grid(
            row=1, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="w"
        )
        labels = (
            ("Workday start", "start"),
            ("Workday end", "end"),
            ("On-pace tolerance", "tolerance"),
            ("Critical base", "base"),
            ("Critical growth", "growth"),
            ("Codex login source", "distro"),
            ("Time zone (IANA, e.g. Europe/Stockholm)", "timezone"),
        )
        work_widgets = []
        for row, (label, key) in enumerate(labels, start=2):
            label_widget = ttk.Label(dialog, text=label)
            label_widget.grid(row=row, column=0, padx=16, pady=5, sticky="w")
            if key in ("start", "end"):
                entry = ttk.Combobox(
                    dialog,
                    textvariable=fields[key],
                    values=_clock_time_choices(
                        getattr(self.settings.schedule, f"{key}_minutes"),
                        allow_day_end=key == "end",
                    ),
                    state="readonly",
                    width=21,
                )
                entry.grid(row=row, column=1, padx=16, pady=5)
                work_widgets.extend((label_widget, entry))
            elif key == "distro":
                ttk.Combobox(
                    dialog,
                    textvariable=fields[key],
                    values=_login_source_choices(
                        self.settings.wsl_distro, installed_distros
                    ),
                    width=42,
                ).grid(row=row, column=1, padx=16, pady=5)
            else:
                entry = ttk.Entry(dialog, textvariable=fields[key], width=24)
                entry.grid(row=row, column=1, padx=16, pady=5)

        def update_mode(_event=None) -> None:
            personal = fields["mode"].get() == "Personal"
            mode_help.set(
                "Pace uses the entire reset period, including evenings and weekends."
                if personal
                else "Pace uses your working hours, Monday through Friday."
            )
            for widget in work_widgets:
                widget.grid_remove() if personal else widget.grid()

        mode_box.bind("<<ComboboxSelected>>", update_mode)
        update_mode()
        ttk.Checkbutton(
            dialog, text="Enable critical-pace notifications", variable=fields["notify"]
        ).grid(row=len(labels) + 2, column=0, columnspan=2, padx=16, pady=8, sticky="w")

        def save() -> None:
            try:
                timezone_name = fields["timezone"].get().strip() or None
                if timezone_name:
                    ZoneInfo(timezone_name)
                mode = UsageMode(fields["mode"].get().lower())
                schedule = (
                    replace(self.settings.schedule, mode=mode)
                    if mode is UsageMode.PERSONAL
                    else Schedule(
                        _parse_clock_time(fields["start"].get()),
                        _parse_clock_time(fields["end"].get(), allow_day_end=True),
                        mode,
                    )
                )
                settings = Settings(
                    schedule,
                    Thresholds(
                        Decimal(fields["tolerance"].get()),
                        Decimal(fields["base"].get()),
                        Decimal(fields["growth"].get()),
                    ),
                    fields["notify"].get(),
                    _distro_from_login_source(fields["distro"].get()),
                    timezone_name,
                )
                self._apply_settings(settings)
            except (
                ValueError,
                ArithmeticError,
                ZoneInfoNotFoundError,
                OSError,
                sqlite3.Error,
            ) as error:
                messagebox.showerror("Invalid settings", str(error), parent=dialog)
                return
            dialog.destroy()

        ttk.Button(dialog, text="Cancel", command=dialog.destroy).grid(
            row=len(labels) + 3, column=0, padx=16, pady=(4, 16), sticky="w"
        )
        ttk.Button(dialog, text="Save", command=save).grid(
            row=len(labels) + 3, column=1, padx=16, pady=(4, 16), sticky="e"
        )

    def _notify_critical(self) -> None:
        if os.name != "nt":
            return
        script = "Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Warning; $n.Visible=$true; $n.ShowBalloonTip(10000,'Codex Credit Monitor','Codex spending is critically ahead.',[System.Windows.Forms.ToolTipIcon]::Warning); Start-Sleep -Seconds 11; $n.Dispose()"
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


def _timezone_name(configured: str | None) -> str:
    override = configured or os.environ.get("CODEX_CREDIT_MONITOR_TIMEZONE")
    if override:
        try:
            ZoneInfo(override)
            return override
        except ZoneInfoNotFoundError:
            pass
    try:
        from tzlocal import get_localzone_name

        detected = get_localzone_name()
        ZoneInfo(detected)
        return detected
    except (ImportError, OSError, ValueError, ZoneInfoNotFoundError):
        pass
    name = time.tzname[0]
    return {
        "CET": "Europe/Stockholm",
        "CEST": "Europe/Stockholm",
        "W. Europe Standard Time": "Europe/Stockholm",
        "GMT Standard Time": "Europe/London",
        "Eastern Standard Time": "America/New_York",
        "Pacific Standard Time": "America/Los_Angeles",
    }.get(name, "UTC")


def _decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.1"))).rstrip("0").rstrip(".")


def _signed(value: Decimal) -> str:
    return ("+" if value >= 0 else "") + _decimal(value)


def main() -> int:
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    try:
        MonitorApplication().run()
    finally:
        instance.release()
    return 0
