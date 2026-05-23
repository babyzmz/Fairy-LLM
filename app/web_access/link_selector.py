from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_registry import SourceDescriptor


_IRRELEVANT_TERMS = ("support", "store", "privacy", "careers", "legal", "login", "sign in")
_TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "specs": ("spec", "specs", "specifications", "tech specs", "参数", "规格", "compare"),
    "release": ("newsroom", "announces", "announce", "press", "release", "发布", "新闻稿"),
    "news": ("news", "source", "stories", "科技", "新闻", "最新"),
    "product_lookup": ("product", "products", "产品"),
    "general_info": ("about", "overview", "docs", "learn", "details", "features", "blog"),
}


@dataclass(slots=True)
class ScoredLink:
    text: str
    url: str
    score: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "url": self.url,
            "score": self.score,
            "score_reasons": list(self.reasons),
        }


def select_candidate_links(
    *,
    task_type: str,
    entity: str,
    page_snapshot: dict[str, Any],
    source_descriptor: SourceDescriptor | None = None,
    max_links: int = 2,
) -> list[ScoredLink]:
    entity_text = str(entity or "").strip().lower()
    known_paths = _known_paths_for_entity(source_descriptor, entity_text)
    hint_terms = tuple(str(item or "").strip().lower() for item in (source_descriptor.navigation_hints.get(task_type, ()) if source_descriptor else ()) if str(item or "").strip())
    candidates = list(page_snapshot.get("links") or []) + [
        {"text": str(item), "url": str(item)} for item in list(page_snapshot.get("nav_items") or []) if str(item).strip().startswith(("http://", "https://"))
    ]

    seen: set[str] = set()
    scored: list[ScoredLink] = []
    for item in candidates:
        url = str(item.get("url") or "").strip()
        text = str(item.get("text") or url).strip()
        if not url or not text:
            continue
        lowered_text = text.lower()
        lowered_url = url.lower()
        if lowered_url in seen:
            continue
        seen.add(lowered_url)
        score = 0
        reasons: list[str] = []
        if entity_text and entity_text in lowered_text:
            score += 3
            reasons.append("entity_in_text")
        if entity_text and entity_text.replace(" ", "-") in lowered_url:
            score += 4
            reasons.append("entity_in_url")
        for term in _TASK_KEYWORDS.get(task_type, ()):
            lowered_term = term.lower()
            if lowered_term in lowered_text or lowered_term in lowered_url:
                score += 5 if task_type == "specs" else 4
                reasons.append(f"task_term:{term}")
        for hint in hint_terms:
            if hint and (hint in lowered_text or hint in lowered_url):
                score += 3
                reasons.append(f"source_hint:{hint}")
        if any(path in lowered_url for path in known_paths):
            score += 6
            reasons.append("known_product_path")
        if any(term in lowered_text or term in lowered_url for term in _IRRELEVANT_TERMS):
            score -= 4
            reasons.append("irrelevant_term")
        if score <= 0:
            continue
        scored.append(ScoredLink(text=text, url=url, score=score, reasons=reasons))

    scored.sort(key=lambda item: (-item.score, len(item.url), item.text.lower()))
    return scored[: max(1, int(max_links or 2))]


def _known_paths_for_entity(source_descriptor: SourceDescriptor | None, entity_text: str) -> tuple[str, ...]:
    if source_descriptor is None or not entity_text:
        return ()
    normalized_entity = entity_text.strip().lower()
    hits: list[str] = []
    for key, paths in source_descriptor.known_product_paths.items():
        if key in normalized_entity or normalized_entity in key:
            hits.extend(path.lower() for path in paths)
    return tuple(dict.fromkeys(hits))
