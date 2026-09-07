import re
import tomllib
from pathlib import Path
from unittest import TestCase

from codex_credit_monitor_windows import __version__
from codex_credit_monitor_windows.version import VERSION_LABEL


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VersionTests(TestCase):
    def test_source_version_is_valid_and_exposed_by_application(self):
        source_version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertRegex(source_version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(__version__, source_version)
        self.assertEqual(VERSION_LABEL, f"Version {source_version}")

    def test_package_metadata_uses_version_file(self):
        pyproject = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertIn("version", pyproject["project"]["dynamic"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["dynamic"]["version"],
            {"file": "VERSION"},
        )

    def test_changelog_has_current_version_entry(self):
        changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertRegex(
            changelog,
            rf"(?m)^## {re.escape(__version__)} - \d{{4}}-\d{{2}}-\d{{2}}$",
        )
