from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .domain import Schedule, Thresholds, UsageMode


def _is_domain_joined(netapi32=None) -> bool:
    """Read local Windows domain membership; unknown status defaults to personal."""
    if os.name != "nt":
        return False
    try:
        if netapi32 is None:
            netapi32 = ctypes.WinDLL("netapi32.dll")
        get_join = netapi32.NetGetJoinInformation
        get_join.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(ctypes.c_int),
        ]
        get_join.restype = ctypes.c_uint32
        free_buffer = netapi32.NetApiBufferFree
        free_buffer.argtypes = [ctypes.c_void_p]
        free_buffer.restype = ctypes.c_uint32
        name = ctypes.c_wchar_p()
        status = ctypes.c_int()
        try:
            result = get_join(None, ctypes.byref(name), ctypes.byref(status))
            # NETSETUP_JOIN_STATUS.NetSetupDomainName is 3.
            return result == 0 and status.value == 3
        finally:
            if name:
                free_buffer(name)
    except (AttributeError, OSError):
        return False


def app_data_home() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "CodexCreditMonitor"
    return Path.home() / ".local" / "share" / "codex-credit-monitor-windows"


@dataclass(frozen=True)
class Settings:
    schedule: Schedule = Schedule(mode=UsageMode.PERSONAL)
    thresholds: Thresholds = Thresholds()
    notifications_enabled: bool = True
    wsl_distro: str | None = None
    timezone_name: str | None = None


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_home() / "settings.json"

    def load(self) -> Settings:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            mode = UsageMode.WORK if _is_domain_joined() else UsageMode.PERSONAL
            settings = Settings(schedule=Schedule(mode=mode))
            self.save(settings)
            return settings
        except (OSError, UnicodeError):
            return Settings()
        try:
            raw = json.loads(content)
            distro, timezone_name = raw.get("wsl_distro"), raw.get("timezone_name")
            return Settings(
                Schedule(
                    int(raw["start_minutes"]),
                    int(raw["end_minutes"]),
                    UsageMode(raw.get("usage_mode", "work")),
                ),
                Thresholds(
                    Decimal(raw["tolerance"]),
                    Decimal(raw["critical_base"]),
                    Decimal(raw["critical_growth"]),
                ),
                bool(raw.get("notifications_enabled", True)),
                distro.strip() if isinstance(distro, str) and distro.strip() else None,
                (
                    timezone_name.strip()
                    if isinstance(timezone_name, str) and timezone_name.strip()
                    else None
                ),
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "usage_mode": settings.schedule.mode.value,
            "start_minutes": settings.schedule.start_minutes,
            "end_minutes": settings.schedule.end_minutes,
            "tolerance": str(settings.thresholds.tolerance),
            "critical_base": str(settings.thresholds.critical_base),
            "critical_growth": str(settings.thresholds.critical_growth),
            "notifications_enabled": settings.notifications_enabled,
            "wsl_distro": settings.wsl_distro,
            "timezone_name": settings.timezone_name,
        }
        fd, temporary = tempfile.mkstemp(
            prefix="settings-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, indent=2)
                output.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
