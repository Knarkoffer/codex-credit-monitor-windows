import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from codex_credit_monitor_windows.domain import UsageMetric
from codex_credit_monitor_windows.usage import (
    Credentials,
    UsageError,
    decode_usage,
    fetch_usage,
    read_credentials,
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
        self.assertEqual(result.metric, UsageMetric.CREDITS)

    def test_personal_plan_uses_the_longest_rate_limit_window(self):
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 3,
                    "limit_window_seconds": 5 * 60 * 60,
                    "reset_at": 1735707600,
                },
                "secondary_window": {
                    "used_percent": 8,
                    "limit_window_seconds": 7 * 24 * 60 * 60,
                    "reset_at": 1736294400,
                },
            },
        }
        result = decode_usage(json.dumps(payload).encode(), self.now, self.credentials)
        self.assertEqual(result.metric, UsageMetric.PLAN_USAGE)
        self.assertEqual(result.used, 8)
        self.assertEqual(result.limit, 100)
        self.assertEqual(result.reset_at - result.window_start, timedelta(days=7))

    def test_token_plan_hint_prefers_personal_window_when_both_metrics_exist(self):
        credentials = Credentials(
            "secret",
            "workspace",
            "user",
            self.now + timedelta(hours=1),
            "pro",
        )
        payload = {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 604800,
                    "reset_at": 1736294400,
                }
            },
            "spend_control": {
                "individual_limit": {
                    "limit": 1000,
                    "used": 100,
                    "reset_at": 1738368000,
                }
            },
        }
        result = decode_usage(json.dumps(payload).encode(), self.now, credentials)
        self.assertEqual(result.metric, UsageMetric.PLAN_USAGE)
        self.assertEqual(result.used, 12)

    def test_enterprise_plan_keeps_individual_credit_limit(self):
        payload = {
            "plan_type": "enterprise_cbp_usage_based",
            "rate_limit": {
                "secondary_window": {
                    "used_percent": 8,
                    "limit_window_seconds": 604800,
                    "reset_at": 1736294400,
                }
            },
            "spend_control": {
                "individual_limit": {
                    "limit": 25000,
                    "used": 8000,
                    "reset_at": 1738368000,
                }
            },
        }
        credentials = Credentials(
            "secret",
            "workspace",
            "user",
            self.now + timedelta(hours=1),
            "plus",
        )
        result = decode_usage(json.dumps(payload).encode(), self.now, credentials)
        self.assertEqual(result.metric, UsageMetric.CREDITS)
        self.assertEqual(result.used, 8000)

    def test_reads_plan_type_from_the_id_token(self):
        access_token = _token(
            {
                "exp": (self.now + timedelta(hours=1)).timestamp(),
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "workspace",
                    "chatgpt_user_id": "user",
                },
            }
        )
        id_token = _token(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_plan_type": "plus",
                }
            }
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": access_token,
                            "id_token": id_token,
                        }
                    }
                ),
                encoding="utf-8",
            )
            credentials = read_credentials(path=path)
        self.assertEqual(credentials.plan_type, "plus")

    @patch(
        "codex_credit_monitor_windows.usage.distribution_is_running",
        return_value=False,
    )
    @patch("codex_credit_monitor_windows.usage.os.name", "nt")
    def test_stopped_configured_wsl_distribution_has_specific_error(
        self, _running_state
    ):
        with self.assertRaises(UsageError) as caught:
            read_credentials(wsl_distro="Ubuntu-24.04")

        self.assertEqual(
            str(caught.exception),
            "Selected WSL distribution 'Ubuntu-24.04' is not started yet, "
            "cannot access token.",
        )

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

    def test_expired_wsl_token_explains_how_to_renew_it(self):
        with self.assertRaisesRegex(
            UsageError,
            "Start Codex in that WSL distribution to renew it",
        ) as caught:
            fetch_usage(
                Credentials(
                    "secret",
                    "workspace",
                    "user",
                    self.now - timedelta(seconds=1),
                    wsl_distro="Ubuntu-24.04",
                ),
                self.now,
            )
        self.assertIn("Ubuntu-24.04", str(caught.exception))


def _token(payload):
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return f"header.{encoded}.signature"
