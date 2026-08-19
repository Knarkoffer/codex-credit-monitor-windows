from unittest import TestCase
from unittest.mock import patch

from codex_credit_monitor_windows.single_instance import SingleInstance


class SingleInstanceTests(TestCase):
    def test_non_windows_environment_allows_starting(self):
        instance = SingleInstance()

        with patch("codex_credit_monitor_windows.single_instance.os.name", "posix"):
            self.assertTrue(instance.acquire())
            instance.release()

