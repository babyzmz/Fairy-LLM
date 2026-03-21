from __future__ import annotations

import warnings
from typing import Any, Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from app.config import llm_config
from utils.browser_automation import browser_automation


SearchDiagnostics = list[dict[str, Any]]
SEARCH_ENGINE_DOMAINS = (
    "bing.com",
    "duckduckgo.com",
    "google.com",
)
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "form",
    "spm",
    "from",
    "source",
    "scm",
    "campaign",
    "ref",
}


def _is_search_engine_host(host: str) -> bool:
    lowered = host.lower()
    if "searx" in lowered:
        return True
    return any(lowered == domain or lowered.endswith(f".{domain}") for domain in SEARCH_ENGINE_DOMAINS)


def _looks_like_search_engine_url(url: str) -> bool:
    parsed = urlparse(url)
    if not _is_search_engine_host(parsed.netloc):
        return False
    path = parsed.path.lower()
    return path in {"", "/"} or path.startswith("/search") or "html" in path or path.startswith("/lite")


def _strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    query = parse_qs(parsed.query, keep_blank_values=False)
    clean_pairs: list[tuple[str, str]] = []
    for key, values in query.items():
        if key.lower() in TRACKING_QUERY_KEYS:
            continue
        for value in values:
            clean_pairs.append((key, value))
    clean_query = urlencode(clean_pairs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, ""))


def normalize_search_result_url(result: dict[str, Any] | str) -> str:
    raw_url = str(result.get("url", "") if isinstance(result, dict) else result).strip()
    if not raw_url.startswith(("http://", "https://")):
        return ""

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
                    query = parse_qs(parsed.query)
                    break

    if "google." in parsed.netloc:
        for key in ("q", "url"):
            value = query.get(key)
            if value:
                candidate = unquote(value[0])
                if candidate.startswith(("http://", "https://")):
                    raw_url = candidate
                    parsed = urlparse(raw_url)
                    query = parse_qs(parsed.query)
                    break

    raw_url = _strip_tracking_params(raw_url)
    if _looks_like_search_engine_url(raw_url):
        return ""
    return raw_url


def _parse_duckduckgo_results(html: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for node in soup.select(".result"):
        anchor = node.select_one(".result__a") or node.select_one("a.result-link")
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()
        if not title or not href:
            continue
        url = normalize_search_result_url(href)
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        snippet_node = (
            node.select_one(".result__snippet")
            or node.select_one(".result-snippet")
            or node.select_one(".result__body")
        )
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "provider": "duckduckgo-html"})
        if len(results) >= max_results:
            break
    return results


def _parse_bing_results(html: str, max_results: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    candidate_nodes = soup.select("li.b_algo") or soup.select(".algo") or soup.select("main li")
    for node in candidate_nodes:
        anchor = node.select_one("h2 a") or node.select_one("a[href]")
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()
        if not title or not href or href in seen_urls:
            continue
        if not href.startswith(("http://", "https://")):
            continue
        href = normalize_search_result_url(href)
        if not href:
            continue
        snippet_node = node.select_one(".b_caption p") or node.select_one("p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        seen_urls.add(href)
        results.append({"title": title, "url": href, "snippet": snippet, "provider": "bing-html"})
        if len(results) >= max_results:
            break
    return results


def _import_ddgs() -> Any | None:
    try:
        from ddgs import DDGS  # type: ignore
        return DDGS
    except Exception:
        return None


def _search_via_ddgs(query: str, max_results: int) -> list[dict[str, Any]]:
    DDGS = _import_ddgs()
    if DDGS is None:
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=max_results):
                    url = normalize_search_result_url(str(item.get("href", "")).strip())
                    title = str(item.get("title", "")).strip()
                    snippet = str(item.get("body", "")).strip()
                    if not title or not url or url in seen_urls:
                        continue
                    if not url.startswith(("http://", "https://")):
                        continue
                    seen_urls.add(url)
                    results.append({"title": title, "url": url, "snippet": snippet, "provider": "ddgs"})
                    if len(results) >= max_results:
                        break
    except Exception:
        return []
    return results


def _search_via_searxng(query: str, max_results: int, timeout_sec: int) -> list[dict[str, Any]]:
    base_url = llm_config.search_api_url.strip().rstrip("/")
    if not base_url:
        return []

    headers = {"User-Agent": "Mozilla/5.0"}
    if llm_config.search_api_key:
        headers["Authorization"] = f"Bearer {llm_config.search_api_key}"

    try:
        response = requests.get(
            f"{base_url}/search",
            params={"q": query, "format": "json"},
            headers=headers,
            timeout=timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in payload.get("results", [])[:max_results]:
        url = normalize_search_result_url(str(item.get("url", "")).strip())
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("content", "")).strip()
        if not title or not url or url in seen_urls:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "provider": "searxng"})
    return results


def _sort_preferred(results: list[dict[str, Any]], preferred_domains: Iterable[str] | None) -> list[dict[str, Any]]:
    preferred = tuple(domain.lower() for domain in (preferred_domains or []))
    if not preferred:
        return results

    def score(item: dict[str, Any]) -> tuple[int, str]:
        domain = urlparse(str(item.get("url", ""))).netloc.lower()
        preferred_hit = 0 if any(hint in domain for hint in preferred) else 1
        return (preferred_hit, domain)

    return sorted(results, key=score)


