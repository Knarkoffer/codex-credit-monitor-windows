# Codex Credit Monitor for Windows + WSL

A small native Windows desktop monitor for the Codex credit allocation of the
active ChatGPT account.  Its UI, storage, and networking use the Python standard library.

It is designed for the common setup where Codex runs inside WSL but the monitor
runs on Windows: on refresh it reads the WSL `~/.codex/auth.json` via `wsl.exe`.
It never writes credentials or uses a refresh token.  When a native Windows
Codex login exists, `%USERPROFILE%\\.codex\\auth.json` is preferred.

## Requirements

Install Python 3.12 or later for Windows from python.org and ensure `py` is on
`PATH`.

## Windows virtual environment (recommended)

Create an isolated environment in the checkout, activate it, and install the
timezone data used for daylight-saving-aware working-hour calculations:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

If your PowerShell execution policy prevents activation, use the venv's Python
directly; this has the same result:

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m codex_credit_monitor_windows
```

## Run on Windows

The simplest option is to double-click **`Launch Codex Credit Monitor.vbs`** in
Windows Explorer. It starts the monitor without showing a command-prompt
window. On its first run, it creates `.venv`, installs the required package,
and then opens the monitor. Python 3.12 or later must be installed first.

From PowerShell in this checkout:

```powershell
python -m codex_credit_monitor_windows
```

If you use more than one WSL distribution, set the one that holds your Codex
login before starting:

```powershell
$env:CODEX_CREDIT_MONITOR_WSL_DISTRO = "Ubuntu"
python -m codex_credit_monitor_windows
```

The app refreshes on launch and every 15 minutes.  Its settings and SQLite
history are stored under `%LOCALAPPDATA%\\CodexCreditMonitor` (or
`%APPDATA%\\CodexCreditMonitor` if `LOCALAPPDATA` is unavailable).
Set the IANA time zone (for example `Europe/Stockholm`) in **Settings**.

## Develop and test in WSL

Use a separate Linux virtual environment when developing from WSL. (If the
`venv` module is absent, install your distribution's `python3-venv` package.)

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
make PYTHON=python test
make PYTHON=python check
```

Install the checks into Git with `pre-commit install`. Ruff runs lint checks and
Black verifies formatting before each commit. Run all configured checks manually
with `pre-commit run --all-files`.

The tests are deterministic: they neither read your login nor use the network.
To run the UI from WSL, use `python3 -m codex_credit_monitor_windows`; WSLg
must be available for Tk to display a window.

## Privacy

The sole network request is `GET https://chatgpt.com/backend-api/wham/usage`.
Tokens, response bodies, account IDs, and credit values are not written to logs.
Failed refreshes preserve the last successful display and history.
