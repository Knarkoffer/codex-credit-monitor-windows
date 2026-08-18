from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
import urllib.error

from codex_credit_monitor_windows.usage import Credentials, UsageError, fetch_usage


class UsageRequestTests(TestCase):
    def test_402_has_an_actionable_message(self):
        credentials = Credentials(
            "secret",
            "workspace",
            "user",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        error = urllib.error.HTTPError(
            "https://example.invalid", 402, "Payment Required", {}, None
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(UsageError, "not currently eligible"):
                fetch_usage(credentials)
