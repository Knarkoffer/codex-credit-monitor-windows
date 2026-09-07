from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path


PACKAGE_NAME = "codex-credit-monitor-windows"


def _current_version() -> str:
    source_version = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        value = source_version.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value

    try:
        return package_version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "unknown"


__version__ = _current_version()
VERSION_LABEL = f"Version {__version__}"
