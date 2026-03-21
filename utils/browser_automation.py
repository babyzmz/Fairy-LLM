from __future__ import annotations

import atexit
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

VERIFICATION_MARKERS = (
    "captcha",
    "human verification",
    "verify you are human",
    "security check",
    "robot",
    "unusual traffic",
    "验证",
    "安全验证",
    "请完成验证",
    "人机验证",
)

from bs4 import BeautifulSoup


SCREENSHOT_DIR = Path("data/screenshots")
SEARCH_ENGINE_HOSTS = ("bing.com", "duckduckgo.com", "google.com")
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "form",
    "from",
    "source",
    "ref",
}


def _is_search_engine_host(host: str) -> bool:
    lowered = host.lower()
    if "searx" in lowered:
        return True
    return any(lowered == domain or lowered.endswith(f".{domain}") for domain in SEARCH_ENGINE_HOSTS)


def _strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=False)
    clean_items: list[tuple[str, str]] = []
    for key, values in query.items():
        if key.lower() in TRACKING_QUERY_KEYS:
            continue
        for value in values:
            clean_items.append((key, value))
    from urllib.parse import urlencode, urlunparse
    clean_query = urlencode(clean_items, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, ""))


def _normalize_search_result_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    if "duckduckgo.com" in parsed.netloc:
        uddg = query.get("uddg")
        if uddg:
            raw_url = unquote(uddg[0])
            parsed = urlparse(raw_url)
            query = parse_qs(parsed.query)
    if "bing.com" in parsed.netloc:
        for key in ("u", "url", "target", "r"):
            value = query.get(key)
            if value:
                candidate = unquote(value[0])
                if candidate.startswith(("http://", "https://")):
                    raw_url = candidate
                    parsed = urlparse(raw_url)
                    break
    raw_url = _strip_tracking_params(raw_url)
    parsed = urlparse(raw_url)
    if _is_search_engine_host(parsed.netloc):
        return ""
    return raw_url


@dataclass(slots=True)
class BrowserFetchResult:
    html: str
    final_url: str
    title: str


@dataclass(slots=True)
class BrowserSearchHit:
    title: str
    url: str
    snippet: str
    provider: str = "browser"


@dataclass(slots=True)
class BrowserPageResult:
    requested_url: str
    final_url: str
    title: str
    html: str
    meta_description: str
    visible_text: str
    headings: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    key_values: list[str] = field(default_factory=list)
    table_rows: list[str] = field(default_factory=list)
    screenshot_path: str = ""
    handle_id: str = ""
    status_code: int | None = None
    blocked_reason: str = ""


