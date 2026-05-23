from __future__ import annotations

import atexit
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal
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
from utils.browser_cdp_attach import CdpAttachBackend, probe_cdp_http_origin


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
BROWSER_EXECUTABLE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "chrome": (
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ),
    "edge": (
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ),
}
SMOKE_HTML = "data:text/html,<html><body><h1>Hello Fairy</h1><a href=\"javascript:void(0)\" id=\"go\">Go</a><p>browser smoke</p></body></html>"
BrowserAvailabilityLevel = Literal["full", "partial", "unavailable"]


def _env_truthy(name: str, default: bool = True) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


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


@dataclass(slots=True)
class BrowserAvailabilityReport:
    available: bool
    availability_level: BrowserAvailabilityLevel
    reason: str
    fallback_mode: str = "rendered_read"
    executable_path: str = ""
    browser_type: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> BrowserAvailabilityLevel:
        return self.availability_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "availability_level": self.availability_level,
            "level": self.availability_level,
            "reason": self.reason,
            "fallback_mode": self.fallback_mode,
            "executable_path": self.executable_path,
            "browser_type": self.browser_type,
            "diagnostics": dict(self.diagnostics),
        }


class BrowserAutomation:
    """Lazy Playwright wrapper with transient and interactive browser sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._cdp_backend: CdpAttachBackend | None = None
        self._sessions: dict[str, tuple[Any, Any]] = {}
        self._availability_cache: BrowserAvailabilityReport | None = None
        self._availability_checked_at = 0.0
        self._availability_ttl_sec = 120.0
        self._last_diagnostics: dict[str, Any] = {}
        atexit.register(self.close)

    def available(self) -> bool:
        return self.availability_status().available

    def availability_status(self, *, force_refresh: bool = False) -> BrowserAvailabilityReport:
        now = time.time()
        with self._lock:
            if (
                not force_refresh
                and self._availability_cache is not None
                and now - self._availability_checked_at <= self._availability_ttl_sec
            ):
                return self._availability_cache
        report = self._diagnose_availability()
        with self._lock:
            self._availability_cache = report
            self._availability_checked_at = now
            self._last_diagnostics = dict(report.diagnostics)
        return report

    def last_diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_diagnostics)

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

    def _page_result_from_payload(self, payload: dict[str, Any]) -> BrowserPageResult:
        return BrowserPageResult(
            requested_url=str(payload.get("requested_url", "")).strip(),
            final_url=str(payload.get("final_url", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            html=str(payload.get("html", "") or ""),
            meta_description=str(payload.get("meta_description", "")).strip(),
            visible_text=str(payload.get("visible_text", "")).strip(),
            headings=list(payload.get("headings", []) or []),
            links=list(payload.get("links", []) or []),
            key_values=list(payload.get("key_values", []) or []),
            table_rows=list(payload.get("table_rows", []) or []),
            screenshot_path=str(payload.get("screenshot_path", "")).strip(),
            handle_id=str(payload.get("handle_id", "")).strip(),
            status_code=int(payload.get("status_code", 200) or 200),
            blocked_reason=str(payload.get("blocked_reason", "")).strip(),
        )

    def _using_cdp_backend(self) -> bool:
        report = self.availability_status()
        return str(report.diagnostics.get("backend") or "") == "cdp_attach"

    def _ensure_cdp_backend(self) -> CdpAttachBackend:
        report = self.availability_status()
        origin = str(report.diagnostics.get("cdp_attach_origin") or "").strip()
        if not origin:
            raise RuntimeError("cdp_attach_origin_missing")
        with self._lock:
            if self._cdp_backend is not None and self._cdp_backend.http_origin == origin:
                return self._cdp_backend
            self._cdp_backend = CdpAttachBackend(origin)
            return self._cdp_backend

    def open_page(
        self,
        url: str,
        *,
        timeout_ms: int = 15000,
        wait_after_load_ms: int = 1200,
        capture_screenshot: bool = True,
    ) -> BrowserPageResult:
        if self._using_cdp_backend():
            payload = self._ensure_cdp_backend().open_page(
                url,
                timeout_ms=timeout_ms,
                wait_after_load_ms=wait_after_load_ms,
                capture_screenshot=capture_screenshot,
            )
            return self._page_result_from_payload(payload)
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
        if self._using_cdp_backend():
            payload = self._ensure_cdp_backend().open_interactive_page(
                url,
                timeout_ms=timeout_ms,
                wait_after_load_ms=wait_after_load_ms,
                capture_screenshot=capture_screenshot,
            )
            return self._page_result_from_payload(payload)
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
        if self._using_cdp_backend():
            payload = self._ensure_cdp_backend().extract_session(handle_id, capture_screenshot=capture_screenshot)
            return self._page_result_from_payload(payload)
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
        if self._using_cdp_backend():
            payload = self._ensure_cdp_backend().interact(
                handle_id,
                action=action,
                selector=selector,
                text=text,
                key=key,
                delta_y=delta_y,
                timeout_ms=timeout_ms,
                wait_after_load_ms=wait_after_load_ms,
            )
            return self._page_result_from_payload(payload)
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
        if self._cdp_backend is not None:
            self._cdp_backend.close_session(handle_id)
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
            cdp_backend = self._cdp_backend
            self._browser = None
            self._playwright = None
            self._cdp_backend = None
            self._availability_cache = None
            self._availability_checked_at = 0.0
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
        if cdp_backend is not None:
            try:
                cdp_backend.close()
            except Exception:
                pass

    def _diagnose_availability(self) -> BrowserAvailabilityReport:
        diagnostics: dict[str, Any] = {
            "browser_package": "unknown",
            "browser_binary": "unknown",
            "launch": "not_attempted",
            "open_page": "not_attempted",
            "extract": "not_attempted",
            "interaction_smoke": "not_attempted",
            "last_launch_error": "",
            "last_smoke_result": "not_run",
            "backend": "",
        }
        executable_path, browser_type, resolution_reason, resolution_diag = self._resolve_browser_preferences()
        diagnostics.update(resolution_diag)
        diagnostics["browser_type"] = browser_type
        diagnostics["executable_path"] = executable_path

        attach_origin = self._resolve_attach_origin()
        if attach_origin:
            diagnostics["cdp_attach_origin"] = attach_origin
            try:
                smoke = CdpAttachBackend(attach_origin).smoke(SMOKE_HTML)
                diagnostics.update(smoke)
                diagnostics["browser_package"] = "not_required"
                return BrowserAvailabilityReport(
                    available=True,
                    availability_level=str(smoke.get("status") or "partial"),
                    reason=str(smoke.get("reason") or "browser_automation_ready"),
                    fallback_mode="" if str(smoke.get("status")) == "full" else "rendered_read",
                    executable_path=executable_path,
                    browser_type=browser_type or "chrome",
                    diagnostics=diagnostics,
                )
            except Exception as exc:  # noqa: BLE001
                diagnostics["cdp_attach_error"] = repr(exc)

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            diagnostics["browser_package"] = "missing"
            diagnostics["import_error"] = repr(exc)
            diagnostics["failure_cause"] = "package_missing"
            return BrowserAvailabilityReport(
                available=False,
                availability_level="unavailable",
                reason="package_missing",
                diagnostics=diagnostics,
            )

        diagnostics["browser_package"] = "ok"
        if resolution_reason:
            diagnostics["failure_cause"] = resolution_reason
            return BrowserAvailabilityReport(
                available=False,
                availability_level="unavailable",
                reason=resolution_reason,
                executable_path=executable_path,
                browser_type=browser_type,
                diagnostics=diagnostics,
            )

        smoke = self._run_playwright_smoke(
            sync_playwright=sync_playwright,
            executable_path=executable_path,
            browser_type=browser_type,
            headless=self._resolve_headless(),
        )
        diagnostics.update(smoke)
        if smoke.get("status") == "full":
            return BrowserAvailabilityReport(
                available=True,
                availability_level="full",
                reason="browser_automation_ready",
                fallback_mode="",
                executable_path=executable_path,
                browser_type=browser_type,
                diagnostics=diagnostics,
            )
        if smoke.get("status") == "partial":
            return BrowserAvailabilityReport(
                available=True,
                availability_level="partial",
                reason=str(smoke.get("reason") or "page_operation_failed"),
                fallback_mode="rendered_read",
                executable_path=executable_path,
                browser_type=browser_type,
                diagnostics=diagnostics,
            )
        diagnostics["failure_cause"] = str(smoke.get("reason") or "browser_launch_failed")
        return BrowserAvailabilityReport(
            available=False,
            availability_level="unavailable",
            reason=str(smoke.get("reason") or "browser_launch_failed"),
            fallback_mode="rendered_read",
            executable_path=executable_path,
            browser_type=browser_type,
            diagnostics=diagnostics,
        )

    def _resolve_headless(self) -> bool:
        return _env_truthy("FAIRY_BROWSER_HEADLESS", default=True)

    def _resolve_attach_origin(self) -> str:
        explicit_url = str(os.getenv("FAIRY_BROWSER_REMOTE_DEBUGGING_URL") or "").strip()
        explicit_port = str(os.getenv("FAIRY_BROWSER_REMOTE_DEBUGGING_PORT") or "").strip()
        ports_raw = str(os.getenv("FAIRY_BROWSER_REMOTE_DEBUGGING_PORTS") or "").strip()
        origins: list[str] = []
        if explicit_url:
            origins.append(explicit_url if explicit_url.startswith("http") else f"http://127.0.0.1:{explicit_url}")
        if explicit_port:
            origins.append(f"http://127.0.0.1:{explicit_port}")
        if ports_raw:
            for item in ports_raw.split(","):
                port = item.strip()
                if port:
                    origins.append(f"http://127.0.0.1:{port}")
        origins.extend(["http://127.0.0.1:9778", "http://127.0.0.1:9222", "http://127.0.0.1:9777"])
        deduped: list[str] = []
        for origin in origins:
            if origin not in deduped:
                deduped.append(origin)
        return probe_cdp_http_origin(deduped)

    def _resolve_browser_preferences(self) -> tuple[str, str, str, dict[str, Any]]:
        diagnostics: dict[str, Any] = {"executable_source": ""}
        requested_type = str(os.getenv("FAIRY_BROWSER_TYPE") or "").strip().lower()
        if requested_type not in {"", "chrome", "edge", "chromium"}:
            requested_type = "chromium"
        env_path = str(os.getenv("FAIRY_BROWSER_EXECUTABLE_PATH") or "").strip()
        if env_path:
            diagnostics["executable_source"] = "env"
            if not Path(env_path).exists():
                diagnostics["browser_binary"] = "missing"
                return env_path, requested_type or self._infer_browser_type(env_path), "executable_resolution_failed", diagnostics
            diagnostics["browser_binary"] = "ok"
            return env_path, requested_type or self._infer_browser_type(env_path), "", diagnostics

        bundled_path = self._find_playwright_browser_executable()
        if bundled_path:
            diagnostics["executable_source"] = "playwright_bundle"
            diagnostics["browser_binary"] = "ok"
            return bundled_path, requested_type or "chromium", "", diagnostics

        requested_order = []
        if requested_type in {"chrome", "edge"}:
            requested_order.append(requested_type)
        requested_order.extend([item for item in ("chrome", "edge") if item not in requested_order])
        for browser_name in requested_order:
            for candidate in BROWSER_EXECUTABLE_CANDIDATES.get(browser_name, ()):
                if Path(candidate).exists():
                    diagnostics["executable_source"] = "system_install"
                    diagnostics["browser_binary"] = "ok"
                    return candidate, browser_name, "", diagnostics

        diagnostics["browser_binary"] = "missing"
        return "", requested_type or "chromium", "browser_binary_missing", diagnostics

    def _find_playwright_browser_executable(self) -> str:
        candidate_roots = []
        configured_root = str(os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
        if configured_root and configured_root != "0":
            candidate_roots.append(Path(configured_root))
        candidate_roots.extend([Path.cwd() / ".ms-playwright", Path.home() / "AppData" / "Local" / "ms-playwright"])
        for root in candidate_roots:
            if not root.exists():
                continue
            for pattern in ("chromium-*", "chrome-*", "chromium_headless_shell-*"):
                for entry in root.glob(pattern):
                    for rel in (Path("chrome-win/chrome.exe"), Path("chrome-win/headless_shell.exe"), Path("chrome.exe")):
                        target = entry / rel
                        if target.exists():
                            return str(target)
        return ""

    @staticmethod
    def _infer_browser_type(executable_path: str) -> str:
        lowered = executable_path.lower()
        if "edge" in lowered or "msedge" in lowered:
            return "edge"
        if "chrome" in lowered:
            return "chrome"
        return "chromium"

    def _run_playwright_smoke(self, *, sync_playwright, executable_path: str, browser_type: str, headless: bool) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {"status": "unavailable", "reason": "unknown_but_traced"}
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_kwargs)
                diagnostics["launch"] = "ok"
                page = browser.new_page()
                page.goto(SMOKE_HTML, wait_until="domcontentloaded", timeout=10000)
                diagnostics["open_page"] = "ok"
                body_text = str(page.text_content("body") or "").strip()
                diagnostics["extract"] = "ok" if body_text else "empty"
                diagnostics["last_smoke_result"] = body_text[:120]
                try:
                    page.click("#go", timeout=5000)
                    diagnostics["interaction_smoke"] = "ok"
                    diagnostics["status"] = "full"
                    diagnostics["reason"] = "browser_automation_ready"
                except Exception as exc:  # noqa: BLE001
                    diagnostics["interaction_smoke"] = "failed"
                    diagnostics["interaction_error"] = repr(exc)
                    diagnostics["status"] = "partial"
                    diagnostics["reason"] = "page_operation_failed"
                browser.close()
                return diagnostics
        except Exception as exc:  # noqa: BLE001
            diagnostics["launch"] = "failed"
            diagnostics["last_launch_error"] = repr(exc)
            diagnostics["last_smoke_result"] = "launch_failed"
            diagnostics["status"] = "unavailable"
            diagnostics["reason"] = "browser_launch_failed"
            return diagnostics

    def _ensure_browser(self):
        report = self.availability_status()
        if not report.available and report.availability_level == "unavailable":
            raise RuntimeError(f"Browser automation unavailable: {report.reason}")
        with self._lock:
            if self._browser is not None:
                return self._browser
            from playwright.sync_api import sync_playwright

            launch_kwargs: dict[str, Any] = {"headless": self._resolve_headless()}
            if report.executable_path:
                launch_kwargs["executable_path"] = report.executable_path
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
                return self._browser
            except Exception:
                playwright = self._playwright
                self._playwright = None
                if playwright is not None:
                    try:
                        playwright.stop()
                    except Exception:
                        pass
                with self._lock:
                    self._availability_cache = None
                    self._availability_checked_at = 0.0
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


