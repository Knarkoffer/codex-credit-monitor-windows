@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Start this file by double-clicking it in Windows Explorer.
if /i not "%~1"=="--background" (
    start "" wscript.exe "%~dp0Launch Codex Credit Monitor.vbs"
    exit /b 0
)

cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Preparing Codex Credit Monitor for its first run...
    call :find_python
    if not defined PYTHON_COMMAND (
        set "CCM_ERROR_MESSAGE=Python 3.12 or later was not found. Install it from python.org, then start the monitor again."
        goto :error
    )

    !PYTHON_COMMAND! -m venv .venv
    if errorlevel 1 (
        set "CCM_ERROR_MESSAGE=Python was found, but it could not create the monitor's .venv folder. For details, run this CMD file with the --background argument."
        goto :error
    )

    "%PYTHON%" -m pip install .
    if errorlevel 1 (
        set "CCM_ERROR_MESSAGE=The monitor setup could not install its dependencies. For details, run this CMD file with the --background argument."
        goto :error
    )
)

"%PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 12))"
if errorlevel 1 (
    set "CCM_ERROR_MESSAGE=The existing .venv uses Python older than 3.12. Delete the .venv folder next to this launcher and start it again after installing Python 3.12 or later."
    goto :error
)

set "CCM_SOURCE_VERSION="
set /p "CCM_SOURCE_VERSION="<"VERSION"
if not defined CCM_SOURCE_VERSION (
    set "CCM_ERROR_MESSAGE=The monitor's VERSION file is missing or empty. Download a complete copy of the project and try again."
    goto :error
)

"%PYTHON%" -c "from importlib.metadata import version; import sys; raise SystemExit(version('codex-credit-monitor-windows') != sys.argv[1])" "!CCM_SOURCE_VERSION!" >nul 2>&1
if errorlevel 1 (
    echo Updating Codex Credit Monitor to version !CCM_SOURCE_VERSION!...
    "%PYTHON%" -m pip install --upgrade .
    if errorlevel 1 (
        set "CCM_ERROR_MESSAGE=The monitor could not update to version !CCM_SOURCE_VERSION!. For details, run this CMD file with the --background argument."
        goto :error
    )
)

"%PYTHON%" -c "import PIL, pystray" >nul 2>&1
if errorlevel 1 (
    echo Installing tray support...
    "%PYTHON%" -m pip install .
    if errorlevel 1 (
        set "CCM_ERROR_MESSAGE=The monitor could not install its tray-support dependencies. For details, run this CMD file with the --background argument."
        goto :error
    )
)

"%PYTHON%" -m codex_credit_monitor_windows
if errorlevel 1 (
    set "CCM_ERROR_MESSAGE=The monitor stopped unexpectedly. For details, run this CMD file with the --background argument."
    goto :error
)

endlocal
exit /b 0

:find_python
set "PYTHON_COMMAND="

rem Prefer the requested version, then any supported Python registered with the
rem Windows launcher, and finally a Python available directly on PATH.
py -3.12 -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 set "PYTHON_COMMAND=py -3.12"

if defined PYTHON_COMMAND exit /b 0
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 set "PYTHON_COMMAND=py -3"

if defined PYTHON_COMMAND exit /b 0
python -c "import sys; raise SystemExit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 set "PYTHON_COMMAND=python"

exit /b 0

:error
if not defined CCM_ERROR_MESSAGE set "CCM_ERROR_MESSAGE=Codex Credit Monitor could not start. For details, run this CMD file with the --background argument."
echo %CCM_ERROR_MESSAGE%
if defined CCM_ERROR_FILE > "%CCM_ERROR_FILE%" echo %CCM_ERROR_MESSAGE%
endlocal
exit /b 1
