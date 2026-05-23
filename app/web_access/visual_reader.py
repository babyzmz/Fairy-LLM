from __future__ import annotations

from typing import Any

from .decision_models import VisualReadResult


class VisualReader:
    def read(self, page: dict[str, Any], targets: list[dict[str, Any]]) -> list[VisualReadResult]:
        results: list[VisualReadResult] = []
        screenshot_path = str(page.get("screenshot_path") or "").strip()
        visible_text = " ".join(str(page.get("visible_text") or "").split())
        headings = [str(item).strip() for item in list(page.get("headings") or []) if str(item).strip()]
        links = [item for item in list(page.get("links") or []) if isinstance(item, dict)]
        source_url = str(page.get("final_url") or page.get("url") or "").strip()
        for target in targets:
            region = str(target.get("region") or "page").strip() or "page"
            summary = self._summarize_region(region, visible_text=visible_text, headings=headings, links=links)
            if not summary:
                continue
            results.append(
                VisualReadResult(
                    region=region,
                    summary=summary,
                    confidence=0.82 if region in {"top_banner", "hero_section"} else 0.76,
                    source_url=source_url or str(target.get("page_url") or "").strip(),
                    visual_type=self._visual_type_for_region(region),
                    screenshot_path=screenshot_path,
                )
            )
        return results

    def _summarize_region(
        self,
        region: str,
        *,
        visible_text: str,
        headings: list[str],
        links: list[dict[str, Any]],
    ) -> str:
        if region == "top_banner":
            if headings:
                return f"\u9875\u9762\u9876\u90e8\u516c\u544a\u5199\u7740\uff1a{headings[0]}"
            snippet = self._shorten(visible_text, 96)
            return f"\u9875\u9762\u9876\u90e8\u516c\u544a\u5199\u7740\uff1a{snippet}" if snippet else ""
        if region == "hero_section":
            if headings:
                detail = self._shorten(visible_text, 180)
                return f"\u9996\u5c4f\u4e3b\u533a\u57df\u5f3a\u8c03\uff1a{headings[0]}\u3002{detail}".strip()
            snippet = self._shorten(visible_text, 140)
            return f"\u9996\u5c4f\u4e3b\u533a\u57df\u5f3a\u8c03\uff1a{snippet}" if snippet else ""
        if region == "results_panel":
            headlines = [str(item.get("text") or item.get("title") or "").strip() for item in links[:3]]
            headlines = [item for item in headlines if item]
            if headlines:
                joined = "\uFF1B".join(headlines[:3])
                return f"\u7ed3\u679c\u533a\u57df\u4e3b\u8981\u6761\u76ee\uff1a{joined}"
            return self._shorten(visible_text, 180)
        return self._shorten(visible_text, 180)

    @staticmethod
    def _visual_type_for_region(region: str) -> str:
        if region == "top_banner":
            return "banner"
        if region == "hero_section":
            return "hero"
        if region == "results_panel":
            return "card"
        return "unknown"

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "\u2026"
