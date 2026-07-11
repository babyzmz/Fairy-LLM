from __future__ import annotations

from typing import Protocol

from fairy_core.research.models import (
    FetchedDocument,
    FetchRequest,
    ResearchCapabilityHealth,
    SearchHit,
    SearchRequest,
)


class SearchPort(Protocol):
    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]: ...

    def health(self) -> ResearchCapabilityHealth: ...


class FetchPort(Protocol):
    def fetch(self, request: FetchRequest) -> FetchedDocument: ...


__all__ = ["FetchPort", "SearchPort"]
