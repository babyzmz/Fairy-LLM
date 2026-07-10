from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import TextClause

from fairy_core.domain.models import ScopeContract
from fairy_core.memory.models import MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySearchDocument,
    MemorySearchHit,
    MemorySourceKind,
)

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_DOCUMENT_COLUMNS = """
    d.id, d.source_kind, d.source_id, d.source_revision, d.namespace,
    d.project_id, d.conversation_id, d.task_id, d.version_id, d.language,
    d.normalized_text, d.content_hash, d.source_cursor,
    d.projection_generation, d.updated_at
"""


def query_tokens(query: str) -> tuple[str, ...]:
    if len(query) > 10_000:
        raise ValueError("search query cannot exceed 10,000 characters")
    normalized = query.strip().casefold()
    if not normalized:
        raise ValueError("search query is required")
    return tuple(token[:128] for token in _TOKEN_PATTERN.findall(normalized)[:32])


def validate_search_bounds(*, generation: int, limit: int) -> None:
    if generation < 1:
        raise ValueError("generation must be positive")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")


def build_postgres_search_statement(
    *,
    scope: ScopeContract,
    query: str,
    generation: int,
    limit: int,
    tenant_id: str,
) -> TextClause:
    tokens = query_tokens(query)
    validate_search_bounds(generation=generation, limit=limit)
    scope_sql, scope_params = _scope_sql(scope)
    statement = text(
        f"""
        SELECT {_DOCUMENT_COLUMNS},
               ts_rank_cd(
                   d.search_vector,
                   (
                       websearch_to_tsquery('simple', :query)
                       || to_tsquery('simple', :prefix_query)
                   )
               ) AS lexical_rank
        FROM memory_search_documents AS d
        WHERE d.tenant_id = :tenant_id
          AND d.projection_generation = :generation
          AND ({scope_sql})
          AND d.search_vector @@ (
              websearch_to_tsquery('simple', :query)
              || to_tsquery('simple', :prefix_query)
          )
        ORDER BY lexical_rank DESC, d.source_cursor DESC, d.source_id
        LIMIT :candidate_limit
        """
    )
    return statement.bindparams(
        tenant_id=tenant_id,
        generation=generation,
        query=" ".join(f'"{token}"' for token in tokens),
        prefix_query=" & ".join(f"{token}:*" for token in tokens),
        candidate_limit=min(max(limit * 4, limit), 400),
        **scope_params,
    )


def build_sqlite_search_statement(
    *,
    scope: ScopeContract,
    tokens: tuple[str, ...],
    generation: int,
    limit: int,
    tenant_id: str,
) -> TextClause:
    scope_sql, scope_params = _scope_sql(scope)
    statement = text(
        f"""
        SELECT {_DOCUMENT_COLUMNS},
               bm25(memory_search_documents_fts) AS lexical_rank
        FROM memory_search_documents_fts
        JOIN memory_search_documents AS d
          ON d.fts_rowid = memory_search_documents_fts.rowid
        WHERE memory_search_documents_fts MATCH :query
          AND d.tenant_id = :tenant_id
          AND d.projection_generation = :generation
          AND ({scope_sql})
        ORDER BY lexical_rank ASC, d.source_cursor DESC, d.source_id
        LIMIT :candidate_limit
        """
    )
    fts_query = " AND ".join(f'"{token}"*' for token in tokens)
    return statement.bindparams(
        tenant_id=tenant_id,
        generation=generation,
        query=fts_query,
        candidate_limit=min(max(limit * 4, limit), 400),
        **scope_params,
    )


def hit_from_row(
    row: RowMapping,
    *,
    normalized_query: str,
    dialect_name: str,
) -> MemorySearchHit:
    document = _document_from_row(row)
    raw_rank = float(row["lexical_rank"] or 0)
    if dialect_name == "sqlite":
        bounded_rank = min(max(raw_rank, -60), 60)
        lexical_score = 1.0 / (1.0 + math.exp(bounded_rank))
    else:
        nonnegative_rank = max(raw_rank, 0)
        lexical_score = nonnegative_rank / (1.0 + nonnegative_rank)
    return MemorySearchHit(
        document=document,
        lexical_score=lexical_score,
        exact_match=normalized_query in document.normalized_text.casefold(),
    )


def _scope_sql(scope: ScopeContract) -> tuple[str, dict[str, Any]]:
    read_scope = set(scope.memory_read_scope)
    clauses: list[str] = []
    params = {
        "project_id": str(scope.project_id) if scope.project_id else None,
        "conversation_id": str(scope.conversation_id),
        "task_id": str(scope.task_id),
    }
    if scope.project_id is not None and "project_canonical" in read_scope:
        clauses.append("(d.namespace = 'project_canonical' AND d.project_id = :project_id)")
    if read_scope & {
        "conversation_draft",
        "current_conversation",
        "current_conversation_draft",
    }:
        clauses.append(
            "(d.namespace = 'conversation_draft' AND d.conversation_id = :conversation_id)"
        )
    if "user_profile" in read_scope:
        clauses.append("d.namespace = 'user_profile'")
    if "task_episode" in read_scope:
        clauses.append("(d.namespace = 'task_episode' AND d.task_id = :task_id)")
    clauses.append(
        "(d.namespace IS NULL AND (d.conversation_id = :conversation_id OR d.task_id = :task_id))"
    )
    return " OR ".join(clauses), params


def _document_from_row(row: RowMapping) -> MemorySearchDocument:
    source_revision = int(row["source_revision"])
    return MemorySearchDocument(
        id=UUID(row["id"]),
        source_kind=MemorySourceKind(row["source_kind"]),
        source_id=UUID(row["source_id"]),
        source_revision=source_revision or None,
        namespace=MemoryNamespace(row["namespace"]) if row["namespace"] else None,
        project_id=_uuid(row["project_id"]),
        conversation_id=_uuid(row["conversation_id"]),
        task_id=_uuid(row["task_id"]),
        version_id=_uuid(row["version_id"]),
        language=row["language"],
        normalized_text=row["normalized_text"],
        content_hash=row["content_hash"],
        source_cursor=int(row["source_cursor"]),
        projection_generation=int(row["projection_generation"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _datetime(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = [
    "build_postgres_search_statement",
    "build_sqlite_search_statement",
    "hit_from_row",
    "query_tokens",
    "validate_search_bounds",
]
