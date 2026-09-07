from unittest import TestCase
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from codex_credit_monitor_windows.app import (
    WINDOWS_APP_USER_MODEL_ID,
    MonitorApplication,
    _clock_time_choices,
    _configure_windows_app_identity,
    _format_clock_time,
    _parse_clock_time,
)
from codex_credit_monitor_windows.domain import Observation, Schedule, UsageMode
from codex_credit_monitor_windows.settings import Settings, SettingsStore
from codex_credit_monitor_windows.storage import HistoryStore


class FakeShell32:
    def __init__(self) -> None:
        self.app_ids = []

    def SetCurrentProcessExplicitAppUserModelID(self, app_id) -> None:
        self.app_ids.append(app_id)


class WindowsIdentityTests(TestCase):
    def test_application_registers_its_own_windows_taskbar_identity(self):
        shell32 = FakeShell32()

        with patch("codex_credit_monitor_windows.app.os.name", "nt"):
            _configure_windows_app_identity(shell32)

        self.assertEqual(shell32.app_ids, [WINDOWS_APP_USER_MODEL_ID])

    def test_application_does_not_call_windows_api_on_other_platforms(self):
        shell32 = FakeShell32()

        with patch("codex_credit_monitor_windows.app.os.name", "posix"):
            _configure_windows_app_identity(shell32)

        self.assertEqual(shell32.app_ids, [])


class ClockTimeTests(TestCase):
    def test_clock_times_convert_to_and_from_stored_minutes(self):
        self.assertEqual(_format_clock_time(8 * 60), "08:00")
        self.assertEqual(_parse_clock_time("17:30"), 17 * 60 + 30)
        self.assertEqual(_parse_clock_time("24:00", allow_day_end=True), 24 * 60)

    def test_start_time_rejects_end_of_day_value(self):
        with self.assertRaisesRegex(ValueError, "valid time"):
            _parse_clock_time("24:00")

    def test_choices_keep_an_existing_time_outside_the_fifteen_minute_steps(self):
        choices = _clock_time_choices(8 * 60 + 7)

        self.assertIn("08:07", choices)
        self.assertIn("08:15", choices)


class FakeRoot:
    def __init__(self, state: str) -> None:
        self.window_state = state
        self.withdrawn = False
        self.deiconified = False
        self.lifted = False
        self.focused = False

    def state(self, new_state=None):
        if new_state is not None:
            self.window_state = new_state
        return self.window_state

    def withdraw(self) -> None:
        self.withdrawn = True

    def deiconify(self) -> None:
        self.deiconified = True

    def lift(self) -> None:
        self.lifted = True

    def focus_force(self) -> None:
        self.focused = True


class FakeTray:
    def __init__(self) -> None:
        self.shown = 0
        self.hidden = 0
        self.minimize_hints = 0

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1

    def show_minimize_hint(self) -> None:
        self.minimize_hints += 1


class MinimizeToTrayTests(TestCase):
    def application(self, state: str) -> MonitorApplication:
        application = MonitorApplication.__new__(MonitorApplication)
        application.root = FakeRoot(state)
        application.tray = FakeTray()
        application.closing = False
        return application

    def test_minimized_window_is_hidden_after_tray_icon_appears(self):
        application = self.application("iconic")

        application._hide_if_minimized()

        self.assertEqual(application.tray.shown, 1)
        self.assertEqual(application.tray.minimize_hints, 1)
        self.assertTrue(application.root.withdrawn)

    def test_non_minimized_window_is_not_hidden(self):
        application = self.application("normal")

        application._hide_if_minimized()

        self.assertEqual(application.tray.shown, 0)
        self.assertFalse(application.root.withdrawn)

    def test_restore_hides_tray_icon_and_activates_window(self):
        application = self.application("withdrawn")

        application._restore_from_tray()

        self.assertEqual(application.tray.hidden, 1)
        self.assertTrue(application.root.deiconified)
        self.assertEqual(application.root.state(), "normal")
        self.assertTrue(application.root.lifted)
        self.assertTrue(application.root.focused)


class UsageModeTests(TestCase):
    def test_saving_mode_updates_display_and_current_graph_without_refresh(self):
        with TemporaryDirectory() as directory, ExitStack() as resources:
            app = MonitorApplication.__new__(MonitorApplication)
            app.settings_store = SettingsStore(Path(directory) / "settings.json")
            app.store = HistoryStore(Path(directory) / "history.sqlite")
            resources.callback(app.store.close)
            app.settings = Settings(schedule=Schedule(), timezone_name="UTC")
            start = datetime.now(timezone.utc) - timedelta(days=1)
            app.result = app.store.commit(
                Observation(
                    "u",
                    "w",
                    Decimal(100),
                    Decimal(25),
                    start + timedelta(days=7),
                    datetime.now(timezone.utc),
                    start,
                ),
                app.settings.schedule,
                app.settings.thresholds,
                "UTC",
            )
            window_id = app.result.window.id
            app.selected_window = window_id
            app.graph = Mock()
            app.window_box = {}
            app.window_choice = Mock()
            app.values = {
                key: Mock()
                for key in ("spent", "left", "working", "pace", "reset", "updated")
            }
            app.labels = {key: Mock() for key in ("spent", "left", "working")}
            app.tray = Mock()
            app.status = Mock()
            app.message = Mock()
            app.last_error = None
            personal = Settings(
                schedule=Schedule(mode=UsageMode.PERSONAL), timezone_name="UTC"
            )

            app._apply_settings(personal)

            self.assertEqual(app.settings_store.load(), personal)
            self.assertEqual(app.result.window.schedule.mode, UsageMode.PERSONAL)
            app.labels["working"].set.assert_called_with("Calendar time (Personal)")
            graph_window, observations = app.graph.update.call_args.args
            self.assertEqual(graph_window.schedule.mode, UsageMode.PERSONAL)
            self.assertEqual(len(observations), 1)
            self.assertEqual(app.selected_window, window_id)

    def test_settings_can_be_saved_before_first_successful_refresh(self):
        app = MonitorApplication.__new__(MonitorApplication)
        app.settings_store = Mock()
        app.result = None
        app._refresh_display = Mock()
        settings = Settings()

        app._apply_settings(settings)

        app.settings_store.save.assert_called_once_with(settings)
        self.assertEqual(app.settings, settings)
        app._refresh_display.assert_called_once_with()
