from __future__ import annotations

from dataclasses import dataclass


_BOUNDARY_CHARS = set(" \t\r\n,.;:!?，。！？；：、")


@dataclass(slots=True)
class CharacterTimestamp:
    text: str
    start: float
    end: float


def build_character_timestamps(text: str, duration_sec: float) -> list[CharacterTimestamp]:
    cleaned = text or ""
    if not cleaned:
        return []

    count = len(cleaned)
    safe_duration = max(duration_sec, 0.08 * count, 0.12)
    step = safe_duration / max(count, 1)
    timestamps: list[CharacterTimestamp] = []
    cursor = 0.0
    for char in cleaned:
        start = cursor
        end = cursor + step
        timestamps.append(CharacterTimestamp(text=char, start=start, end=end))
        cursor = end
    return timestamps


class StreamingTextAllocator:
    """Allocate sentence text to streamed audio chunks using a stable fallback heuristic."""

    def __init__(self, text: str, chars_per_second: float = 7.2) -> None:
        self._remaining = text.strip()
        self._chars_per_second = max(2.0, chars_per_second)

    def consume(self, duration_sec: float, *, is_final: bool = False) -> str:
        remaining = self._remaining.strip()
        if not remaining:
            self._remaining = ""
            return ""

        if is_final:
            self._remaining = ""
            return remaining

        target_chars = max(1, round(max(duration_sec, 0.12) * self._chars_per_second))
        split_at = min(len(remaining), target_chars)
        split_at = self._prefer_boundary(remaining, split_at)

        chunk_text = remaining[:split_at].strip()
        tail = remaining[split_at:].lstrip()
        if not chunk_text:
            chunk_text = remaining[: max(1, split_at)]
            tail = remaining[len(chunk_text) :].lstrip()
        self._remaining = tail
        return chunk_text

    def flush(self) -> str:
        tail = self._remaining.strip()
        self._remaining = ""
        return tail

    def _prefer_boundary(self, text: str, split_at: int) -> int:
        if split_at >= len(text):
            return len(text)

        window_start = max(1, split_at - 6)
        window_end = min(len(text), split_at + 6)

        for idx in range(split_at, window_end):
            if text[idx - 1] in _BOUNDARY_CHARS:
                return idx
        for idx in range(split_at, window_start, -1):
            if text[idx - 1] in _BOUNDARY_CHARS:
                return idx
        return split_at
