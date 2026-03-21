"""Table extractor — parses HTML tables into list-of-rows."""
from __future__ import annotations
import re


def extract_tables(html: str) -> list[list[str]]:
    tables: list[list[str]] = []
    for tbl in re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL | re.I)[:3]:
        cells = re.findall(r"<t[dh][^>]*>([^<]*)</t[dh]>", tbl, re.I)
        if cells:
            tables.append([c.strip() for c in cells[:20]])
    return tables
