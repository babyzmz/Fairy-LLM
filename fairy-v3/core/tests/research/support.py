from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fairy_core.research.models import FetchedDocument, FetchRequest


class FixtureFetchPort:
    def __init__(self, documents: dict[str, FetchedDocument]) -> None:
        self.documents = documents
        self.requests: list[FetchRequest] = []

    def fetch(self, request: FetchRequest) -> FetchedDocument:
        self.requests.append(request)
        return self.documents[request.url]


def document(
    requested_url: str,
    *,
    final_url: str | None = None,
    title: str = "Source",
    text: str = "Evidence text",
) -> FetchedDocument:
    body = text.encode("utf-8")
    resolved = final_url or requested_url
    chain = (requested_url, resolved) if requested_url != resolved else (requested_url,)
    return FetchedDocument.create(
        requested_url=requested_url,
        final_url=resolved,
        redirect_chain=chain,
        media_type="text/plain",
        byte_length=len(body),
        content_hash=hashlib.sha256(body).hexdigest(),
        title=title,
        text=text,
        fetched_at=datetime(2026, 7, 11, 3, 0, tzinfo=UTC),
    )
