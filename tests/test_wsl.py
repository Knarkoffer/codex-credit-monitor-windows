from unittest import TestCase

from codex_credit_monitor_windows.wsl import _decode_wsl_output, default_distribution


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
