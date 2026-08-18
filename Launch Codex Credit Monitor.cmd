@echo off
setlocal

rem Start this file by double-clicking it in Windows Explorer.
if /i not "%~1"=="--background" (
    start "" wscript.exe "%~dp0Launch Codex Credit Monitor.vbs"
    exit /b 0
)

cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Preparing Codex Credit Monitor for its first run...
    py -3.12 -m venv .venv >nul 2>&1
    if errorlevel 1 py -3 -m venv .venv
    if errorlevel 1 goto :error

    "%PYTHON%" -m pip install .
    if errorlevel 1 goto :error
)

"%PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 12))"
if errorlevel 1 goto :error

"%PYTHON%" -m codex_credit_monitor_windows
if errorlevel 1 goto :error

endlocal
exit /b 0

:error
endlocal
exit /b 1