class BrowserAutomation:
    """Lazy Playwright wrapper with transient and interactive browser sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._disabled = False
        self._sessions: dict[str, tuple[Any, Any]] = {}
        atexit.register(self.close)

    def available(self) -> bool:
        if self._disabled:
            return False
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except Exception:
            return False
        return True

    def fetch_html(
        self,
        url: str,
        *,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 1200,
    ) -> BrowserFetchResult:
        page_data = self.open_page(
            url,
            timeout_ms=timeout_ms,
            wait_after_load_ms=wait_after_load_ms,
            capture_screenshot=False,
        )
        return BrowserFetchResult(
            html=page_data.html,
            final_url=page_data.final_url,
            title=page_data.title,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 1200,
        preferred_domains: Iterable[str] | None = None,
    ) -> list[BrowserSearchHit]:
        search_urls = [
            ("duckduckgo-browser", f"https://duckduckgo.com/html/?q={quote_plus(query)}"),
            ("duckduckgo-browser", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
            ("bing-browser", f"https://www.bing.com/search?q={quote_plus(query)}"),
        ]
        preferred = tuple(domain.lower() for domain in (preferred_domains or []))

        for provider, search_url in search_urls:
            try:
                html = self.fetch_html(
                    search_url,
                    timeout_ms=timeout_ms,
                    wait_after_load_ms=wait_after_load_ms,
                ).html
            except Exception:
                continue
            if "bing" in provider:
                results = self._parse_bing_results(html, max_results=max_results)
            else:
                results = self._parse_duckduckgo_results(html, max_results=max_results)
            if not results:
                continue
            if preferred:
                results.sort(key=lambda item: self._preferred_sort_key(item.url, preferred))
            return results[:max_results]
        return []

    def open_page(
        self,
        url: str,
        *,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 1200,
        capture_screenshot: bool = True,
    ) -> BrowserPageResult:
        context, page = self._create_context_page()
        try:
            self._navigate(page, url, timeout_ms=timeout_ms, wait_after_load_ms=wait_after_load_ms)
            return self._build_page_result(
                page,
                requested_url=url,
                capture_screenshot=capture_screenshot,
            )
        finally:
            self._close_page_context(context, page)

    def open_interactive_page(
        self,
        url: str,
        *,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 1200,
        capture_screenshot: bool = True,
    ) -> BrowserPageResult:
        context, page = self._create_context_page()
        self._navigate(page, url, timeout_ms=timeout_ms, wait_after_load_ms=wait_after_load_ms)
        handle_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._sessions[handle_id] = (context, page)
            self._prune_sessions()
        return self._build_page_result(
            page,
            requested_url=url,
            capture_screenshot=capture_screenshot,
            handle_id=handle_id,
        )

    def extract_session(
        self,
        handle_id: str,
        *,
        capture_screenshot: bool = False,
    ) -> BrowserPageResult:
        context, page = self._get_session(handle_id)
        _ = context
        return self._build_page_result(
            page,
            requested_url=page.url,
            capture_screenshot=capture_screenshot,
            handle_id=handle_id,
        )

    def interact(
        self,
        handle_id: str,
        *,
        action: str,
        selector: str = "",
        text: str = "",
        key: str = "Enter",
        delta_y: int = 960,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 900,
    ) -> BrowserPageResult:
        _, page = self._get_session(handle_id)
        if action == "click":
            if not selector:
                raise ValueError("click action requires selector")
            page.locator(selector).first.click(timeout=timeout_ms)
        elif action == "fill":
            if not selector:
                raise ValueError("fill action requires selector")
            page.locator(selector).first.fill(text, timeout=timeout_ms)
        elif action == "press":
            if selector:
                page.locator(selector).first.press(key, timeout=timeout_ms)
            else:
                page.keyboard.press(key)
        elif action == "scroll":
            page.mouse.wheel(0, delta_y)
        elif action == "goto":
            if not text:
                raise ValueError("goto action requires text=url")
            self._navigate(page, text, timeout_ms=timeout_ms, wait_after_load_ms=wait_after_load_ms)
        else:
            raise ValueError(f"Unsupported browser action: {action}")

        if wait_after_load_ms > 0:
            page.wait_for_timeout(wait_after_load_ms)
        return self._build_page_result(
            page,
            requested_url=page.url,
            capture_screenshot=True,
            handle_id=handle_id,
        )

    def close_session(self, handle_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(handle_id, None)
        if session is None:
            return
        context, page = session
        self._close_page_context(context, page)

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            browser = self._browser
            playwright = self._playwright
            self._browser = None
            self._playwright = None
        for context, page in sessions:
            self._close_page_context(context, page)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _ensure_browser(self):
        with self._lock:
            if self._disabled:
                raise RuntimeError("Browser automation disabled after previous launch failure")
            if self._browser is not None:
                return self._browser
            from playwright.sync_api import sync_playwright

            try:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)
                return self._browser
            except Exception:
                self._disabled = True
                playwright = self._playwright
                self._playwright = None
                if playwright is not None:
                    try:
                        playwright.stop()
                    except Exception:
                        pass
                raise

    def _create_context_page(self):
        browser = self._ensure_browser()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        return context, page

    def _navigate(self, page, url: str, *, timeout_ms: int, wait_after_load_ms: int) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
        except Exception:
            pass
        if wait_after_load_ms > 0:
            page.wait_for_timeout(wait_after_load_ms)

    def _build_page_result(
        self,
        page,
        *,
        requested_url: str,
        capture_screenshot: bool,
        handle_id: str = "",
    ) -> BrowserPageResult:
        extracted = self._extract_page_payload(page)
        screenshot_path = self._save_screenshot(page) if capture_screenshot else ""
        blocked_reason = self._detect_blocked_reason(page.url, page.title(), extracted["visible_text"])
        return BrowserPageResult(
            requested_url=requested_url,
            final_url=page.url,
            title=page.title(),
            html=page.content(),
            meta_description=extracted["meta_description"],
            visible_text=extracted["visible_text"],
            headings=extracted["headings"],
            links=extracted["links"],
            key_values=extracted["key_values"],
            table_rows=extracted["table_rows"],
            screenshot_path=screenshot_path,
            handle_id=handle_id,
            status_code=200,
            blocked_reason=blocked_reason,
        )

    def _detect_blocked_reason(self, final_url: str, title: str, visible_text: str) -> str:
        haystack = "\n".join((final_url, title, visible_text[:4000])).lower()
        for marker in VERIFICATION_MARKERS:
            if marker in haystack:
                return marker
        return ""

    def _save_screenshot(self, page) -> str:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"browser_{int(time.time() * 1000)}.png"
        target = SCREENSHOT_DIR / filename
        page.screenshot(path=str(target), full_page=True)
        return str(target)

    def _extract_page_payload(self, page) -> dict[str, Any]:
        payload = page.evaluate(
            """
            () => {
              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const visibleNodes = Array.from(document.querySelectorAll(
                'main, article, section, p, li, h1, h2, h3, h4, td, th, blockquote, pre, button'
              ));
              const visibleText = [];
              let visibleLength = 0;
              for (const node of visibleNodes) {
                const text = normalize(node.innerText || node.textContent || '');
                if (!text || text.length < 2) continue;
                visibleText.push(text);
                visibleLength += text.length;
                if (visibleLength > 20000) break;
              }

              const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                .map((node) => normalize(node.innerText || node.textContent || ''))
                .filter(Boolean)
                .slice(0, 12);

              const links = Array.from(document.querySelectorAll('a[href]'))
                .map((node) => ({
                  text: normalize(node.innerText || node.textContent || ''),
                  url: node.href || '',
                }))
                .filter((item) => item.url)
                .slice(0, 20);

              const tableRows = [];
              for (const row of Array.from(document.querySelectorAll('table tr')).slice(0, 24)) {
                const cells = Array.from(row.querySelectorAll('th, td'))
                  .map((node) => normalize(node.innerText || node.textContent || ''))
                  .filter(Boolean);
                if (cells.length >= 2) {
                  tableRows.push(cells.join(' | '));
                }
              }

              const keyValues = [];
              for (const item of Array.from(document.querySelectorAll('dt, dd, li, p')).slice(0, 80)) {
                const text = normalize(item.innerText || item.textContent || '');
                if (!text) continue;
                if ((text.includes(':') || text.includes('：')) && text.length <= 180) {
                  keyValues.push(text);
                }
              }

              const metaDescription = normalize(
                document.querySelector('meta[name="description"]')?.content ||
                document.querySelector('meta[property="og:description"]')?.content ||
                ''
              );

              return {
                meta_description: metaDescription,
                visible_text: visibleText.join('\\n').slice(0, 20000),
                headings,
                links,
                key_values: keyValues.slice(0, 24),
                table_rows: tableRows.slice(0, 24),
              };
            }
            """
        )
        return {
            "meta_description": str(payload.get("meta_description", "")).strip(),
            "visible_text": str(payload.get("visible_text", "")).strip(),
            "headings": [str(item).strip() for item in payload.get("headings", []) if str(item).strip()],
            "links": [
                {"text": str(item.get("text", "")).strip(), "url": str(item.get("url", "")).strip()}
                for item in payload.get("links", [])
                if str(item.get("url", "")).strip()
            ],
            "key_values": [str(item).strip() for item in payload.get("key_values", []) if str(item).strip()],
            "table_rows": [str(item).strip() for item in payload.get("table_rows", []) if str(item).strip()],
        }

    def _get_session(self, handle_id: str):
        with self._lock:
            session = self._sessions.get(handle_id)
        if session is None:
            raise KeyError(f"Browser handle not found: {handle_id}")
        return session

    def _prune_sessions(self, max_keep: int = 6) -> None:
        if len(self._sessions) <= max_keep:
            return
        old_handle = next(iter(self._sessions))
        session = self._sessions.pop(old_handle)
        context, page = session
        self._close_page_context(context, page)

    def _close_page_context(self, context, page) -> None:
        try:
            page.close()
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass

    def _preferred_sort_key(self, url: str, preferred_domains: tuple[str, ...]) -> tuple[int, str]:
        domain = urlparse(url).netloc.lower()
        matched = 0 if any(hint in domain for hint in preferred_domains) else 1
        return (matched, domain)

    def _parse_duckduckgo_results(self, html: str, *, max_results: int) -> list[BrowserSearchHit]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[BrowserSearchHit] = []
        seen_urls: set[str] = set()
        for node in soup.select(".result"):
            anchor = node.select_one(".result__a") or node.select_one("a.result-link")
            if anchor is None:
                continue
            title = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "").strip()
            if not title or not href:
                continue
            url = _normalize_search_result_url(href)
            if not url.startswith(("http://", "https://")) or url in seen_urls:
                continue
            snippet_node = (
                node.select_one(".result__snippet")
                or node.select_one(".result-snippet")
                or node.select_one(".result__body")
            )
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
            seen_urls.add(url)
            results.append(BrowserSearchHit(title=title, url=url, snippet=snippet, provider="duckduckgo-browser"))
            if len(results) >= max_results:
                break
        return results

    def _parse_bing_results(self, html: str, *, max_results: int) -> list[BrowserSearchHit]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[BrowserSearchHit] = []
        seen_urls: set[str] = set()
        for node in soup.select("li.b_algo"):
            anchor = node.select_one("h2 a")
            if anchor is None:
                continue
            title = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "").strip()
            if not title or not href:
                continue
            href = _normalize_search_result_url(href)
            if not href or href in seen_urls:
                continue
            snippet_node = node.select_one(".b_caption p") or node.select_one("p")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
            seen_urls.add(href)
            results.append(BrowserSearchHit(title=title, url=href, snippet=snippet, provider="bing-browser"))
            if len(results) >= max_results:
                break
        return results



browser_automation = BrowserAutomation()

