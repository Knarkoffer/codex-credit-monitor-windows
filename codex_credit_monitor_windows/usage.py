from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .domain import Observation


USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_CLAIM = "https://api.openai.com/auth"


class UsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    access_token: str
    account_id: str | None
    user_id: str | None
    expires_at: datetime | None


def read_credentials(
    wsl_distro: str | None = None, path: Path | None = None
) -> Credentials:
    """Read only an existing Codex login; never use its refresh token."""
    try:
        if path is not None:
            source = path.read_text(encoding="utf-8")
        else:
            source = _read_auth_file(wsl_distro)
        root = json.loads(source)
        tokens = root["tokens"]
        access_token = tokens.get("access_token") or tokens.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise KeyError("access token")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        raise UsageError(
            "Codex OAuth credentials were not found or could not be read."
        ) from error
    access_claims, id_claims = (
        _jwt_payload(access_token) or {},
        _jwt_payload(tokens.get("id_token") or tokens.get("idToken") or "") or {},
    )
    auth = access_claims.get(AUTH_CLAIM) or id_claims.get(AUTH_CLAIM) or {}
    account_id = (
        tokens.get("account_id")
        or tokens.get("accountId")
        or auth.get("chatgpt_account_id")
    )
    user_id = auth.get("chatgpt_user_id") or auth.get("user_id") or id_claims.get("sub")
    expires = access_claims.get("exp")
    expires_at = (
        datetime.fromtimestamp(float(expires), timezone.utc)
        if isinstance(expires, (int, float))
        else None
    )
    return Credentials(access_token, _string(account_id), _string(user_id), expires_at)


def _read_auth_file(wsl_distro: str | None) -> str:
    override = os.environ.get("CODEX_CREDIT_MONITOR_AUTH_PATH")
    if override:
        return Path(override).read_text(encoding="utf-8")
    if os.name != "nt":
        return (Path.home() / ".codex" / "auth.json").read_text(encoding="utf-8")
    distro = wsl_distro or os.environ.get("CODEX_CREDIT_MONITOR_WSL_DISTRO")
    if distro:
        return _read_wsl_auth_file(distro)
    native_path = (
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".codex" / "auth.json"
    )
    if native_path.is_file():
        return native_path.read_text(encoding="utf-8")
    return _read_wsl_auth_file(None)


def _read_wsl_auth_file(distro: str | None) -> str:
    command = (
        ["wsl.exe"]
        + (["-d", distro] if distro else [])
        + ["--", "sh", "-lc", 'cat "$HOME/.codex/auth.json"']
    )
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=10
    )
    return completed.stdout


def fetch_usage(credentials: Credentials, now: datetime | None = None) -> Observation:
    observed_at = now or datetime.now(timezone.utc)
    if credentials.expires_at is None:
        raise UsageError(
            "The Codex OAuth token has no usable expiration time. Run `codex login`, then refresh this monitor."
        )
    if credentials.expires_at <= observed_at:
        raise UsageError(
            "The Codex OAuth token has expired. Run `codex login`, then refresh this monitor."
        )
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {credentials.access_token}",
            "User-Agent": "codex-cli",
        },
        method="GET",
    )
    if credentials.account_id:
        request.add_header("ChatGPT-Account-Id", credentials.account_id)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise UsageError(f"The usage service returned HTTP {response.status}.")
            data = response.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise UsageError(
                "The usage service rejected the current OAuth credentials."
            ) from error
        if error.code == 402:
            raise UsageError(
                "The selected ChatGPT account is not currently eligible for Codex credit data (HTTP 402). Check its Codex plan or credits, then run `codex login` in WSL if you need to select a different account."
            ) from error
        raise UsageError(f"The usage service returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UsageError("The usage service could not be reached.") from error
    return decode_usage(data, observed_at, credentials)


def decode_usage(
    data: bytes, observed_at: datetime, credentials: Credentials
) -> Observation:
    try:
        root = json.loads(data)
        if not isinstance(root, dict):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise UsageError("The usage service did not return a JSON object.") from error
    user_id, account_id = (
        _string(root.get("user_id")) or credentials.user_id,
        _string(root.get("account_id")) or credentials.account_id,
    )
    node = _limit_node(root)
    if not user_id or not account_id:
        raise UsageError("The usage response did not identify a user and workspace.")
    if node is None:
        raise UsageError(
            "The usage response did not contain an individual credit limit."
        )
    try:
        return Observation(
            user_id,
            account_id,
            _decimal(node.get("limit")),
            _decimal(node.get("used")),
            _timestamp(
                node.get("reset_at", node.get("resets_at", node.get("resetsAt")))
            ),
            observed_at,
        )
    except (ValueError, TypeError, InvalidOperation) as error:
        raise UsageError(
            "The usage response contained incomplete or invalid credit values."
        ) from error


def _limit_node(root: dict[str, Any]) -> dict[str, Any] | None:
    for container in (root, root.get("rate_limit"), root.get("spend_control")):
        if isinstance(container, dict):
            for key in ("individual_limit", "individualLimit"):
                if isinstance(container.get(key), dict):
                    return container[key]
    return None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError
    return result


def _timestamp(value: Any) -> datetime:
    if isinstance(value, bool) or value is None:
        raise ValueError
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    try:
        return datetime.fromtimestamp(float(str(value).strip()), timezone.utc)
    except ValueError:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)


def _jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return decoded if isinstance(decoded, dict) else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
