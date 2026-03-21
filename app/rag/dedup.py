from __future__ import annotations

import hashlib
import re


TIMESTAMP_PATTERN = re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:[ t]\d{2}:\d{2}(?::\d{2})?)?\b")
FILLER_PATTERN = re.compile(r"\b(其实|就是|然后|那个|这个|那个时候)\b")


def normalize_text_for_dedup(text: str) -> str:
    normalized = text.lower().strip()
    normalized = TIMESTAMP_PATTERN.sub(" ", normalized)
    normalized = FILLER_PATTERN.sub(" ", normalized)
    normalized = normalized.replace("：", ":").replace("，", ",").replace("。", ".")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def fingerprint_text(text: str) -> str:
    normalized = normalize_text_for_dedup(text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
