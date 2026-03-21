"""Metadata extractor."""
from __future__ import annotations
import re


def extract_metadata(html: str) -> dict:
    """Extract og/meta tags from HTML."""
    meta: dict = {}
    for m in re.finditer(r'<meta[^>]+>', html, re.I):
        tag = m.group(0)
        prop = re.search(r'(?:property|name)=["\']([^"\']+)["\']', tag, re.I)
        content = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
        if prop and content:
            meta[prop.group(1).lower()] = content.group(1)
    return meta
