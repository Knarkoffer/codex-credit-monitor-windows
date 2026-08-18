from __future__ import annotations

import os
import subprocess


def distributions() -> list[str]:
    """Return installed WSL distributions with Windows' default listed first."""
    if os.name != "nt":
        return []
    try:
        names = [
            line.strip()
            for line in _decode_wsl_output(
                subprocess.run(
                    ["wsl.exe", "--list", "--quiet"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                ).stdout
            ).splitlines()
            if line.strip()
        ]
        verbose = _decode_wsl_output(
            subprocess.run(
                ["wsl.exe", "--list", "--verbose"],
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout
        ).splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    default = default_distribution(names, verbose)
    return ([default] if default else []) + [name for name in names if name != default]


def default_distribution(names: list[str], listing: list[str]) -> str | None:
    """Identify the starred distribution in `wsl.exe --list --verbose` output.

    Headers and running-state words are localized by Windows, but the leading
    asterisk and distribution names are stable.
    """
    for line in listing:
        candidate = line.lstrip()
        if not candidate.startswith("*"):
            continue
        candidate = candidate[1:].lstrip()
        for name in sorted(names, key=len, reverse=True):
            if candidate == name or (
                candidate.startswith(name)
                and candidate[len(name) :].startswith((" ", "\t"))
            ):
                return name
    return names[0] if len(names) == 1 else None


def _decode_wsl_output(data: bytes) -> str:
    """Decode console-style UTF-16 output emitted by wsl.exe when piped."""
    if (
        data.startswith((b"\xff\xfe", b"\xfe\xff"))
        or data.count(b"\0") > len(data) // 4
    ):
        return data.decode("utf-16")
    return data.decode("utf-8", errors="replace")
