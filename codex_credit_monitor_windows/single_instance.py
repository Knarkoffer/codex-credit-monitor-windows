"""Windows-only single-instance support for the desktop monitor."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


APPLICATION_TITLE = "Codex Credit Monitor"
MUTEX_NAME = r"Local\CodexCreditMonitor-2C9C4060-7EB3-4327-A521-5D4A4E84D366"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class SingleInstance:
    """Keep one monitor process alive and activate it on a repeat launch."""

    def __init__(self) -> None:
        self._mutex: wintypes.HANDLE | None = None

    def acquire(self) -> bool:
        """Return whether this process may start the monitor.

        The monitor is also useful from non-Windows development environments,
        where this deliberately becomes a no-op.
        """
        if os.name != "nt":
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not mutex:
            # A failure to create a mutex should not prevent the monitor from
            # starting; normal application startup is safer than a false block.
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex)
            _activate_existing_window()
            return False

        self._mutex = mutex
        return True

    def release(self) -> None:
        if self._mutex is None or os.name != "nt":
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(self._mutex)
        self._mutex = None


def _activate_existing_window() -> None:
    """Restore and foreground the existing Tk window when it can be found."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL

    window = user32.FindWindowW(None, APPLICATION_TITLE)
    if window:
        user32.ShowWindow(window, SW_RESTORE)
        user32.SetForegroundWindow(window)
