"""Real Windows system backend.

Launching uses ``ShellExecuteW`` (via ``os.startfile``) — the same path as the
Run dialog, so App Paths, app-execution aliases, URIs and UAC elevation behave
exactly as the user expects. Observation uses ``EnumWindows`` /
``GetForegroundWindow`` / DWM cloaking and ``psutil``. UWP apps, whose
top-level window belongs to ``ApplicationFrameHost.exe``, are resolved to the
process that actually hosts the content.

Phase 2 adds UI Automation for interacting *inside* windows.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - guarded import
    raise ImportError("WindowsSystemBackend is only available on Windows")

import asyncio
import ctypes
import os
import re
import shutil
import winreg
from ctypes import wintypes

import psutil

from jarvis.tools.system.backend import (
    EnvironmentSnapshot,
    LaunchError,
    ProcessInfo,
    SystemBackend,
    WindowInfo,
)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_ole32 = ctypes.WinDLL("ole32")

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = wintypes.LONG
_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_ERROR_CANCELLED = 1223
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4
_FRAME_HOST = "applicationframehost.exe"
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
_URI = re.compile(r"^[a-z][a-z0-9+.-]+:(?![\\/])", re.IGNORECASE)
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def _title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    result = _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return result == 0 and cloaked.value != 0


def _is_app_window(hwnd: int) -> bool:
    """Visible, titled, unowned, non-tool, non-cloaked top-level window."""
    if not _user32.IsWindowVisible(hwnd):
        return False
    if _user32.GetWindowTextLengthW(hwnd) <= 0:
        return False
    if _user32.GetWindow(hwnd, _GW_OWNER):
        return False
    if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
        return False
    if _class_name(hwnd) in _SHELL_CLASSES:
        return False
    return not _is_cloaked(hwnd)


def _frame_host_child_pid(hwnd: int, host_pid: int) -> int | None:
    """The process rendering a UWP app inside an ApplicationFrameHost window."""
    found: list[int] = []

    def callback(child: int | None, _lparam: int) -> bool:
        try:
            child_pid = _pid(child or 0)
            if child_pid and child_pid != host_pid:
                found.append(child_pid)
                return False
        except Exception:  # never raise inside a ctypes callback
            return False
        return True

    _user32.EnumChildWindows(hwnd, _WNDENUMPROC(callback), 0)
    return found[0] if found else None


def _process_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name")
        if name:
            names[int(proc.info["pid"])] = str(name).lower()
    return names


def _process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except (psutil.Error, OSError):
        return ""


def _snapshot() -> EnvironmentSnapshot:
    names = _process_names()
    foreground = int(_user32.GetForegroundWindow() or 0)
    windows: list[WindowInfo] = []

    def callback(hwnd: int | None, _lparam: int) -> bool:
        try:
            handle = int(hwnd or 0)
            if not handle or not _is_app_window(handle):
                return True
            pid = _pid(handle)
            name = names.get(pid, "")
            if name == _FRAME_HOST:
                child = _frame_host_child_pid(handle, pid)
                if child:
                    pid, name = child, names.get(child) or _process_name(child)
            windows.append(
                WindowInfo(
                    handle=handle,
                    title=_title(handle),
                    pid=pid,
                    process_name=name,
                    is_foreground=handle == foreground,
                )
            )
        except Exception:  # never raise inside a ctypes callback
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    processes = tuple(ProcessInfo(pid=pid, name=name) for pid, name in names.items())
    return EnvironmentSnapshot(windows=tuple(windows), processes=processes)


def _active_window() -> WindowInfo | None:
    handle = int(_user32.GetForegroundWindow() or 0)
    if not handle:
        return None
    pid = _pid(handle)
    name = _process_name(pid)
    if name == _FRAME_HOST:
        child = _frame_host_child_pid(handle, pid)
        if child:
            pid, name = child, _process_name(child)
    return WindowInfo(
        handle=handle, title=_title(handle), pid=pid, process_name=name, is_foreground=True
    )


def _uri_registered(scheme: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme):
            return True
    except OSError:
        return False


def _app_path(executable: str) -> str | None:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"{_APP_PATHS}\{executable}") as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip('"'))
        if os.path.isfile(path):
            return path
    return None


def _resolve(candidate: str) -> str | None:
    if _URI.match(candidate):
        return candidate if _uri_registered(candidate.split(":", 1)[0]) else None
    expanded = os.path.expandvars(candidate)
    if "%" in expanded:  # unresolved environment variable
        return None
    if os.path.isabs(expanded):
        return expanded if os.path.isfile(expanded) else None
    return _app_path(expanded) or shutil.which(expanded)


def _launch(target: str) -> None:
    # ShellExecute may delegate to shell extensions and requires COM on the
    # calling thread (worker threads from asyncio.to_thread have none).
    initialized = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
    try:
        _shell_execute(target)
    finally:
        if initialized >= 0:  # S_OK or S_FALSE must be balanced
            _ole32.CoUninitialize()


def _shell_execute(target: str) -> None:
    try:
        os.startfile(target)
    except FileNotFoundError as exc:
        raise LaunchError(
            "app_not_found", "Windows couldn't find the application.", str(exc)
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) == _ERROR_CANCELLED:
            raise LaunchError(
                "cancelled_by_user", "The Windows security prompt was cancelled.", str(exc)
            ) from exc
        raise LaunchError(
            "launch_failed", "Windows refused to start the application.", str(exc)
        ) from exc


class WindowsSystemBackend(SystemBackend):
    name = "windows"
    simulated = False

    async def snapshot(self) -> EnvironmentSnapshot:
        return await asyncio.to_thread(_snapshot)

    async def active_window(self) -> WindowInfo | None:
        return await asyncio.to_thread(_active_window)

    async def resolve(self, candidate: str) -> str | None:
        return await asyncio.to_thread(_resolve, candidate)

    async def launch(self, target: str) -> None:
        await asyncio.to_thread(_launch, target)
