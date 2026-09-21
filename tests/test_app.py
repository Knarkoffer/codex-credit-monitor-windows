import queue
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest import TestCase
from unittest.mock import Mock, patch

from codex_credit_monitor_windows.app import (
    AUTOMATIC_LOGIN_SOURCE,
    WINDOWS_APP_USER_MODEL_ID,
    MonitorApplication,
    _HoverTooltip,
    _clock_time_choices,
    _configure_windows_app_identity,
    _distro_from_login_source,
    _format_clock_time,
    _format_forecast_date,
    _format_in_timezone,
    _format_pace_status,
    _forecast_message,
    _login_source_choices,
    _parse_clock_time,
    _timezone_name,
)
from codex_credit_monitor_windows.domain import (
    Observation,
    PaceState,
    Schedule,
    UsageMetric,
    UsageMode,
)
from codex_credit_monitor_windows.settings import Settings, SettingsStore
from codex_credit_monitor_windows.forecast import ForecastState, UsageForecast
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


class LoginSourceTests(TestCase):
    def test_automatic_source_round_trips_without_selecting_a_distribution(self):
        choices = _login_source_choices(None, ["Ubuntu", "Debian"])

        self.assertEqual(choices[0], AUTOMATIC_LOGIN_SOURCE)
        self.assertIsNone(_distro_from_login_source(choices[0]))

    def test_saved_unavailable_distribution_remains_selectable(self):
        choices = _login_source_choices("Archived Linux", ["Ubuntu"])

        self.assertEqual(choices, (AUTOMATIC_LOGIN_SOURCE, "Archived Linux", "Ubuntu"))
        self.assertEqual(
            _distro_from_login_source(" Archived Linux "), "Archived Linux"
        )


class TimezoneTests(TestCase):
    def test_detected_iana_timezone_is_used_when_none_is_configured(self):
        module = ModuleType("tzlocal")
        module.get_localzone_name = Mock(return_value="America/New_York")

        with patch.dict(sys.modules, {"tzlocal": module}):
            self.assertEqual(_timezone_name(None), "America/New_York")

    def test_invalid_local_timezone_detection_uses_the_legacy_fallback(self):
        module = ModuleType("tzlocal")
        module.get_localzone_name = Mock(side_effect=ValueError("misconfigured"))

        with (
            patch.dict(sys.modules, {"tzlocal": module}),
            patch("codex_credit_monitor_windows.app.time.tzname", ("CET", "CEST")),
        ):
            self.assertEqual(_timezone_name(None), "Europe/Stockholm")

    def test_timestamp_is_formatted_in_the_selected_timezone(self):
        value = datetime(2025, 1, 1, 1, 30, tzinfo=timezone.utc)

        self.assertEqual(
            _format_in_timezone(value, "America/New_York", "%d %b %Y, %H:%M"),
            "31 Dec 2024, 20:30",
        )


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
            app.forecast = Mock()
            app.forecast_row = Mock()
            app.forecast_tooltip = Mock()
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


class HoverTooltipTests(TestCase):
    def setUp(self):
        self.tooltip = _HoverTooltip.__new__(_HoverTooltip)
        self.tooltip.row = Mock()
        self.tooltip.text = Mock()
        self.tooltip.text.get.return_value = "Based on 16 readings."
        self.tooltip.pending = None
        self.tooltip.popup = None

    def test_hover_is_delayed_and_leaving_cancels_it(self):
        self.tooltip.row.after.return_value = "timer"
        self.tooltip._schedule()
        self.tooltip.row.after.assert_called_once_with(400, self.tooltip._show)
        self.tooltip.hide()
        self.tooltip.row.after_cancel.assert_called_once_with("timer")
        self.assertIsNone(self.tooltip.pending)

    def test_clearing_text_destroys_an_open_tooltip(self):
        popup = self.tooltip.popup = Mock()
        self.tooltip.set_text("")
        self.tooltip.text.set.assert_called_once_with("")
        popup.destroy.assert_called_once_with()
        self.assertIsNone(self.tooltip.popup)
        self.tooltip.hide()
        popup.destroy.assert_called_once_with()

    def test_delayed_callback_does_not_show_a_hidden_row(self):
        self.tooltip.row.winfo_ismapped.return_value = False
        with patch("codex_credit_monitor_windows.app.tk.Toplevel") as popup:
            self.tooltip._show()
        popup.assert_not_called()


