from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fairy_core.domain.ids import new_id
from fairy_core.domain.urls import canonical_http_url

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SearchKind(StrEnum):
    WEB = "web"
    NEWS = "news"


class ResearchArtifactKind(StrEnum):
    WEB_BRIEF = "web_brief"
    SPECS = "specs"
    COMPARE = "compare"
    RELEASE = "release"


class ResearchCapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ResearchCapabilityHealth:
    provider: str
    status: ResearchCapabilityStatus
    observed_at: datetime
    error_code: str | None
    diagnostics: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        status: ResearchCapabilityStatus,
        observed_at: datetime,
        error_code: str | None,
        diagnostics: tuple[str, ...] = (),
    ) -> ResearchCapabilityHealth:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        normalized_status = ResearchCapabilityStatus(status)
        normalized_error = _optional_text(error_code, maximum=128)
        if (normalized_status is ResearchCapabilityStatus.AVAILABLE) != (normalized_error is None):
            raise ValueError("non-available health requires exactly one error code")
        return cls(
            provider=_required_text(provider, "provider", maximum=128),
            status=normalized_status,
            observed_at=observed_at,
            error_code=normalized_error,
            diagnostics=tuple(
                _required_text(item, "diagnostic", maximum=512) for item in diagnostics
            ),
        )


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    kind: SearchKind
    count: int
    offset: int
    freshness: str | None

    @classmethod
    def create(
        cls,
        *,
        query: str,
        kind: SearchKind = SearchKind.WEB,
        count: int = 10,
        offset: int = 0,
        freshness: str | None = None,
    ) -> SearchRequest:
        normalized_query = _required_text(query, "query", maximum=2_000)
        if isinstance(count, bool) or not 1 <= count <= 20:
            raise ValueError("count must be between 1 and 20")
        if isinstance(offset, bool) or not 0 <= offset <= 200:
            raise ValueError("offset must be between 0 and 200")
        normalized_freshness = freshness.strip() if freshness is not None else None
        if normalized_freshness == "" or (
            normalized_freshness is not None and len(normalized_freshness) > 64
        ):
            raise ValueError("freshness is invalid")
        return cls(
            query=normalized_query,
            kind=SearchKind(kind),
            count=count,
            offset=offset,
            freshness=normalized_freshness,
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    provider: str
    rank: int
    title: str
    url: str
    description: str
    published_at: datetime | None
    language: str | None
    source: str | None

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        rank: int,
        title: str,
        url: str,
        description: str,
        published_at: datetime | None = None,
        language: str | None = None,
        source: str | None = None,
    ) -> SearchHit:
        if isinstance(rank, bool) or rank < 1:
            raise ValueError("rank must be positive")
        if published_at is not None and (
            published_at.tzinfo is None or published_at.utcoffset() is None
        ):
            raise ValueError("published_at must be timezone-aware")
        return cls(
            provider=_required_text(provider, "provider", maximum=128),
            rank=rank,
            title=_required_text(title, "title", maximum=1_000),
            url=canonical_http_url(url),
            description=_bounded_text(description, maximum=4_000),
            published_at=published_at,
            language=_optional_text(language, maximum=32),
            source=_optional_text(source, maximum=255),
        )


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    timeout_seconds: float
    max_text_characters: int
    cache_key: str

    @classmethod
    def create(
        cls,
        *,
        url: str,
        timeout_seconds: float = 15,
        max_text_characters: int = 200_000,
    ) -> FetchRequest:
        canonical = canonical_http_url(url)
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if isinstance(max_text_characters, bool) or not 1_000 <= max_text_characters <= 1_000_000:
            raise ValueError("max_text_characters must be between 1,000 and 1,000,000")
        cache_payload = json.dumps(
            {
                "max_text_characters": max_text_characters,
                "url": canonical,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return cls(
            url=canonical,
            timeout_seconds=float(timeout_seconds),
            max_text_characters=max_text_characters,
            cache_key=hashlib.sha256(cache_payload).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    media_type: str
    byte_length: int
    content_hash: str
    title: str
    text: str
    fetched_at: datetime
    truncated: bool

    @classmethod
    def create(
        cls,
        *,
        requested_url: str,
        final_url: str,
        redirect_chain: tuple[str, ...],
        media_type: str,
        byte_length: int,
        content_hash: str,
        title: str,
        text: str,
        fetched_at: datetime | None = None,
        truncated: bool = False,
    ) -> FetchedDocument:
        requested = canonical_http_url(requested_url)
        final = canonical_http_url(final_url)
        chain = tuple(canonical_http_url(item) for item in redirect_chain)
        if not chain or chain[0] != requested or chain[-1] != final:
            raise ValueError("redirect_chain must bind requested and final URLs")
        if isinstance(byte_length, bool) or byte_length < 0:
            raise ValueError("byte_length cannot be negative")
        if _SHA256.fullmatch(content_hash) is None:
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        at = fetched_at or datetime.now(UTC)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        normalized_media = _required_text(media_type, "media_type", maximum=255).lower()
        if "/" not in normalized_media:
            raise ValueError("media_type must be a type/subtype")
        return cls(
            requested_url=requested,
            final_url=final,
            redirect_chain=chain,
            media_type=normalized_media,
            byte_length=byte_length,
            content_hash=content_hash,
            title=_required_text(title, "title", maximum=1_000),
            text=_required_text(text, "text", maximum=1_000_000),
            fetched_at=at,
            truncated=bool(truncated),
        )


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    id: UUID
    artifact_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    ordinal: int
    source_url: str
    canonical_url: str
    redirect_chain: tuple[str, ...]
    title: str
    media_type: str
    byte_length: int
    content_hash: str
    excerpt: str
    fetched_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        required_ids = (
            self.id,
            self.artifact_id,
            self.conversation_id,
            self.task_id,
        )
        optional_ids = (self.project_id, self.version_id)
        if any(not isinstance(value, UUID) for value in required_ids) or any(
            value is not None and not isinstance(value, UUID) for value in optional_ids
        ):
            raise ValueError("evidence identifiers must be UUID values")
        if isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("evidence ordinal must be positive")
        source_url = canonical_http_url(self.source_url)
        canonical_url = canonical_http_url(self.canonical_url)
        if source_url != self.source_url or canonical_url != self.canonical_url:
            raise ValueError("evidence URLs must be canonical")
        if not isinstance(self.redirect_chain, tuple):
            raise ValueError("evidence redirect_chain must be an immutable tuple")
        redirect_chain = tuple(canonical_http_url(item) for item in self.redirect_chain)
        if (
            redirect_chain != self.redirect_chain
            or not redirect_chain
            or redirect_chain[0] != source_url
            or redirect_chain[-1] != canonical_url
        ):
            raise ValueError("evidence redirect_chain must bind source and canonical URLs")
        title = _required_text(self.title, "title", maximum=1_000)
        media_type = _required_text(self.media_type, "media_type", maximum=255).lower()
        excerpt = _required_text(self.excerpt, "excerpt", maximum=8_000)
        if title != self.title or media_type != self.media_type or excerpt != self.excerpt:
            raise ValueError("evidence text fields must be canonical")
        if "/" not in media_type:
            raise ValueError("evidence media_type must be a type/subtype")
        if isinstance(self.byte_length, bool) or self.byte_length < 0:
            raise ValueError("evidence byte_length cannot be negative")
        if _SHA256.fullmatch(self.content_hash) is None:
            raise ValueError("evidence content_hash must be a lowercase SHA-256 digest")
        for name, value in (("fetched_at", self.fetched_at), ("created_at", self.created_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"evidence {name} must be timezone-aware")

    @classmethod
    def create(
        cls,
        *,
        artifact_id: UUID,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID | None,
        ordinal: int,
        document: FetchedDocument,
        excerpt: str,
        created_at: datetime | None = None,
    ) -> ResearchEvidence:
        if isinstance(ordinal, bool) or ordinal < 1:
            raise ValueError("evidence ordinal must be positive")
        at = created_at or datetime.now(UTC)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return cls(
            id=new_id(),
            artifact_id=artifact_id,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            ordinal=ordinal,
            source_url=document.requested_url,
            canonical_url=document.final_url,
            redirect_chain=document.redirect_chain,
            title=document.title,
            media_type=document.media_type,
            byte_length=document.byte_length,
            content_hash=document.content_hash,
            excerpt=_required_text(excerpt, "excerpt", maximum=8_000),
            fetched_at=document.fetched_at,
            created_at=at,
        )

    @classmethod
    def restore(cls, **values) -> ResearchEvidence:
        return cls(**values)


def _required_text(value: str, name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    return normalized


def _bounded_text(value: str, *, maximum: int) -> str:
    normalized = value.strip()
    return normalized[:maximum]


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError("optional text is too long")
    return normalized


__all__ = [
    "FetchRequest",
    "FetchedDocument",
    "ResearchArtifactKind",
    "ResearchCapabilityHealth",
    "ResearchCapabilityStatus",
    "ResearchEvidence",
    "SearchHit",
    "SearchKind",
    "SearchRequest",
    "canonical_http_url",
]
