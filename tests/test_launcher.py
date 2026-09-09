import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@skipUnless(os.name == "nt", "Requires the Windows command interpreter")
class LauncherTests(TestCase):
    def test_source_update_starts_without_contacting_package_index(self):
        for argument in ("--diagnose", "--background"):
            with self.subTest(argument=argument), tempfile.TemporaryDirectory(
                prefix="monitor launcher "
            ) as directory:
                root = Path(directory)
                launcher = root / "Launch Codex Credit Monitor.cmd"
                # Use the test environment without copying a Windows venv.
                source = (PROJECT_ROOT / launcher.name).read_text()
                source = source.replace(
                    'set "PYTHON=.venv\\Scripts\\python.exe"',
                    f'set "PYTHON={sys.executable}"',
                )
                launcher.write_text(source)
                (root / "VERSION").write_text("999.0.0\n")
                for dependency in ("PIL", "pystray", "tzdata"):
                    (root / f"{dependency}.py").write_text("")
                # Fail locally if startup attempts any pip operation.
                (root / "pip.py").write_text(
                    "from pathlib import Path\n"
                    "Path('pip-called').touch()\n"
                    "raise SystemExit(1)\n"
                )
                package = root / "codex_credit_monitor_windows"
                package.mkdir()
                (package / "__init__.py").write_text("")
                (package / "__main__.py").write_text(
                    "from pathlib import Path\nPath('started').touch()\n"
                )

                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", str(launcher), argument],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((root / "started").exists(), result.stdout)
                self.assertFalse((root / "pip-called").exists())
