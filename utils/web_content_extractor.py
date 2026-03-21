from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass(slots=True)
class ExtractedWebContent:
    title: str
    meta_description: str
    body_text: str


def extract_web_content(html: str) -> ExtractedWebContent:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""

    meta_description = ""
    meta_node = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_node is not None:
        meta_description = (meta_node.get("content") or "").strip()

    for node in soup(["script", "style", "noscript", "header", "footer", "svg", "form"]):
        node.decompose()

    body_text = soup.get_text("\n", strip=True)
    body_text = re.sub(r"\n{2,}", "\n", body_text)
    body_text = body_text.strip()

    return ExtractedWebContent(
        title=title,
        meta_description=meta_description,
        body_text=body_text,
    )
