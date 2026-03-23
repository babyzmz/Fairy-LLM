from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class NewsItemSchema:
    headline: str
    source: str = ""
    published_at: str = ""
    summary: str = ""
    image_path: str = ""
    image_url: str = ""
    url: str = ""

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "NewsItemSchema | None":
        headline = str(payload.get("headline") or payload.get("title") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not headline or not url:
            return None
        return cls(
            headline=headline,
            source=str(payload.get("source", "") or "").strip(),
            published_at=str(payload.get("published_at", "") or "").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
            image_path=str(payload.get("image_path", "") or payload.get("image_url", "") or "").strip(),
            image_url=str(payload.get("image_url", "") or "").strip(),
            url=url,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "source": self.source,
            "published_at": self.published_at,
            "summary": self.summary,
            "image_path": self.image_path,
            "image_url": self.image_url,
            "url": self.url,
        }


@dataclass(slots=True)
class NewsListSchema:
    title: str
    items: list[NewsItemSchema]

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "NewsListSchema":
        items: list[NewsItemSchema] = []
        for item in list(payload.get("items", []) or []):
            if not isinstance(item, dict):
                continue
            repaired = NewsItemSchema.repair(item)
            if repaired is not None:
                items.append(repaired)
        title = str(payload.get("title") or "News Briefing").strip() or "News Briefing"
        return cls(title=title, items=items)

    def is_valid(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "items": [item.to_dict() for item in self.items]}
