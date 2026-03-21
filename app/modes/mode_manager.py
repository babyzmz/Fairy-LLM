from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from app.settings.game_mode_settings import GameModeSettings, load_game_mode_settings


class ModeManager:
    def __init__(self) -> None:
        self._settings = load_game_mode_settings()
        self._active_mode = "game_mode" if self._settings.enabled else "normal_mode"
        self._last_switch_ts = 0.0
        self._listeners: list[Callable[[str], None]] = []

    @property
    def settings(self) -> GameModeSettings:
        return self._settings

    def reload_settings(self) -> None:
        self._settings = load_game_mode_settings()
        if not self._settings.enabled:
            self.set_mode("normal_mode")

    def add_listener(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)

    def set_mode(self, mode: str) -> None:
        if mode == self._active_mode:
            return
        self._active_mode = mode
        self._last_switch_ts = time.monotonic()
        for callback in list(self._listeners):
            try:
                callback(mode)
            except Exception:
                continue

    def get_active_mode(self) -> str:
        return self._active_mode

    def is_game_mode_active(self) -> bool:
        return self._active_mode == "game_mode"

    def refresh(self) -> str:
        settings = self._settings
        if not settings.enabled:
            self.set_mode("normal_mode")
            return self._active_mode
        if not settings.auto_switch_when_game_detected:
            self.set_mode("game_mode")
            return self._active_mode
        current_name = self._foreground_process_name().lower()
        should_be_game = current_name in set(settings.known_game_processes)
        target_mode = "game_mode" if should_be_game else "normal_mode"
        debounce = max(2, settings.auto_switch_debounce_seconds)
        if target_mode != self._active_mode and (time.monotonic() - self._last_switch_ts) >= debounce:
            self.set_mode(target_mode)
        return self._active_mode

    def _foreground_process_name(self) -> str:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value)
        if not process_id:
            return ""
        handle = kernel32.OpenProcess(0x1000, False, process_id)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
        return ""