def _record_attempt(
    diagnostics: SearchDiagnostics,
    provider: str,
    *,
    ok: bool,
    result_count: int = 0,
    error: str = "",
) -> None:
    diagnostics.append(
        {
            "provider": provider,
            "ok": ok,
            "result_count": int(result_count),
            "error": error.strip(),
        }
    )


def search_web_detailed(
    query: str,
    max_results: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> dict[str, Any]:
    diagnostics: SearchDiagnostics = []

    if llm_config.search_backend in {"auto", "searxng"}:
        try:
            searxng_results = _search_via_searxng(query, max_results=max_results, timeout_sec=timeout_sec)
            _record_attempt(diagnostics, "searxng", ok=bool(searxng_results), result_count=len(searxng_results))
            if searxng_results:
                return {
                    "results": _sort_preferred(searxng_results, preferred_domains)[:max_results],
                    "diagnostics": diagnostics,
                }
        except Exception as exc:
            _record_attempt(diagnostics, "searxng", ok=False, error=str(exc))

    if llm_config.browser_automation_enabled and llm_config.search_backend in {"auto", "browser", "browser_automation"}:
        if browser_automation.available():
            try:
                browser_results = browser_automation.search(
                    query,
                    max_results=max_results,
                    timeout_ms=llm_config.browser_navigation_timeout_sec * 1000,
                    wait_after_load_ms=llm_config.browser_post_load_wait_ms,
                    preferred_domains=preferred_domains,
                )
                _record_attempt(diagnostics, "browser", ok=bool(browser_results), result_count=len(browser_results))
                if browser_results:
                    return {
                        "results": [
                            {
                                "title": item.title,
                                "url": item.url,
                                "snippet": item.snippet,
                                "provider": item.provider,
                            }
                            for item in browser_results[:max_results]
                        ],
                        "diagnostics": diagnostics,
                    }
            except Exception as exc:
                _record_attempt(diagnostics, "browser", ok=False, error=str(exc))
        else:
            _record_attempt(diagnostics, "browser", ok=False, error="browser_automation_unavailable")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        )
    }
    search_urls = [
        ("duckduckgo-html", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
        ("duckduckgo-html", f"https://duckduckgo.com/html/?q={quote_plus(query)}"),
    ]

    for provider, search_url in search_urls:
        try:
            response = requests.get(search_url, headers=headers, timeout=timeout_sec)
            response.raise_for_status()
            results = _parse_duckduckgo_results(response.text, max_results=max_results)
            _record_attempt(
                diagnostics,
                provider,
                ok=bool(results),
                result_count=len(results),
                error="http_202_empty" if response.status_code == 202 and not results else "",
            )
            if results:
                return {
                    "results": _sort_preferred(results, preferred_domains)[:max_results],
                    "diagnostics": diagnostics,
                }
        except Exception as exc:
            _record_attempt(diagnostics, provider, ok=False, error=str(exc))

    try:
        response = requests.get(
            f"https://www.bing.com/search?q={quote_plus(query)}",
            headers=headers,
            timeout=timeout_sec,
        )
        response.raise_for_status()
        results = _parse_bing_results(response.text, max_results=max_results)
        _record_attempt(diagnostics, "bing-html", ok=bool(results), result_count=len(results))
        if results:
            return {
                "results": _sort_preferred(results, preferred_domains)[:max_results],
                "diagnostics": diagnostics,
            }
    except Exception as exc:
        _record_attempt(diagnostics, "bing-html", ok=False, error=str(exc))

    if llm_config.search_backend in {"auto", "ddgs"}:
        try:
            ddgs_results = _search_via_ddgs(query, max_results=max_results)
            _record_attempt(diagnostics, "ddgs", ok=bool(ddgs_results), result_count=len(ddgs_results))
            if ddgs_results:
                return {
                    "results": _sort_preferred(ddgs_results, preferred_domains)[:max_results],
                    "diagnostics": diagnostics,
                }
        except Exception as exc:
            _record_attempt(diagnostics, "ddgs", ok=False, error=str(exc))

    return {"results": [], "diagnostics": diagnostics}


def search_web(
    query: str,
    max_results: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    return search_web_detailed(
        query,
        max_results=max_results,
        timeout_sec=timeout_sec,
        preferred_domains=preferred_domains,
    )["results"]


def search_web_queries(
    queries: Iterable[str],
    *,
    max_results_per_query: int = 5,
    timeout_sec: int = 12,
    preferred_domains: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for order, query in enumerate(queries):
        q = query.strip()
        if not q:
            continue
        try:
            results = search_web(
                q,
                max_results=max_results_per_query,
                timeout_sec=timeout_sec,
                preferred_domains=preferred_domains,
            )
        except Exception:
            continue
        for rank, item in enumerate(results):
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(
                {
                    **item,
                    "query": q,
                    "query_order": order,
                    "result_rank": rank,
                }
            )
    return merged
