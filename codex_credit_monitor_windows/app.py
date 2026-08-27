from __future__ import annotations

import os
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import Evaluation, Schedule, Thresholds, UsageMetric, evaluate
from .graph import UsageGraph
from .settings import Settings, SettingsStore
from .single_instance import SingleInstance
from .storage import CommitResult, HistoryStore
from .tray import TrayController, create_icon_image
from .usage import UsageError, fetch_usage, read_credentials
from .wsl import distributions


REFRESH_MILLISECONDS = 15 * 60 * 1000
STALE_SECONDS = 30 * 60
WINDOWS_APP_USER_MODEL_ID = "CodexCreditMonitor.Windows"


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
        self.selected_window: int | None = None
        self.values: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="—")
            for key in ("spent", "left", "working", "pace", "reset", "updated")
        }
        self.labels = {
            "spent": tk.StringVar(value="Credits spent"),
            "left": tk.StringVar(value="Credits left"),
        }
        self.status = tk.StringVar(value="Usage unavailable")
        self.message = tk.StringVar(value="Waiting for the first successful refresh.")
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
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status, font=("Segoe UI", 16, "bold")).grid(
            row=1, column=0, sticky="w"
        )
        self.refresh_button = ttk.Button(header, text="Refresh", command=self.refresh)
        self.refresh_button.grid(row=0, column=1, rowspan=2, sticky="e")
        details = ttk.Frame(outer, padding=(0, 12, 0, 4))
        details.grid(row=1, column=0, sticky="ew")
        details.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(
            (
                ("spent", self.labels["spent"]),
                ("left", self.labels["left"]),
                ("working", "Working time"),
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
        ttk.Button(actions, text="Quit", command=self.close).grid(
            row=0, column=2, sticky="e"
        )
        actions.columnconfigure(2, weight=1)

    def run(self) -> None:
        self.refresh()
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
            self.root.after(0, self._commit_observation, observation)
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, UsageError)
                else "Usage refresh failed unexpectedly."
            )
            self.root.after(0, self._finish_refresh, message)

    def _commit_observation(self, observation) -> None:
        try:
            self.result = self.store.commit(
                observation,
                self.settings.schedule,
                self.settings.thresholds,
                _timezone_name(self.settings.timezone_name),
            )
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
        evaluation = self._current_evaluation()
        if self.result is None or evaluation is None:
            self.status.set("Refreshing…" if self.refreshing else "Usage unavailable")
            self.message.set(
                self.last_error or "Waiting for the first successful refresh."
            )
            return
        observation = self.result.observation
        spent = observation.used / observation.limit * Decimal(100)
        self.tray.set_utilization(spent)
        working_passed = Decimal(100) - (
            evaluation.remaining_working_percent or Decimal(0)
        )
        stale = datetime.now(timezone.utc) >= observation.observed_at + timedelta(
            seconds=STALE_SECONDS
        )
        self.status.set(
            f"{evaluation.state.value}{'  ⚠' if stale or self.last_error else ''}"
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
        self.values["working"].set(f"{_decimal(working_passed)}% passed")
        self.values["pace"].set(f"{_signed(evaluation.difference)} points")
        self.values["reset"].set(
            observation.reset_at.astimezone().strftime("%d %b %Y, %H:%M")
        )
        self.values["updated"].set(
            observation.observed_at.astimezone().strftime("%d %b %Y, %H:%M")
        )
        messages = (
            ["Data is stale; pace is frozen at the 30-minute freshness boundary."]
            if stale
            else []
        ) + ([self.last_error] if self.last_error else [])
        self.message.set(" ".join(messages))

    def _reload_windows(self) -> None:
        assert self.result is not None
        selected = self.selected_window
        choices: list[str] = []
        self.window_labels = {}
        for window in self.store.windows(self.result.observation.account_key):
            prefix = "Current · " if window.id == self.result.window.id else ""
            label = (
                prefix
                + f"{window.start.astimezone():%d %b %Y} – {window.end.astimezone():%d %b %Y}"
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
            self.graph.clear()
            self.window_box["values"] = []
            self.window_choice.set("")

    def show_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.grab_set()
        installed_distros = distributions()
        initial_distro = self.settings.wsl_distro or (
            installed_distros[0] if installed_distros else ""
        )
        fields = {
            "start": tk.StringVar(value=str(self.settings.schedule.start_minutes)),
            "end": tk.StringVar(value=str(self.settings.schedule.end_minutes)),
            "tolerance": tk.StringVar(value=str(self.settings.thresholds.tolerance)),
            "base": tk.StringVar(value=str(self.settings.thresholds.critical_base)),
            "growth": tk.StringVar(value=str(self.settings.thresholds.critical_growth)),
            "distro": tk.StringVar(value=initial_distro),
            "timezone": tk.StringVar(
                value=self.settings.timezone_name or _timezone_name(None)
            ),
            "notify": tk.BooleanVar(value=self.settings.notifications_enabled),
        }
        labels = (
            ("Workday start (minutes after midnight)", "start"),
            ("Workday end", "end"),
            ("On-pace tolerance", "tolerance"),
            ("Critical base", "base"),
            ("Critical growth", "growth"),
            ("WSL distribution (optional)", "distro"),
            ("Time zone (IANA, e.g. Europe/Stockholm)", "timezone"),
        )
        for row, (label, key) in enumerate(labels):
            ttk.Label(dialog, text=label).grid(
                row=row, column=0, padx=16, pady=5, sticky="w"
            )
            if key == "distro" and installed_distros:
                ttk.Combobox(
                    dialog,
                    textvariable=fields[key],
                    values=installed_distros,
                    state="readonly",
                    width=21,
                ).grid(row=row, column=1, padx=16, pady=5)
            else:
                ttk.Entry(dialog, textvariable=fields[key], width=24).grid(
                    row=row, column=1, padx=16, pady=5
                )
        ttk.Checkbutton(
            dialog, text="Enable critical-pace notifications", variable=fields["notify"]
        ).grid(row=len(labels), column=0, columnspan=2, padx=16, pady=8, sticky="w")

        def save() -> None:
            try:
                timezone_name = fields["timezone"].get().strip() or None
                if timezone_name:
                    ZoneInfo(timezone_name)
                self.settings = Settings(
                    Schedule(int(fields["start"].get()), int(fields["end"].get())),
                    Thresholds(
                        Decimal(fields["tolerance"].get()),
                        Decimal(fields["base"].get()),
                        Decimal(fields["growth"].get()),
                    ),
                    fields["notify"].get(),
                    fields["distro"].get().strip() or None,
                    timezone_name,
                )
                self.settings_store.save(self.settings)
            except (ValueError, ArithmeticError) as error:
                messagebox.showerror("Invalid settings", str(error), parent=dialog)
                return
            dialog.destroy()
            self._refresh_display()

        ttk.Button(dialog, text="Cancel", command=dialog.destroy).grid(
            row=len(labels) + 1, column=0, padx=16, pady=(4, 16), sticky="w"
        )
        ttk.Button(dialog, text="Save", command=save).grid(
            row=len(labels) + 1, column=1, padx=16, pady=(4, 16), sticky="e"
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