class PaceStatusTests(TestCase):
    def test_each_pace_state_gets_its_agreed_symbol(self):
        expected = {
            PaceState.BEHIND: "Safe  ✅",
            PaceState.ON_PACE: "On pace  🎯",
            PaceState.AHEAD: "Ahead  🟠",
            PaceState.CRITICAL: "Critically ahead  ⚠️",
            PaceState.UNAVAILABLE: "Unavailable  ❔",
        }
        for state, label in expected.items():
            with self.subTest(state=state):
                self.assertEqual(_format_pace_status(state), label)

    def test_stale_or_failed_refresh_keeps_one_warning_instead_of_a_checkmark(self):
        for state in PaceState:
            with self.subTest(state=state):
                self.assertEqual(
                    _format_pace_status(state, warning=True), f"{state.value}  ⚠️"
                )

    def test_exhaustion_overrides_pace_until_the_period_ends(self):
        app = MonitorApplication.__new__(MonitorApplication)
        app.settings = Settings(schedule=Schedule(mode=UsageMode.PERSONAL))
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        start, end = now - timedelta(days=7), now + timedelta(minutes=1)
        app.values = {
            key: Mock()
            for key in ("spent", "left", "working", "pace", "reset", "updated")
        }
        app.labels = {key: Mock() for key in ("spent", "left", "working")}
        app.tray = Mock()
        app.status = Mock()
        app.message = Mock()
        app.last_error = None
        app._hide_forecast = Mock()
        app.store = Mock()
        app.store.observations.return_value = []
        app.result = Mock()
        app.result.window = Mock(
            start=start, schedule=app.settings.schedule, timezone_name="UTC"
        )
        with patch("codex_credit_monitor_windows.app.datetime") as clock:
            for metric in UsageMetric:
                for used in (99, 100, 101):
                    with self.subTest(metric=metric, used=used):
                        clock.now.return_value = now
                        app.result.observation = Observation(
                            "u",
                            "w",
                            Decimal(100),
                            Decimal(used),
                            end,
                            now,
                            start,
                            metric,
                        )
                        app._refresh_display()
                        # Near reset, pace alone says On pace even at 100%.
                        app.status.set.assert_called_with(
                            "On pace  🎯" if used < 100 else "Allowance exhausted  ❌"
                        )
                        clock.now.return_value = end
                        app._refresh_display()
                        app.status.set.assert_called_with("Period ended  ❔")
                        self.assertIn("Refresh", app.message.set.call_args.args[0])
            clock.now.return_value = now
            app.result.observation = Observation(
                "u",
                "w",
                Decimal(100),
                Decimal(100),
                end,
                now - timedelta(hours=1),
                start,
            )
            app.last_error = "Refresh failed."
            app._refresh_display()
            app.status.set.assert_called_with("Allowance exhausted  ❌")
            self.assertIn("stale", app.message.set.call_args.args[0])
            self.assertIn("Refresh failed", app.message.set.call_args.args[0])

            app.result = None
            for refreshing, label in (
                (True, "Refreshing…  ⏳"),
                (False, "Usage unavailable  ❔"),
            ):
                app.refreshing = refreshing
                app._refresh_display()
                app.status.set.assert_called_with(label)


