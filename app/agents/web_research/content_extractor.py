"""Content extractor — parses raw page text into structured ExtractedEvidence."""
from __future__ import annotations
import re
from app.agents.web_research.models import ExtractedEvidence


class ContentExtractor:
    """Heuristic extractor for raw page text / HTML snippets."""

    @staticmethod
    def extract(url: str, raw_html: str, fallback_title: str = "") -> ExtractedEvidence:
        text = ContentExtractor._strip_tags(raw_html)
        title = ContentExtractor._extract_title(raw_html) or fallback_title
        date = ContentExtractor._extract_date(text)
        numbers = ContentExtractor._extract_numbers(text)
        tables = ContentExtractor._extract_tables(raw_html)
        domain = re.search(r"https?://([^/]+)", url or "")
        domain_str = domain.group(1) if domain else ""
        # Trim to reasonable length
        main_text = text[:4000].strip()
        return ExtractedEvidence(
            url=url,
            title=title,
            main_text=main_text,
            publish_date=date,
            domain=domain_str,
            word_count=len(main_text.split()),
            numbers=numbers,
            tables=tables,
            extraction_ok=True,
        )

    @staticmethod
    def _strip_tags(html: str) -> str:
        # Remove script/style blocks
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Collapse whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _extract_title(html: str) -> str:
        m = re.search(r"<title[^>]*>([^<]{1,200})</title>", html, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r"<h1[^>]*>([^<]{1,200})</h1>", html, re.I)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_date(text: str) -> str:
        m = re.search(r"(202\d[-/]\d{1,2}[-/]\d{1,2})", text)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_numbers(text: str) -> list[str]:
        return re.findall(r"[\$\u00a5\u20ac\xa3]?[\d,]+\.?\d*\s*(?:%|USD|CNY|AUD|EUR|GBP)?", text)[:20]

    @staticmethod
    def _extract_tables(html: str) -> list[list[str]]:
        tables: list[list[str]] = []
        for table_html in re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL | re.I)[:3]:
            cells = re.findall(r"<t[dh][^>]*>([^<]*)</t[dh]>", table_html, re.I)
            if cells:
                tables.append([c.strip() for c in cells[:20]])
        return tables
