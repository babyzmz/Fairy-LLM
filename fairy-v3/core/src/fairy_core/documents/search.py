from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.documents.models import (
    DocumentChunk,
    DocumentRevision,
    DocumentSearchHit,
    DocumentStatus,
    DocumentVisibility,
    ExtractedDocument,
    ManagedDocument,
)
from fairy_core.documents.sqlite_fts import document_sqlite_fts_available
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_SELECT_COLUMNS = """
    d.id AS d_id,
    d.project_id AS d_project_id,
    d.conversation_id AS d_conversation_id,
    d.source_task_id AS d_source_task_id,
    d.version_id AS d_version_id,
    d.filename AS d_filename,
    d.media_type AS d_media_type,
    d.byte_length AS d_byte_length,
    d.content_hash AS d_content_hash,
    d.storage_location AS d_storage_location,
    d.current_revision AS d_current_revision,
    d.visibility AS d_visibility,
    d.status AS d_status,
    d.idempotency_key AS d_idempotency_key,
    d.created_at AS d_created_at,
    d.updated_at AS d_updated_at,
    r.document_id AS r_document_id,
    r.revision AS r_revision,
    r.content_hash AS r_content_hash,
    r.byte_length AS r_byte_length,
    r.media_type AS r_media_type,
    r.parser AS r_parser,
    r.parser_version AS r_parser_version,
    r.section_count AS r_section_count,
    r.chunk_count AS r_chunk_count,
    r.created_at AS r_created_at,
    c.id AS c_id,
    c.document_id AS c_document_id,
    c.revision AS c_revision,
    c.revision_hash AS c_revision_hash,
    c.ordinal AS c_ordinal,
    c.section_ordinal AS c_section_ordinal,
    c.locator AS c_locator,
    c.normalized_text AS c_normalized_text,
    c.content_hash AS c_content_hash,
    c.token_count AS c_token_count,
    c.updated_at AS c_updated_at
"""


class DocumentProjectionStaleError(RuntimeError):
    error_code = "DOCUMENT_PROJECTION_STALE"


def chunk_extracted_document(
    document: ManagedDocument,
    extracted: ExtractedDocument,
    *,
    max_characters: int = 1_600,
    overlap_characters: int = 200,
) -> tuple[DocumentChunk, ...]:
    if not 200 <= max_characters <= 20_000:
        raise ValueError("document chunk size must be between 200 and 20,000 characters")
    if not 0 <= overlap_characters < max_characters // 2:
        raise ValueError("document chunk overlap is invalid")
    chunks: list[DocumentChunk] = []
    for section in extracted.sections:
        for chunk_index, (start, end, value) in enumerate(
            _text_windows(
                section.text,
                max_characters=max_characters,
                overlap_characters=overlap_characters,
            )
        ):
            locator = dict(section.locator)
            locator.update({"chunk": chunk_index, "start": start, "end": end})
            chunks.append(
                DocumentChunk.create(
                    document=document,
                    ordinal=len(chunks),
                    section_ordinal=section.ordinal,
                    locator=locator,
                    text=value,
                )
            )
    if not chunks or len(chunks) > 100_000:
        raise ValueError("document must produce between 1 and 100,000 chunks")
    return tuple(chunks)


class SqlAlchemyDocumentSearchIndex:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        if bind.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported document search dialect: {bind.dialect.name}")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def search(
        self,
        *,
        scope: ScopeContract,
        query: str,
        limit: int,
    ) -> tuple[DocumentSearchHit, ...]:
        tokens = _query_tokens(query)
        if not 1 <= limit <= 50:
            raise ValueError("document search limit must be between 1 and 50")
        if not tokens:
            return ()
        scope_sql, scope_parameters = _scope_sql(scope)
        with self._session.read() as connection:
            if connection.dialect.name == "sqlite":
                if not document_sqlite_fts_available(connection):
                    raise DocumentProjectionStaleError("SQLite document FTS is unavailable")
                statement = text(
                    f"""
                    SELECT {_SELECT_COLUMNS},
                           bm25(core_document_chunks_fts) AS lexical_rank
                    FROM core_document_chunks_fts
                    JOIN core_document_chunks AS c
                      ON c.fts_rowid = core_document_chunks_fts.rowid
                    JOIN core_documents AS d
                      ON d.tenant_id = c.tenant_id AND d.id = c.document_id
                    JOIN core_document_revisions AS r
                      ON r.tenant_id = d.tenant_id
                     AND r.document_id = d.id
                     AND r.revision = d.current_revision
                     AND r.content_hash = d.content_hash
                    WHERE core_document_chunks_fts MATCH :query
                      AND d.tenant_id = :tenant_id
                      AND d.status = 'active'
                      AND c.revision = d.current_revision
                      AND c.revision_hash = d.content_hash
                      AND ({scope_sql})
                    ORDER BY lexical_rank ASC, d.created_at DESC, c.ordinal, c.id
                    LIMIT :candidate_limit
                    """
                ).bindparams(
                    tenant_id=self._tenant_id,
                    query=" AND ".join(f'"{token}"*' for token in tokens),
                    candidate_limit=min(limit * 4, 200),
                    **scope_parameters,
                )
                dialect = "sqlite"
            else:
                statement = text(
                    f"""
                    SELECT {_SELECT_COLUMNS},
                           ts_rank_cd(
                               c.search_vector,
                               websearch_to_tsquery('simple', :query)
                           ) AS lexical_rank
                    FROM core_document_chunks AS c
                    JOIN core_documents AS d
                      ON d.tenant_id = c.tenant_id AND d.id = c.document_id
                    JOIN core_document_revisions AS r
                      ON r.tenant_id = d.tenant_id
                     AND r.document_id = d.id
                     AND r.revision = d.current_revision
                     AND r.content_hash = d.content_hash
                    WHERE d.tenant_id = :tenant_id
                      AND d.status = 'active'
                      AND c.revision = d.current_revision
                      AND c.revision_hash = d.content_hash
                      AND c.search_vector @@ websearch_to_tsquery('simple', :query)
                      AND ({scope_sql})
                    ORDER BY lexical_rank DESC, d.created_at DESC, c.ordinal, c.id
                    LIMIT :candidate_limit
                    """
                ).bindparams(
                    tenant_id=self._tenant_id,
                    query=" ".join(f'"{token}"' for token in tokens),
                    candidate_limit=min(limit * 4, 200),
                    **scope_parameters,
                )
                dialect = "postgresql"
            rows = connection.execute(statement).mappings().all()
        normalized_query = " ".join(tokens)
        hits = [
            _hit_from_row(
                row,
                normalized_query=normalized_query,
                dialect_name=dialect,
            )
            for row in rows
        ]
        hits.sort(
            key=lambda hit: (
                not hit.exact_match,
                -hit.lexical_score,
                -hit.document.created_at.timestamp(),
                hit.chunk.ordinal,
                str(hit.chunk.id),
            )
        )
        return tuple(hits[:limit])