class ForecastDisplayTests(TestCase):
    def test_forecast_dates_use_readable_dates_or_today_with_a_countdown(self):
        now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
        cases = (
            (timedelta(days=1, hours=4), "Tuesday, 15 September at 19:00"),
            (timedelta(hours=4), "Today at 19:00 (in 4 hours)"),
            (timedelta(hours=1, minutes=1), "Today at 16:01 (in 1 hour 1 minute)"),
            (timedelta(minutes=20), "Today at 15:20 (in 20 minutes)"),
            (timedelta(seconds=20), "Today at 15:00 (in less than a minute)"),
            (timedelta(), "Today at 15:00 (estimate passed)"),
            (timedelta(minutes=-1), "Today at 14:59 (estimate passed)"),
        )
        for offset, expected in cases:
            with self.subTest(offset=offset):
                self.assertEqual(
                    _format_forecast_date(now + offset, "Europe/Stockholm", now),
                    expected,
                )

    def test_today_uses_the_configured_timezone_not_the_utc_date(self):
        now = datetime(2026, 9, 14, 23, tzinfo=timezone.utc)
        self.assertEqual(
            _format_forecast_date(now + timedelta(hours=2), "Europe/Stockholm", now),
            "Today at 03:00 (in 2 hours)",
        )
        now = datetime(2026, 9, 14, 21, tzinfo=timezone.utc)
        self.assertEqual(
            _format_forecast_date(now + timedelta(hours=2), "Europe/Stockholm", now),
            "Tuesday, 15 September at 01:00",
        )

    def test_countdown_measures_elapsed_time_across_the_dst_clock_change(self):
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Stockholm")
        now = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
        later = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=1)
        self.assertEqual(
            _format_forecast_date(later, "Europe/Stockholm", now),
            "Today at 02:30 (in 1 hour)",
        )

    def test_forecast_uses_current_history_even_when_graph_shows_an_old_period(self):
        with TemporaryDirectory() as directory, ExitStack() as resources:
            app = MonitorApplication.__new__(MonitorApplication)
            app.store = HistoryStore(Path(directory) / "history.sqlite")
            resources.callback(app.store.close)
            app.settings = Settings(
                schedule=Schedule(mode=UsageMode.PERSONAL),
                timezone_name="Europe/Stockholm",
            )
            start = datetime(2025, 1, 6, 8, tzinfo=timezone.utc)
            for offset in (-7, 0):
                period_start = start + timedelta(days=offset)
                for hour in range(3):
                    app.result = app.store.commit(
                        Observation(
                            "u",
                            "w",
                            Decimal(100),
                            Decimal(10 * hour),
                            period_start + timedelta(days=7),
                            period_start + timedelta(hours=hour),
                            period_start,
                            UsageMetric.PLAN_USAGE,
                        ),
                        app.settings.schedule,
                        app.settings.thresholds,
                        app.settings.timezone_name,
                    )
                if offset == -7:
                    app.selected_window = app.result.window.id
            app.values = {
                key: Mock()
                for key in ("spent", "left", "working", "pace", "reset", "updated")
            }
            app.labels = {key: Mock() for key in ("spent", "left", "working")}
            app.tray = Mock()
            app.status = Mock()
            app.message = Mock()
            app.forecast = Mock()
            app.forecast_row = Mock()
            app.forecast_tooltip = Mock()
            app.last_error = None
            with patch("codex_credit_monitor_windows.app.datetime") as clock:
                clock.now.return_value = start + timedelta(hours=2)
                app._refresh_display()
                app.forecast.set.assert_called_with("Today at 19:00 (in 8 hours)")
                app.forecast_row.grid.assert_called_once_with()
                text = app.forecast_tooltip.set_text.call_args.args[0]
                self.assertIn(
                    "run out of plan allowance around Monday, 06 January 2025 at 19:00 CET",
                    text,
                )
                self.assertIn("3 readings over 2.0 calendar hours", text)
                self.assertIn("last 1 active day", text)
                self.assertIn("idle time through the latest reading", text)
                self.assertNotEqual(app.selected_window, app.result.window.id)
                # Minute ticks must withdraw a forecast when data goes stale.
                clock.now.return_value += timedelta(minutes=30)
                app._refresh_display()
                app.forecast.set.assert_called_with("")
                app.forecast_tooltip.set_text.assert_called_with("")
                app.forecast_row.grid_remove.assert_called_once_with()

                # Every non-warning state stays out of the details list.
                clock.now.return_value = start + timedelta(hours=2)
                for state in ForecastState:
                    if state is ForecastState.RUNS_OUT:
                        continue
                    with (
                        self.subTest(state=state),
                        patch(
                            "codex_credit_monitor_windows.app.estimate_usage",
                            return_value=UsageForecast(state),
                        ),
                    ):
                        app.forecast_row.reset_mock()
                        app._refresh_display()
                        app.forecast_row.grid.assert_not_called()
                        app.forecast_row.grid_remove.assert_called_once_with()
                        app.forecast_tooltip.set_text.assert_called_with("")

                app.result = None
                app.refreshing = False
                app.forecast_row.reset_mock()
                app._refresh_display()
                app.forecast_row.grid_remove.assert_called_once_with()
                app.forecast_tooltip.set_text.assert_called_with("")

    def test_prediction_in_the_past_requests_a_refresh_instead_of_a_future_claim(self):
        now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
        reading = Observation(
            "u", "w", Decimal(100), Decimal(99), now + timedelta(days=1), now
        )
        text = _forecast_message(
            UsageForecast(ForecastState.RUNS_OUT, now - timedelta(minutes=1), 3, 3600),
            reading,
            Schedule(),
            "UTC",
            now,
        )
        self.assertIn("may already be exhausted", text)
        self.assertIn("Refresh", text)
        self.assertIn("working hours", text)
        self.assertNotIn("you'll run out", text)


