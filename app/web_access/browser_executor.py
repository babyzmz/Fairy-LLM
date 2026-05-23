from __future__ import annotations

import re
import time
from urllib.parse import urljoin
from typing import Any

from bs4 import BeautifulSoup

from app.config import llm_config
from utils.browser_automation import browser_automation

from .decision_models import BrowserAvailabilityStatus
from .source_registry import SourceDescriptor


class BrowserExecutor:
    def available(self) -> bool:
        return self.availability_status().available

    def availability_status(self) -> BrowserAvailabilityStatus:
        if not llm_config.browser_automation_enabled:
            return BrowserAvailabilityStatus(
                available=False,
                level="unavailable",
                reason="browser_automation_disabled",
                fallback_mode="rendered_read",
                diagnostics={"failure_cause": "browser_automation_disabled"},
            )
        report = browser_automation.availability_status()
        return BrowserAvailabilityStatus(
            available=report.available,
            level=report.availability_level,
            reason=report.reason,
            fallback_mode=report.fallback_mode or "rendered_read",
            executable_path=report.executable_path,
            browser_type=report.browser_type,
            diagnostics=dict(report.diagnostics),
        )

    def extract_links(self, page_snapshot: dict[str, Any]) -> list[dict[str, str]]:
        links = []
        for item in list(page_snapshot.get("links") or []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            links.append({"text": text or url, "url": url})
        return links

    def extract_headings(self, page_snapshot: dict[str, Any]) -> list[str]:
        return [str(item).strip() for item in list(page_snapshot.get("headings") or []) if str(item).strip()]

    def extract_nav(self, page_snapshot: dict[str, Any]) -> list[str]:
        return [str(item).strip() for item in list(page_snapshot.get("nav_items") or []) if str(item).strip()]

    @staticmethod
    def extract_headings_from_html(html: str) -> list[str]:
        raw_html = str(html or "")
        if not raw_html:
            return []
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
        except Exception:
            return []
        headings: list[str] = []
        for node in soup.select("h1, h2, h3"):
            text = " ".join((node.get_text(" ", strip=True) or "").split()).strip()
            if not text or len(text) > 160:
                continue
            headings.append(text)
            if len(headings) >= 12:
                break
        return BrowserExecutor._dedupe_casefold(headings)

    @staticmethod
    def extract_nav_from_html(html: str) -> list[str]:
        return BrowserExecutor._extract_nav_items(html)

    @staticmethod
    def extract_links_from_html(base_url: str, html: str) -> list[dict[str, str]]:
        raw_html = str(html or "")
        if not raw_html:
            return []
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
        except Exception:
            return []
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for node in soup.select("a[href]"):
            href = str(node.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(str(base_url or "").strip(), href)
            text = " ".join((node.get_text(" ", strip=True) or "").split()).strip()
            if not text:
                text = url
            if len(text) > 180:
                continue
            lowered = url.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            links.append({"text": text, "url": url})
            if len(links) >= 40:
                break
        return links

    def classify_page_type(
        self,
        page_snapshot: dict[str, Any],
        *,
        source_descriptor: SourceDescriptor | None = None,
        task_type: str = "",
        entity: str = "",
    ) -> str:
        return self._classify_page_type(
            page_snapshot,
            source_descriptor=source_descriptor,
            task_type=task_type,
            entity=entity,
        )

    def rendered_read(self, url: str) -> dict[str, Any]:
        status = self.availability_status()
        if not status.available:
            return {"ok": False, "error": status.reason or "browser_automation_unavailable", "page": {}, "availability": status.to_dict()}
        try:
            page = browser_automation.open_page(
                url,
                timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                capture_screenshot=True,
            )
        except Exception as exc:  # noqa: BLE001
            partial = BrowserAvailabilityStatus(
                available=True,
                level="partial",
                reason="browser_render_failed",
                fallback_mode="http_fetch",
                executable_path=status.executable_path,
                browser_type=status.browser_type,
                diagnostics={**dict(status.diagnostics), "last_launch_error": str(exc), "failure_cause": "page_operation_failed"},
            )
            return {"ok": False, "error": str(exc), "page": {}, "availability": partial.to_dict()}
        return {"ok": True, "error": "", "page": self._page_to_dict(page), "availability": status.to_dict()}

    def execute(
        self,
        *,
        target_url: str,
        actions: list[dict[str, Any]],
        existing_handle_id: str = "",
    ) -> dict[str, Any]:
        status = self.availability_status()
        if not status.available:
            return {
                "ok": False,
                "error": status.reason or "browser_automation_unavailable",
                "page": {},
                "action_log": [],
                "availability": status.to_dict(),
            }

        handle_id = str(existing_handle_id or "").strip()
        page_payload: dict[str, Any] = {}
        action_log: list[dict[str, Any]] = []
        if not actions and target_url:
            actions = [{"type": "open_url", "value": target_url}, {"type": "extract_page"}]

        for action in actions:
            action_type = str(action.get("type") or "").strip().lower()
            try:
                if action_type == "open_url":
                    target = str(action.get("value") or "").strip() or target_url
                    page = browser_automation.open_interactive_page(
                        target,
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                        capture_screenshot=True,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                    handle_id = str(page.handle_id or "").strip()
                elif action_type == "click_text":
                    page = browser_automation.interact(
                        handle_id,
                        action="click",
                        selector=f"text={str(action.get('value') or '').strip()}",
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "click_selector":
                    page = browser_automation.interact(
                        handle_id,
                        action="click",
                        selector=str(action.get("selector") or "").strip(),
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "scroll":
                    page = browser_automation.interact(
                        handle_id,
                        action="scroll",
                        delta_y=int(action.get("value") or 960),
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "type":
                    page = browser_automation.interact(
                        handle_id,
                        action="fill",
                        selector=str(action.get("selector") or "").strip(),
                        text=str(action.get("value") or "").strip(),
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "submit":
                    page = browser_automation.interact(
                        handle_id,
                        action="press",
                        selector=str(action.get("selector") or "").strip(),
                        key=str(action.get("value") or "Enter").strip(),
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "wait":
                    time.sleep(max(0.0, float(action.get("timeout_ms") or 0) / 1000.0))
                    page = browser_automation.extract_session(handle_id, capture_screenshot=False)
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "goto":
                    target = str(action.get("value") or "").strip()
                    page = browser_automation.interact(
                        handle_id,
                        action="goto",
                        text=target,
                        timeout_ms=int(action.get("timeout_ms") or llm_config.browser_navigation_timeout_sec * 1000),
                        wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    )
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "extract_page":
                    page = browser_automation.extract_session(handle_id, capture_screenshot=False)
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                elif action_type == "extract_section":
                    page = browser_automation.extract_session(handle_id, capture_screenshot=True)
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                    section_name = str(action.get("value") or action.get("selector") or "").strip()
                    if section_name:
                        page_payload.setdefault("key_values", []).append(f"section_hint={section_name}")
                elif action_type == "capture_screenshot":
                    page = browser_automation.extract_session(handle_id, capture_screenshot=True)
                    page_payload = self._merge_page_payload(page_payload, self._page_to_dict(page))
                else:
                    partial = BrowserAvailabilityStatus(
                        available=True,
                        level="partial",
                        reason="unsupported_browser_action",
                        fallback_mode="rendered_read",
                        executable_path=status.executable_path,
                        browser_type=status.browser_type,
                        diagnostics={**dict(status.diagnostics), "failure_cause": "page_operation_failed"},
                    )
                    return {
                        "ok": False,
                        "error": f"unsupported_browser_action:{action_type}",
                        "page": page_payload,
                        "action_log": action_log,
                        "availability": partial.to_dict(),
                    }
                handle_id = str(page_payload.get("handle_id") or handle_id).strip()
                action_log.append({"action": action_type, "ok": True})
            except Exception as exc:  # noqa: BLE001
                action_log.append({"action": action_type, "ok": False, "error": str(exc)})
                partial = BrowserAvailabilityStatus(
                    available=True,
                    level="partial",
                    reason="browser_interaction_failed",
                    fallback_mode="rendered_read",
                    executable_path=status.executable_path,
                    browser_type=status.browser_type,
                    diagnostics={**dict(status.diagnostics), "last_launch_error": str(exc), "failure_cause": "page_operation_failed"},
                )
                return {
                    "ok": False,
                    "error": str(exc),
                    "page": page_payload,
                    "action_log": action_log,
                    "availability": partial.to_dict(),
                }

        return {"ok": True, "error": "", "page": page_payload, "action_log": action_log, "availability": status.to_dict()}

    @staticmethod
    def _page_to_dict(page: Any) -> dict[str, Any]:
        payload = {
            "requested_url": str(getattr(page, "requested_url", "") or "").strip(),
            "final_url": str(getattr(page, "final_url", "") or "").strip(),
            "url": str(getattr(page, "final_url", "") or "").strip(),
            "title": str(getattr(page, "title", "") or "").strip(),
            "html": str(getattr(page, "html", "") or ""),
            "meta_description": str(getattr(page, "meta_description", "") or "").strip(),
            "visible_text": str(getattr(page, "visible_text", "") or "").strip(),
            "headings": list(getattr(page, "headings", []) or []),
            "links": list(getattr(page, "links", []) or []),
            "key_values": list(getattr(page, "key_values", []) or []),
            "table_rows": list(getattr(page, "table_rows", []) or []),
            "screenshot_path": str(getattr(page, "screenshot_path", "") or "").strip(),
            "handle_id": str(getattr(page, "handle_id", "") or "").strip(),
            "blocked_reason": str(getattr(page, "blocked_reason", "") or "").strip(),
        }
        nav_items = BrowserExecutor._extract_nav_items(payload.get("html", ""))
        if nav_items:
            payload["nav_items"] = nav_items
        payload["page_type"] = BrowserExecutor._classify_page_type(payload)
        return payload

    @staticmethod
    def _merge_page_payload(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        merged = dict(previous or {})
        for key, value in current.items():
            if value in ("", None, [], {}):
                continue
            merged[key] = value
        if previous.get("screenshot_path") and not merged.get("screenshot_path"):
            merged["screenshot_path"] = previous["screenshot_path"]
        if previous.get("handle_id") and not merged.get("handle_id"):
            merged["handle_id"] = previous["handle_id"]
        return merged

    @staticmethod
    def _extract_nav_items(html: str) -> list[str]:
        raw_html = str(html or "")
        if not raw_html:
            return []
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
        except Exception:
            return []
        nav_items: list[str] = []
        for container in soup.select("nav a, header a"):
            text = " ".join((container.get_text(" ", strip=True) or "").split()).strip()
            if not text or len(text) > 60:
                continue
            nav_items.append(text)
            if len(nav_items) >= 20:
                break
        return BrowserExecutor._dedupe_casefold(nav_items)

    @staticmethod
    def _dedupe_casefold(items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            lowered = str(item).lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(item)
        return deduped

    @staticmethod
    def _classify_page_type(
        page_snapshot: dict[str, Any],
        *,
        source_descriptor: SourceDescriptor | None = None,
        task_type: str = "",
        entity: str = "",
    ) -> str:
        url = str(page_snapshot.get("final_url") or page_snapshot.get("url") or "").strip().lower()
        title = str(page_snapshot.get("title") or "").strip().lower()
        visible_text = str(page_snapshot.get("visible_text") or "").strip().lower()
        headings = [str(item).strip().lower() for item in list(page_snapshot.get("headings") or []) if str(item).strip()]
        nav_items = [str(item).strip().lower() for item in list(page_snapshot.get("nav_items") or []) if str(item).strip()]
        heading_text = " ".join(headings)
        combined = " ".join([title, visible_text[:4000], heading_text, " ".join(nav_items)])
        entity_text = str(entity or "").strip().lower()
        looks_like_home = (not url or url.endswith("/")) and len(nav_items) >= 3 and len(headings) <= 3

        if "/specs/" in url or any(term in f"{title} {heading_text}" for term in ("tech specs", "specifications", "参数", "规格")):
            return "docs"
        if source_descriptor is not None:
            markers = source_descriptor.page_markers
            if any(marker.lower() in url or marker.lower() in combined for marker in markers.get("specs", ())):
                if not looks_like_home or any(term in url for term in ("/specs/", "/specifications/")):
                    return "docs"
            if any(marker.lower() in url or marker.lower() in combined for marker in markers.get("news_index", ())):
                return "news"
            if any(marker.lower() in url or marker.lower() in combined for marker in markers.get("press_release", ())):
                if any(token in combined for token in ("announce", "发布", "新闻稿", "press release", "newsroom")):
                    return "news"
            if any(marker.lower() in url for marker in markers.get("product", ())):
                return "product"

        if looks_like_home:
            return "homepage"
        if "newsroom" in url and any(token in combined for token in ("newsroom", "source", "stories", "latest")):
            return "news"
        if any(token in combined for token in ("press release", "新闻稿", "announces", "发布")) and re.search(r"\d{4}", combined):
            return "news"
        if entity_text and (entity_text in url or entity_text in combined):
            if task_type == "specs" and any(term in combined for term in ("tech specs", "specifications", "鍙傛暟", "瑙勬牸")):
                return "docs"
            if task_type == "release" and re.search(r"\d{4}", combined):
                return "news"
            return "product"
        if any(token in combined for token in ("article", "story")) and re.search(r"\d{4}", combined):
            return "generic"
        if any(token in combined for token in ("docs", "documentation", "guide", "help", "faq", "overview", "pricing")):
            return "docs"
        return "generic"