def _text_windows(
    value: str,
    *,
    max_characters: int,
    overlap_characters: int,
):
    start = 0
    length = len(value)
    while start < length:
        end = min(length, start + max_characters)
        if end < length:
            lower_bound = start + max_characters // 2
            boundary = max(
                value.rfind("\n", lower_bound, end),
                value.rfind(" ", lower_bound, end),
            )
            if boundary > start:
                end = boundary
        raw = value[start:end]
        left_trimmed = len(raw) - len(raw.lstrip())
        right_trimmed = len(raw.rstrip())
        actual_start = start + left_trimmed
        actual_end = start + right_trimmed
        text_value = raw.strip()
        if text_value:
            yield actual_start, actual_end, text_value
        if end >= length:
            break
        next_start = max(start + 1, end - overlap_characters)
        while next_start < length and value[next_start].isspace():
            next_start += 1
        start = next_start


def _query_tokens(query: str) -> tuple[str, ...]:
    normalized = query.strip().casefold()
    if not normalized:
        raise ValueError("document search query is required")
    if len(query) > 10_000:
        raise ValueError("document search query cannot exceed 10,000 characters")
    return tuple(token[:128] for token in _TOKEN_PATTERN.findall(normalized)[:32])


def _scope_sql(scope: ScopeContract) -> tuple[str, dict[str, str | None]]:
    clauses = ["(d.visibility = 'conversation' AND d.conversation_id = :conversation_id)"]
    parameters: dict[str, str | None] = {
        "conversation_id": str(scope.conversation_id),
    }
    if scope.project_id is not None:
        clauses.append("(d.visibility = 'project' AND d.project_id = :project_id)")
        parameters["project_id"] = str(scope.project_id)
    return " OR ".join(clauses), parameters


def _hit_from_row(
    row: RowMapping,
    *,
    normalized_query: str,
    dialect_name: str,
) -> DocumentSearchHit:
    document = ManagedDocument.restore(
        id=UUID(row["d_id"]),
        project_id=_uuid(row["d_project_id"]),
        conversation_id=UUID(row["d_conversation_id"]),
        source_task_id=UUID(row["d_source_task_id"]),
        version_id=_uuid(row["d_version_id"]),
        filename=row["d_filename"],
        media_type=row["d_media_type"],
        byte_length=int(row["d_byte_length"]),
        content_hash=row["d_content_hash"],
        storage_location=row["d_storage_location"],
        current_revision=int(row["d_current_revision"]),
        visibility=DocumentVisibility(row["d_visibility"]),
        status=DocumentStatus(row["d_status"]),
        idempotency_key=row["d_idempotency_key"],
        created_at=_datetime(row["d_created_at"]),
        updated_at=_datetime(row["d_updated_at"]),
    )
    revision = DocumentRevision.restore(
        document_id=UUID(row["r_document_id"]),
        revision=int(row["r_revision"]),
        content_hash=row["r_content_hash"],
        byte_length=int(row["r_byte_length"]),
        media_type=row["r_media_type"],
        parser=row["r_parser"],
        parser_version=row["r_parser_version"],
        section_count=int(row["r_section_count"]),
        chunk_count=int(row["r_chunk_count"]),
        created_at=_datetime(row["r_created_at"]),
    )
    locator = row["c_locator"]
    if isinstance(locator, str):
        locator = json.loads(locator)
    chunk = DocumentChunk.restore(
        id=UUID(row["c_id"]),
        document_id=UUID(row["c_document_id"]),
        revision=int(row["c_revision"]),
        revision_hash=row["c_revision_hash"],
        ordinal=int(row["c_ordinal"]),
        section_ordinal=int(row["c_section_ordinal"]),
        locator=locator,
        text=row["c_normalized_text"],
        content_hash=row["c_content_hash"],
        token_count=int(row["c_token_count"]),
        updated_at=_datetime(row["c_updated_at"]),
    )
    raw_rank = float(row["lexical_rank"] or 0)
    if dialect_name == "sqlite":
        score = 1.0 / (1.0 + math.exp(min(max(raw_rank, -60), 60)))
    else:
        rank = max(raw_rank, 0)
        score = rank / (1.0 + rank)
    return DocumentSearchHit(
        document=document,
        revision=revision,
        chunk=chunk,
        lexical_score=score,
        exact_match=normalized_query in chunk.text.casefold(),
    )


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = [
    "DocumentProjectionStaleError",
    "SqlAlchemyDocumentSearchIndex",
    "chunk_extracted_document",
]
