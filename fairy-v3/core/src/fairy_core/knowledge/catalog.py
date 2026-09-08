from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class KnowledgeRevisionMetadata:
    """Display-only projection; never a substitute for a verified Snapshot read."""

    id: UUID
    source_id: UUID
    project_id: UUID
    revision: int
    relative_path: str
    title: str
    content_hash: str
    revision_hash: str
    byte_length: int
    links: tuple[str, ...]
