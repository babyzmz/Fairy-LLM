"""Readability extractor stub."""
from __future__ import annotations


def extract_readable(html: str) -> str:
    """Return main readable text from HTML. Basic heuristic implementation."""
    import re
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()[:6000]
