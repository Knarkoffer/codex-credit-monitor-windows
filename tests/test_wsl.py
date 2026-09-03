from unittest import TestCase
from unittest.mock import Mock, call, patch

from codex_credit_monitor_windows.wsl import (
    _decode_wsl_output,
    default_distribution,
    distribution_is_running,
)


class WslTests(TestCase):
    def test_default_distribution_is_the_starred_distribution(self):
        names = ["Ubuntu", "Debian"]
        listing = [
            "  NAME      STATE           VERSION",
            "* Ubuntu    Running         2",
            "  Debian    Stopped         2",
        ]
        self.assertEqual(default_distribution(names, listing), "Ubuntu")

    def test_distribution_names_with_spaces_are_supported(self):
        names = ["Ubuntu", "My Linux"]
        listing = ["* My Linux  Running         2"]
        self.assertEqual(default_distribution(names, listing), "My Linux")

    def test_one_distribution_is_default_when_output_has_no_star(self):
        self.assertEqual(default_distribution(["Ubuntu"], ["Name"]), "Ubuntu")

    def test_utf16_wsl_output_is_decoded_without_null_character_gaps(self):
        self.assertEqual(
            _decode_wsl_output("Ubuntu\r\n".encode("utf-16")), "Ubuntu\r\n"
        )

    @patch("codex_credit_monitor_windows.wsl.subprocess.run")
    @patch("codex_credit_monitor_windows.wsl.os.name", "nt")
    def test_running_distribution_is_detected_without_parsing_state_words(self, run):
        run.side_effect = [
            Mock(stdout="Ubuntu\r\nMy Linux\r\n".encode("utf-16")),
            Mock(stdout="My Linux\r\n".encode("utf-16")),
        ]

        self.assertTrue(distribution_is_running("My Linux"))
        self.assertEqual(
            run.call_args_list,
            [
                call(
                    ["wsl.exe", "--list", "--quiet"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                ),
                call(
                    ["wsl.exe", "--list", "--running", "--quiet"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                ),
            ],
        )

    @patch("codex_credit_monitor_windows.wsl.subprocess.run")
    @patch("codex_credit_monitor_windows.wsl.os.name", "nt")
    def test_installed_distribution_absent_from_running_list_is_stopped(self, run):
        run.side_effect = [
            Mock(stdout="Ubuntu-24.04\r\n".encode("utf-16")),
            Mock(stdout=b""),
        ]

        self.assertFalse(distribution_is_running("Ubuntu-24.04"))

    @patch("codex_credit_monitor_windows.wsl.subprocess.run")
    @patch("codex_credit_monitor_windows.wsl.os.name", "nt")
    def test_unknown_distribution_has_unknown_running_state(self, run):
        run.return_value = Mock(stdout="Debian\r\n".encode("utf-16"))

        self.assertIsNone(distribution_is_running("Ubuntu"))
        run.assert_called_once()
