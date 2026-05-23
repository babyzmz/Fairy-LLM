from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    key: str
    canonical_name: str
    domains: tuple[str, ...]
    aliases: tuple[str, ...]
    home_url: str = ""
    section_urls: dict[str, str] = field(default_factory=dict)
    entrypoints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    navigation_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    known_product_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)
    page_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)


SOURCE_REGISTRY: dict[str, SourceDescriptor] = {
    "ithome": SourceDescriptor(
        key="ithome",
        canonical_name="IT之家",
        domains=("ithome.com", "www.ithome.com"),
        aliases=("IT之家", "it之家", "IT 之家", "IThome", "ithome", "ithome.com"),
        home_url="https://www.ithome.com/",
        section_urls={
            "news": "https://www.ithome.com/list/",
            "tech": "https://www.ithome.com/tags/%E7%A7%91%E6%8A%80/",
        },
        entrypoints={
            "news": ("https://www.ithome.com/list/",),
            "tech": ("https://www.ithome.com/tags/%E7%A7%91%E6%8A%80/",),
        },
        navigation_hints={
            "news": ("科技", "新闻", "最新"),
        },
        page_markers={
            "news_index": ("/list/", "/tags/", "IT之家", "科技"),
        },
    ),
    "openai": SourceDescriptor(
        key="openai",
        canonical_name="OpenAI",
        domains=("openai.com", "www.openai.com", "platform.openai.com"),
        aliases=("OpenAI", "openai", "OpenAI 官网", "openai.com"),
        home_url="https://openai.com/",
        section_urls={
            "news": "https://openai.com/news/",
            "docs": "https://platform.openai.com/docs/",
        },
        entrypoints={
            "news": ("https://openai.com/news/",),
        },
        navigation_hints={
            "news": ("News", "Latest", "Launches"),
            "release": ("News", "Launches", "Announces"),
        },
        page_markers={
            "news_index": ("/news", "OpenAI News"),
            "press_release": ("launches", "announces"),
        },
    ),
    "microsoft": SourceDescriptor(
        key="microsoft",
        canonical_name="Microsoft",
        domains=("microsoft.com", "www.microsoft.com", "news.microsoft.com"),
        aliases=("微软", "微软官网", "Microsoft", "microsoft", "microsoft.com"),
        home_url="https://www.microsoft.com/",
        section_urls={
            "news": "https://news.microsoft.com/",
        },
        entrypoints={
            "news": ("https://news.microsoft.com/source/", "https://news.microsoft.com/"),
        },
        navigation_hints={
            "news": ("Source", "News", "Stories"),
            "release": ("Source", "News", "Stories"),
        },
        page_markers={
            "news_index": ("news.microsoft.com/source", "Microsoft Source"),
        },
    ),
    "apple": SourceDescriptor(
        key="apple",
        canonical_name="Apple",
        domains=("apple.com", "www.apple.com", "developer.apple.com"),
        aliases=(
            "苹果",
            "苹果官网",
            "Apple",
            "apple",
            "apple.com",
            "AirPods",
            "AirPods Max",
            "iPad",
            "iPad mini",
            "iPad mini 7",
            "iPad mini7",
            "iPad Pro",
            "iPhone",
            "MacBook",
            "Apple Watch",
        ),
        home_url="https://www.apple.com/",
        section_urls={
            "news": "https://www.apple.com/newsroom/",
        },
        entrypoints={
            "news": ("https://www.apple.com/newsroom/",),
            "products": ("https://www.apple.com/",),
        },
        navigation_hints={
            "specs": ("Tech Specs", "Specifications", "Compare"),
            "release": ("Newsroom", "Announces", "Press Release"),
            "news": ("Newsroom", "Latest", "News"),
            "product_lookup": ("iPad Pro", "iPad mini", "AirPods Max", "iPhone", "MacBook"),
        },
        known_product_paths={
            "ipad mini": ("/ipad-mini/specs/", "/ipad-mini/"),
            "ipad pro": ("/ipad-pro/specs/", "/ipad-pro/"),
            "airpods max": ("/airpods-max/",),
        },
        page_markers={
            "specs": ("/specs/", "Tech Specs", "Specifications"),
            "product": ("/ipad-pro/", "/ipad-mini/", "/airpods-max/"),
            "news_index": ("/newsroom/", "Newsroom"),
            "press_release": ("/newsroom/", "announces", "release"),
        },
    ),
}


def extract_explicit_url(text: str) -> str:
    match = _URL_RE.search(text or "")
    return match.group(0).strip() if match else ""


def resolve_source_descriptor(text: str, explicit_source: str = "") -> SourceDescriptor | None:
    haystack = f"{explicit_source or ''} {text or ''}".strip().lower()
    if not haystack:
        return None
    for descriptor in SOURCE_REGISTRY.values():
        if any(alias.lower() in haystack for alias in descriptor.aliases):
            return descriptor
        if any(domain.lower() in haystack for domain in descriptor.domains):
            return descriptor
    explicit_url = extract_explicit_url(f"{explicit_source or ''} {text or ''}")
    if explicit_url:
        host = _host_from_url(explicit_url)
        if host:
            return _descriptor_for_host(host)
    domain_match = _DOMAIN_RE.search(haystack)
    if domain_match:
        return _descriptor_for_host(domain_match.group(0).lower())
    return None


def preferred_domains_for_source(descriptor: SourceDescriptor | None) -> list[str]:
    if descriptor is None:
        return []
    return list(dict.fromkeys(domain.lower() for domain in descriptor.domains))


def canonical_source_name_from_url(url: str) -> str:
    text = str(url or "").strip().lower()
    if not text:
        return ""
    for descriptor in SOURCE_REGISTRY.values():
        if any(domain.lower() in text for domain in descriptor.domains):
            return descriptor.canonical_name
    host = _host_from_url(text)
    if host:
        return host
    return ""


def _descriptor_for_host(host: str) -> SourceDescriptor | None:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return None
    for descriptor in SOURCE_REGISTRY.values():
        if any(normalized == domain.lower() or normalized.endswith(f".{domain.lower()}") for domain in descriptor.domains):
            return descriptor
    home_url = f"https://{normalized}/"
    canonical = normalized[4:] if normalized.startswith("www.") else normalized
    return SourceDescriptor(
        key=f"dynamic:{canonical}",
        canonical_name=canonical,
        domains=(normalized,),
        aliases=(canonical, normalized),
        home_url=home_url,
        entrypoints={
            "news": (home_url,),
            "products": (home_url,),
            "general_info": (home_url,),
        },
        navigation_hints={
            "news": ("News", "Latest", "Blog", "Updates"),
            "release": ("News", "Press", "Release", "Announce"),
            "specs": ("Specs", "Specifications", "Tech Specs", "Features", "Compare"),
            "product_lookup": ("Products", "Solutions", "Features", "Catalog"),
            "general_info": ("About", "Docs", "Learn", "Blog", "Products"),
        },
        page_markers={
            "news_index": ("news", "blog", "updates"),
            "press_release": ("press", "release", "announc"),
            "specs": ("spec", "specification", "features", "compare"),
            "product": ("product", "products", "solutions"),
        },
    )


def _host_from_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    candidate = text
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    try:
        host = urlparse(candidate).netloc.lower()
    except Exception:
        return ""
    return host.split("@")[-1].split(":")[0].strip()