class RefreshHandoffTests(TestCase):
    def test_fetch_worker_queues_result_without_calling_tk(self):
        app = MonitorApplication.__new__(MonitorApplication)
        app.settings = Settings()
        app.refresh_results = queue.SimpleQueue()
        app.root = Mock()
        observation = Mock()

        with (
            patch("codex_credit_monitor_windows.app.read_credentials"),
            patch(
                "codex_credit_monitor_windows.app.fetch_usage",
                return_value=observation,
            ),
        ):
            app._fetch_worker()

        self.assertEqual(app.refresh_results.get_nowait(), (observation, None))
        self.assertEqual(app.root.mock_calls, [])

    def test_main_thread_poll_dispatches_queued_result(self):
        app = MonitorApplication.__new__(MonitorApplication)
        app.refresh_results = queue.SimpleQueue()
        observation = Mock()
        app.refresh_results.put((observation, None))
        app.root = Mock()
        app.closing = False
        app._commit_observation = Mock()
        app._finish_refresh = Mock()

        app._poll_refresh_results()

        app._commit_observation.assert_called_once_with(observation)
        app._finish_refresh.assert_not_called()
        self.assertEqual(app.root.after.call_args.args[0], 100)


class WindowSelectionTests(TestCase):
    def test_refresh_follows_current_period_but_preserves_a_historical_selection(self):
        for selection in ("current", "historical", "initial"):
            with (
                self.subTest(selection=selection),
                TemporaryDirectory() as directory,
                ExitStack() as resources,
            ):
                app = MonitorApplication.__new__(MonitorApplication)
                app.store = HistoryStore(Path(directory) / "history.sqlite")
                resources.callback(app.store.close)
                app.settings = Settings(timezone_name="UTC")
                app.graph = Mock()
                app.window_box = {}
                app.window_choice = Mock()
                app._finish_refresh = Mock()
                app._notify_critical = Mock()
                start = datetime(2025, 1, 6, tzinfo=timezone.utc)

                def sample(period_start, at):
                    return Observation(
                        "u",
                        "w",
                        Decimal(100),
                        Decimal(1),
                        period_start + timedelta(days=7),
                        at,
                        period_start,
                        UsageMetric.PLAN_USAGE,
                    )

                def commit(item):
                    return app.store.commit(
                        item, app.settings.schedule, app.settings.thresholds, "UTC"
                    )

                historical = commit(
                    sample(start - timedelta(days=7), start - timedelta(days=6))
                )
                current = commit(sample(start, start + timedelta(days=1)))
                app.result = current if selection != "initial" else None
                app.selected_window = {
                    "current": current.window.id,
                    "historical": historical.window.id,
                    "initial": None,
                }[selection]
                # An earlier reported reset means sorting by end date alone
                # would select the previous period ahead of the current one.
                incoming = sample(start - timedelta(days=1), start + timedelta(days=2))

                app._commit_observation(incoming)

                self.assertIsNone(app.last_error)
                self.assertNotEqual(app.result.window.id, current.window.id)
                expected_id = (
                    historical.window.id
                    if selection == "historical"
                    else app.result.window.id
                )
                self.assertEqual(app.selected_window, expected_id)
                plotted_window, readings = app.graph.update.call_args.args
                self.assertEqual(plotted_window.id, expected_id)
                self.assertEqual(len(readings), 1)
                app._finish_refresh.assert_called_once_with(None)
