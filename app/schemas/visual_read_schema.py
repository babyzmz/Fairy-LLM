from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class VisualReadSchema:
    region: str = ""
    summary: str = ""
    confidence: float | None = None
    source_url: str = ""
    visual_type: str = "unknown"
    screenshot_path: str = ""

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "VisualReadSchema":
        confidence: float | None = None
        raw_confidence = payload.get("confidence")
        if raw_confidence not in {None, ""}:
            try:
                confidence = float(raw_confidence)
            except (TypeError, ValueError):
                confidence = None
        return cls(
            region=str(payload.get("region", "") or "").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
            confidence=confidence,
            source_url=str(payload.get("source_url", "") or payload.get("url") or "").strip(),
            visual_type=str(payload.get("visual_type", "") or "unknown").strip() or "unknown",
            screenshot_path=str(payload.get("screenshot_path", "") or "").strip(),
        )

    def is_valid(self) -> bool:
        return bool(self.summary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
