"""Numeric extractor — pulls price/spec numbers from text."""
from __future__ import annotations
import re


def extract_numbers(text: str) -> list[str]:
    return re.findall(
        r"[\$\u00a5\u20ac\xa3]?[\d,]+\.?\d*\s*(?:%|USD|CNY|AUD|EUR|GBP|kg|km|GB|TB)?",
        text or ""
    )[:30]
