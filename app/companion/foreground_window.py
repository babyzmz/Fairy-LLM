from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ForegroundWindow:
    process_name: str
    window_title: str
    exe_path: str
    process_id: int


def get_foreground_window() -> ForegroundWindow | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buffer, length + 1)
    title = title_buffer.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_id = int(pid.value)
    if process_id <= 0:
        return None

    process_name = ""
    exe_path = ""
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
    if handle:
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                exe_path = buffer.value
                process_name = Path(exe_path).name
        finally:
            kernel32.CloseHandle(handle)

    return ForegroundWindow(
        process_name=process_name,
        window_title=title or "",
        exe_path=exe_path,
        process_id=process_id,
    )
