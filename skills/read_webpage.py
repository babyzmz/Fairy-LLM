from __future__ import annotations

from skills.crawl_webpage import crawl_webpage
from utils.web_content_extractor import extract_web_content


def read_webpage(url: str, timeout_sec: int = 12) -> str:
    try:
        crawled = crawl_webpage(url, timeout_sec=timeout_sec)
        extracted = extract_web_content(crawled.html)
        parts = []
        if extracted.meta_description:
            parts.append(extracted.meta_description)
        if extracted.body_text:
            parts.append(extracted.body_text)
        merged = "\n".join(parts).strip()
        if merged:
            return merged[:6000]
    except Exception:
        pass

    try:
        from newspaper import Article

        article = Article(url)
        article.download()
        article.parse()
        text = (article.text or "").strip()
        if text:
            return text[:6000]
    except Exception:
        pass
    return ""
