from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.config import llm_config
from app.mcp_client_layer import MCPClientLayer
from app.tool_registry import ToolRegistry
from skills.crawl_webpage import crawl_webpage
from skills.search_web import search_web_detailed, search_web_queries
from utils.browser_automation import browser_automation
from utils.web_content_extractor import extract_web_content


PRICE_RE = re.compile(r"(?:[$€¥£]|USD|AUD|CNY|RMB)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)
DISPLAY_RE = re.compile(r"\b\d{2}(?:\.\d)?\s?(?:inch|inches|in|\")\b", re.IGNORECASE)
HZ_RE = re.compile(r"\b\d{2,3}\s?hz\b", re.IGNORECASE)
RES_RE = re.compile(r"\b(?:1920x1080|2560x1440|3440x1440|3840x2160|4k|qhd|fhd|uhd)\b", re.IGNORECASE)

FOLLOWER_RE = re.compile(r"(\d+(?:\.\d+)?(?:万|亿)?)\s*粉丝")
RATING_RE = re.compile(r"(?:评分|rating)\s*[:：]?\s*(\d(?:\.\d)?)", re.IGNORECASE)
DATE_RE = re.compile(r"\b(?:20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?)\b")


class BrowserCapability:
    def __init__(self, registry: ToolRegistry, mcp: MCPClientLayer) -> None:
        self.registry = registry
        self.mcp = mcp
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register("search_web", "Search the web and return title/url/snippet results.", self._tool_search_web)
        self.registry.register("open_url", "Open a URL through browser automation when available.", self._tool_open_url)
        self.registry.register(
            "extract_page_text",
            "Extract readable text and structured hints from a fetched page.",
            self._tool_extract_page_text,
        )
        self.registry.register("snapshot_page", "Build a structured snapshot of a page.", self._tool_snapshot_page)
        self.registry.register("browser_interact", "Interact with an existing browser page handle.", self._tool_browser_interact)
        self.registry.register(
            "compare_structured_results",
            "Compare multiple extracted page items and produce differences.",
            self._tool_compare_structured_results,
        )
        self.registry.register(
            "extract_structured_fields",
            "Extract structured fields from a page using a requested schema.",
            self._tool_extract_structured_fields,
        )

    def search(self, query: str, *, allowed_tools: Iterable[str], preferred_domains: Iterable[str] | None = None) -> dict[str, Any]:
        return self.mcp.call_tool(
            "search_web",
            allowed_tools=allowed_tools,
            query=query,
            preferred_domains=list(preferred_domains or []),
        ).data

    def open(self, url: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("open_url", allowed_tools=allowed_tools, url=url).data

    def extract(self, source: dict[str, Any] | str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("extract_page_text", allowed_tools=allowed_tools, source=source).data

    def snapshot(self, source: dict[str, Any] | str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("snapshot_page", allowed_tools=allowed_tools, source=source).data

    def interact(
        self,
        handle_id: str,
        *,
        allowed_tools: Iterable[str],
        action: str,
        selector: str = "",
        text: str = "",
        key: str = "Enter",
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "browser_interact",
            allowed_tools=allowed_tools,
            handle_id=handle_id,
            action=action,
            selector=selector,
            text=text,
            key=key,
        ).data

    def compare(
        self,
        items: list[dict[str, Any]],
        *,
        allowed_tools: Iterable[str],
        focus: str = "",
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "compare_structured_results",
            allowed_tools=allowed_tools,
            items=items,
            focus=focus,
        ).data

    def extract_structured_fields(
        self,
        source: dict[str, Any] | str,
        *,
        allowed_tools: Iterable[str],
        schema: list[str],
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "extract_structured_fields",
            allowed_tools=allowed_tools,
            source=source,
            schema=schema,
        ).data

    def _tool_search_web(self, query: str, preferred_domains: list[str] | None = None) -> dict[str, Any]:
        preferred = preferred_domains or []
        detailed = search_web_detailed(
            query,
            max_results=5,
            timeout_sec=llm_config.web_request_timeout_sec,
            preferred_domains=preferred,
        )
        if detailed.get("results"):
            return detailed

        multi = search_web_queries(
            [query],
            max_results_per_query=5,
            timeout_sec=llm_config.web_request_timeout_sec,
            preferred_domains=preferred,
        )
        if multi:
            detailed["results"] = multi
        return detailed

    def _tool_open_url(self, url: str) -> dict[str, Any]:
        if llm_config.browser_automation_enabled and browser_automation.available():
            try:
                page = browser_automation.open_interactive_page(
                    url,
                    timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                    wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    capture_screenshot=True,
                )
                return self._page_result_to_dict(page, source="browser")
            except Exception:
                pass

        page = crawl_webpage(url)
        return {
            "url": page.url,
            "final_url": page.final_url,
            "title": page.title,
            "status_code": page.status_code,
            "html": page.html,
            "meta_description": "",
            "visible_text": "",
            "headings": [],
            "links": [],
            "key_values": [],
            "table_rows": [],
            "screenshot_path": "",
            "handle_id": "",
            "source": "http",
            "blocked_reason": getattr(page, "blocked_reason", ""),
        }

    def _tool_extract_page_text(self, source: dict[str, Any] | str) -> dict[str, Any]:
        page = self._normalize_page_source(source)
        handle_id = str(page.get("handle_id", "")).strip()
        if handle_id and llm_config.browser_automation_enabled and browser_automation.available():
            try:
                fresh = browser_automation.extract_session(handle_id, capture_screenshot=False)
                page = self._page_result_to_dict(fresh, source="browser")
            except Exception:
                pass

        page_html = str(page.get("html", ""))
        title = str(page.get("title", "")).strip()
        url = str(page.get("final_url") or page.get("url", "")).strip()
        meta_description = str(page.get("meta_description", "")).strip()
        visible_text = str(page.get("visible_text", "")).strip()

        extracted = extract_web_content(page_html) if page_html.strip() else None
        readable_text = visible_text or (extracted.body_text if extracted is not None else "")
        if not title and extracted is not None:
            title = extracted.title
        if not meta_description and extracted is not None:
            meta_description = extracted.meta_description

        headings = [str(item).strip() for item in page.get("headings", []) if str(item).strip()]
        links = [item for item in page.get("links", []) if isinstance(item, dict) and str(item.get("url", "")).strip()]
        key_values = [str(item).strip() for item in page.get("key_values", []) if str(item).strip()]
        table_rows = [str(item).strip() for item in page.get("table_rows", []) if str(item).strip()]

        structured = self._extract_structured_signals(
            page_html,
            readable_text,
            url=url,
            page_title=title,
            headings=headings,
            key_values=key_values,
            table_rows=table_rows,
        )
        if key_values:
            structured["key_values"] = key_values[:16]
        if table_rows:
            structured["table_rows"] = table_rows[:16]

        return {
            "title": title,
            "url": url,
            "meta_description": meta_description,
            "body_text": readable_text[:8000],
            "headings": headings[:12],
            "links": links[:12],
            "key_values": key_values[:16],
            "table_rows": table_rows[:16],
            "screenshot_path": str(page.get("screenshot_path", "")),
            "handle_id": handle_id,
            "structured_signals": structured,
            "blocked_reason": str(page.get("blocked_reason", "")),
        }

    def _tool_extract_structured_fields(self, source: dict[str, Any] | str, schema: list[str]) -> dict[str, Any]:
        extracted = self._tool_extract_page_text(source)
        signals = dict(extracted.get("structured_signals", {}))
        selected: dict[str, Any] = {}
        for key in schema:
            if key in signals:
                selected[key] = signals[key]
        return {
            "title": extracted.get("title", ""),
            "url": extracted.get("url", ""),
            "fields": selected,
            "blocked_reason": extracted.get("blocked_reason", ""),
        }

    def _tool_snapshot_page(self, source: dict[str, Any] | str) -> dict[str, Any]:
        page = self._normalize_page_source(source)
        handle_id = str(page.get("handle_id", "")).strip()
        if handle_id and llm_config.browser_automation_enabled and browser_automation.available():
            try:
                fresh = browser_automation.extract_session(handle_id, capture_screenshot=True)
                page = self._page_result_to_dict(fresh, source="browser")
            except Exception:
                pass
        extracted = self._tool_extract_page_text(page)
        top_links = extracted.get("links", [])[:8]
        return {
            "url": extracted.get("url", ""),
            "title": extracted.get("title", ""),
            "headings": extracted.get("headings", [])[:8],
            "table_rows": extracted.get("table_rows", [])[:10],
            "key_values": extracted.get("key_values", [])[:10],
            "top_links": top_links,
            "screenshot_path": page.get("screenshot_path", "") or extracted.get("screenshot_path", ""),
            "handle_id": handle_id,
            "content_preview": extracted.get("body_text", "")[:1200],
        }

    def _tool_browser_interact(
        self,
        handle_id: str,
        action: str,
        selector: str = "",
        text: str = "",
        key: str = "Enter",
    ) -> dict[str, Any]:
        if not handle_id:
            raise ValueError("browser_interact requires handle_id")
        page = browser_automation.interact(
            handle_id,
            action=action,
            selector=selector,
            text=text,
            key=key,
            timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
            wait_after_load_ms=llm_config.browser_post_load_wait_ms,
        )
        return self._page_result_to_dict(page, source="browser")

    def _tool_compare_structured_results(self, items: list[dict[str, Any]], focus: str = "") -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        spec_counts: dict[str, int] = {}

        for item in items:
            title = str(item.get("title", "")).strip() or str(item.get("url", ""))
            url = str(item.get("url", "")).strip()
            body_text = str(item.get("body_text", "")).strip()
            signals = item.get("structured_signals", {}) if isinstance(item.get("structured_signals"), dict) else {}
            prices = [str(v).strip() for v in signals.get("prices", []) if str(v).strip()][:5]
            specs = [str(v).strip() for v in signals.get("specs", []) if str(v).strip()][:10]
            key_values = [str(v).strip() for v in signals.get("key_values", []) if str(v).strip()][:10]
            for spec in specs:
                spec_counts[spec] = spec_counts.get(spec, 0) + 1
            normalized.append(
                {
                    "title": title,
                    "url": url,
                    "prices": prices,
                    "specs": specs,
                    "key_values": key_values,
                    "summary": body_text[:600],
                }
            )

        shared_points = sorted([spec for spec, count in spec_counts.items() if count > 1])[:12]
        differences: list[str] = []
        for item in normalized:
            unique_specs = [spec for spec in item["specs"] if spec not in shared_points][:5]
            if unique_specs:
                differences.append(f"{item['title']}: {', '.join(unique_specs)}")
                continue
            if item["prices"]:
                differences.append(f"{item['title']}: 价格线索 {', '.join(item['prices'])}")
                continue
            if item["key_values"]:
                differences.append(f"{item['title']}: {item['key_values'][0]}")

        if focus:
            recommendation = f"已围绕“{focus}”整理网页差异。建议优先核对价格、规格和发布日期。"
        elif differences:
            recommendation = "建议优先比较网页中的规格、价格和关键参数，再决定结论。"
        else:
            recommendation = "网页内容已收集，但结构化差异较少，建议继续打开更多来源交叉确认。"

        return {
            "items": normalized,
            "shared_points": shared_points,
            "differences": differences[:12],
            "recommendation": recommendation,
        }

    def _normalize_page_source(self, source: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(source, str):
            return self._tool_open_url(source)
        return dict(source)

    def _page_result_to_dict(self, page, *, source: str) -> dict[str, Any]:
        return {
            "url": page.requested_url,
            "final_url": page.final_url,
            "title": page.title,
            "status_code": page.status_code,
            "html": page.html,
            "meta_description": page.meta_description,
            "visible_text": page.visible_text,
            "headings": page.headings,
            "links": page.links,
            "key_values": page.key_values,
            "table_rows": page.table_rows,
            "screenshot_path": page.screenshot_path,
            "handle_id": page.handle_id,
            "source": source,
            "blocked_reason": getattr(page, "blocked_reason", ""),
        }

    def _extract_structured_signals(
        self,
        html: str,
        body_text: str,
        *,
        url: str,
        page_title: str,
        headings: list[str],
        key_values: list[str],
        table_rows: list[str],
    ) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser") if html.strip() else BeautifulSoup("", "html.parser")
        text = body_text[:12000]
        prices = sorted(set(match.group(0) for match in PRICE_RE.finditer(text)))[:8]
        specs = sorted(
            {
                *(match.group(0) for match in DISPLAY_RE.finditer(text)),
                *(match.group(0) for match in HZ_RE.finditer(text)),
                *(match.group(0) for match in RES_RE.finditer(text)),
                *headings[:8],
            }
        )[:16]

        if not key_values:
            for row in soup.select("table tr")[:20]:
                cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
                if 2 <= len(cells) <= 4:
                    key_values.append(" | ".join(cells))
        if not table_rows:
            table_rows.extend(key_values[:10])

        title_entity = page_title.strip() or (headings[0] if headings else "")
        follower_match = FOLLOWER_RE.search(text)
        rating_match = RATING_RE.search(text)
        dates = sorted(set(match.group(0) for match in DATE_RE.finditer(text)))[:8]
        primary_price = prices[0] if prices else ""
        return {
            "prices": prices,
            "price": primary_price,
            "specs": specs,
            "key_values": key_values[:16],
            "table_rows": table_rows[:16],
            "domain": urlparse(url).netloc.lower(),
            "entity_name": title_entity,
            "follower_count": follower_match.group(1) if follower_match else "",
            "rating": rating_match.group(1) if rating_match else "",
            "date": dates[0] if dates else "",
            "dates": dates,
        }
