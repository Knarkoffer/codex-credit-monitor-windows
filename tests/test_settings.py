import ctypes
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from codex_credit_monitor_windows.domain import Schedule, UsageMode
from codex_credit_monitor_windows.settings import (
    Settings,
    SettingsStore,
    _is_domain_joined,
)


class SettingsTests(TestCase):
    def test_first_launch_saves_domain_based_default_without_manual_save(self):
        for joined, mode in ((False, UsageMode.PERSONAL), (True, UsageMode.WORK)):
            with self.subTest(joined=joined), TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                with patch(
                    "codex_credit_monitor_windows.settings._is_domain_joined",
                    return_value=joined,
                ) as detect:
                    settings = SettingsStore(path).load()
                self.assertEqual(settings.schedule.mode, mode)
                detect.assert_called_once_with()
                self.assertEqual(json.loads(path.read_text())["usage_mode"], mode.value)
                with patch(
                    "codex_credit_monitor_windows.settings._is_domain_joined",
                    return_value=not joined,
                ) as detect:
                    self.assertEqual(SettingsStore(path).load(), settings)
                detect.assert_not_called()

    def test_user_override_survives_restart_in_either_direction(self):
        for joined in (False, True):
            with self.subTest(joined=joined), TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                with patch(
                    "codex_credit_monitor_windows.settings._is_domain_joined",
                    return_value=joined,
                ) as detect:
                    store = SettingsStore(path)
                    store.load()
                    chosen_mode = UsageMode.PERSONAL if joined else UsageMode.WORK
                    chosen = Settings(schedule=Schedule(540, 1080, chosen_mode))
                    store.save(chosen)
                    self.assertEqual(SettingsStore(path).load(), chosen)
                    detect.assert_called_once_with()

    def test_invalid_existing_settings_do_not_trigger_first_launch_detection(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("invalid json")
            with patch(
                "codex_credit_monitor_windows.settings._is_domain_joined"
            ) as detect:
                self.assertEqual(SettingsStore(path).load(), Settings())
            detect.assert_not_called()
            self.assertEqual(path.read_text(), "invalid json")

    def test_both_modes_round_trip_with_saved_working_hours(self):
        with TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            for mode in UsageMode:
                with self.subTest(mode=mode):
                    settings = Settings(
                        schedule=Schedule(540, 1080, mode),
                        timezone_name="Europe/Stockholm",
                    )
                    store.save(settings)
                    self.assertEqual(store.load(), settings)

    def test_existing_settings_keep_work_mode_and_preferences(self):
        with TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            settings = Settings(
                schedule=Schedule(540, 1080),
                wsl_distro="Ubuntu",
                notifications_enabled=False,
            )
            store.save(settings)
            payload = json.loads(store.path.read_text())
            del payload["usage_mode"]
            store.path.write_text(json.dumps(payload))
            with patch(
                "codex_credit_monitor_windows.settings._is_domain_joined"
            ) as detect:
                self.assertEqual(store.load(), settings)
            detect.assert_not_called()


class DomainMembershipTests(TestCase):
    def api(self, status, result=0):
        api = Mock()

        def get_join(server, name_pointer, status_pointer):
            self.assertIsNone(server)
            ctypes.cast(name_pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
            ctypes.cast(status_pointer, ctypes.POINTER(ctypes.c_int))[0] = status
            return result

        api.NetGetJoinInformation.side_effect = get_join
        return api

    def test_only_domain_membership_selects_work_and_buffer_is_freed(self):
        for status in (0, 1, 2, 3):
            with self.subTest(status=status):
                api = self.api(status)
                with patch("codex_credit_monitor_windows.settings.os.name", "nt"):
                    self.assertEqual(_is_domain_joined(api), status == 3)
                api.NetApiBufferFree.assert_called_once()
                buffer = api.NetApiBufferFree.call_args.args[0]
                self.assertEqual(ctypes.cast(buffer, ctypes.c_void_p).value, 1234)

    def test_api_failure_defaults_to_personal_and_frees_returned_buffer(self):
        api = self.api(3, result=5)
        with patch("codex_credit_monitor_windows.settings.os.name", "nt"):
            self.assertFalse(_is_domain_joined(api))
        api.NetApiBufferFree.assert_called_once()

    def test_unavailable_windows_api_defaults_to_personal(self):
        with (
            patch("codex_credit_monitor_windows.settings.os.name", "nt"),
            patch(
                "codex_credit_monitor_windows.settings.ctypes.WinDLL",
                create=True,
                side_effect=OSError,
            ),
        ):
            self.assertFalse(_is_domain_joined())

    def test_non_windows_does_not_query_domain_membership(self):
        api = Mock()
        with patch("codex_credit_monitor_windows.settings.os.name", "posix"):
            self.assertFalse(_is_domain_joined(api))
        api.NetGetJoinInformation.assert_not_called()
