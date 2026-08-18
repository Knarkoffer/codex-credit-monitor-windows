import json
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from codex_credit_monitor_windows.usage import (
    Credentials,
    UsageError,
    decode_usage,
    fetch_usage,
)


class UsageDecoderTests(TestCase):
    def setUp(self):
        self.now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.credentials = Credentials(
            "secret", "workspace", "user", self.now + timedelta(hours=1)
        )

    def test_decodes_numeric_strings_and_fallback_identity(self):
        payload = {
            "rate_limit": {
                "individual_limit": {
                    "limit": "100.25",
                    "used": "12.5",
                    "reset_at": "2025-02-01T00:00:00Z",
                }
            }
        }
        result = decode_usage(json.dumps(payload).encode(), self.now, self.credentials)
        self.assertEqual(str(result.limit), "100.25")
        self.assertEqual(result.workspace_id, "workspace")

    def test_rejects_partial_response_without_exposing_body(self):
        secret = "private-value-that-must-not-appear"
        with self.assertRaises(UsageError) as caught:
            decode_usage(
                json.dumps({"message": secret}).encode(), self.now, self.credentials
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_expired_token_does_not_use_the_network(self):
        with self.assertRaisesRegex(UsageError, "codex login"):
            fetch_usage(
                Credentials(
                    "secret", "workspace", "user", self.now - timedelta(seconds=1)
                ),
                self.now,
            )
