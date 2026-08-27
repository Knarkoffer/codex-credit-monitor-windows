from unittest import TestCase
from unittest.mock import patch

from codex_credit_monitor_windows.app import (
    WINDOWS_APP_USER_MODEL_ID,
    MonitorApplication,
    _configure_windows_app_identity,
)


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
