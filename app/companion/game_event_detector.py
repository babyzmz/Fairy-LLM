from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.companion.foreground_window import ForegroundWindow


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GameMatcher:
    name: str
    process_names: tuple[str, ...]
    title_keywords: tuple[str, ...]

    def matches(self, window: ForegroundWindow) -> bool:
        if self.process_names:
            process_lower = (window.process_name or "").lower()
            if any(needle.lower() == process_lower for needle in self.process_names):
                return True
        if self.title_keywords:
            title_lower = (window.window_title or "").lower()
            if any(needle.lower() in title_lower for needle in self.title_keywords):
                return True
        return False


@dataclass(slots=True)
class WhitelistConfig:
    matchers: tuple[GameMatcher, ...]
    poll_interval_seconds: float


DEFAULT_WHITELIST_PATH = Path("config/game_window_whitelist.json")


def load_whitelist(path: Path | str | None = None) -> WhitelistConfig:
    resolved = Path(path) if path else DEFAULT_WHITELIST_PATH
    if not resolved.exists():
        logger.warning("game_whitelist_missing path=%s", resolved)
        return WhitelistConfig(matchers=(), poll_interval_seconds=5.0)
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("game_whitelist_load_failed path=%s", resolved)
        return WhitelistConfig(matchers=(), poll_interval_seconds=5.0)

    games = raw.get("games") or []
    matchers: list[GameMatcher] = []
    for entry in games:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        process_names = tuple(str(item).strip() for item in entry.get("process") or [] if str(item).strip())
        title_keywords = tuple(str(item).strip() for item in entry.get("title_keywords") or [] if str(item).strip())
        if not process_names and not title_keywords:
            continue
        matchers.append(GameMatcher(name=name, process_names=process_names, title_keywords=title_keywords))

    poll = float(raw.get("poll_interval_seconds") or 5.0)
    poll = max(1.0, min(60.0, poll))
    return WhitelistConfig(matchers=tuple(matchers), poll_interval_seconds=poll)


def detect_game(window: ForegroundWindow | None, matchers: tuple[GameMatcher, ...]) -> str | None:
    if window is None:
        return None
    for matcher in matchers:
        if matcher.matches(window):
            return matcher.name
    return None
