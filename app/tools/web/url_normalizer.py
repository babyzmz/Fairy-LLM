"""URL normalizer — cleans and canonicalizes URLs."""
from __future__ import annotations
import re


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    # Remove tracking params
    url = re.sub(r"[?&](utm_[a-z]+|ref|src)=[^&]*", "", url)
    return url


def is_valid_url(url: str) -> bool:
    return bool(re.match(r"https?://[^\s]{4,}", url or ""))
