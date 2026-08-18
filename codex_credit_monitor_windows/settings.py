from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .domain import Schedule, Thresholds


def app_data_home() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "CodexCreditMonitor"
    return Path.home() / ".local" / "share" / "codex-credit-monitor-windows"


@dataclass(frozen=True)
class Settings:
    schedule: Schedule = Schedule()
    thresholds: Thresholds = Thresholds()
    notifications_enabled: bool = True
    wsl_distro: str | None = None
    timezone_name: str | None = None


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_home() / "settings.json"

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            distro, timezone_name = raw.get("wsl_distro"), raw.get("timezone_name")
            return Settings(
                Schedule(int(raw["start_minutes"]), int(raw["end_minutes"])),
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
        except (OSError, ValueError, TypeError, KeyError):
            return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
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
