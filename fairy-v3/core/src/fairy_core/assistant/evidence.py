from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import UUID

from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_LABEL = 240
_MAX_PATH = 1_024
_MAX_REVISION = 512


class EvidenceRequirementKind(StrEnum):
    WORKSPACE_STRUCTURE = "workspace_structure"
    WORKSPACE_CONTENT = "workspace_content"
    WEB_CURRENT = "web_current"
    RUNTIME_CURRENT = "runtime_current"
    PRIVATE_CURRENT = "private_current"


class EvidenceSourceKind(StrEnum):
    PROJECT_INDEX = "project_index"
    PROJECT_FILE = "project_file"
    TERMINAL_INSPECTION = "terminal_inspection"
    WEB_DOCUMENT = "web_document"
    STRUCTURED_INFORMATION = "structured_information"
    RUNTIME_SNAPSHOT = "runtime_snapshot"
    PRIVATE_SNAPSHOT = "private_snapshot"


class EvidenceClassificationFailedError(RuntimeError):
    error_code = "EVIDENCE_CLASSIFICATION_FAILED"


_WORKSPACE_SOURCES = frozenset(
    {
        EvidenceSourceKind.PROJECT_INDEX,
        EvidenceSourceKind.PROJECT_FILE,
        EvidenceSourceKind.TERMINAL_INSPECTION,
    }
)
_WEB_SOURCES = frozenset(
    {EvidenceSourceKind.WEB_DOCUMENT, EvidenceSourceKind.STRUCTURED_INFORMATION}
)


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    requirement_kind: EvidenceRequirementKind
    source_kind: EvidenceSourceKind
    public_label: str
    relative_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    safe_url: str | None = None
    content_hash: str | None = None
    source_revision: str | None = None
    document_snapshot_id: UUID | None = None
    document_snapshot_hash: str | None = None
    workspace_generation: int | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_kind", EvidenceRequirementKind(self.requirement_kind))
        object.__setattr__(self, "source_kind", EvidenceSourceKind(self.source_kind))
        object.__setattr__(self, "public_label", _label(self.public_label))
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path))
        object.__setattr__(self, "safe_url", _safe_url(self.safe_url))
        object.__setattr__(self, "content_hash", _optional_digest(self.content_hash))
        object.__setattr__(self, "source_revision", _revision(self.source_revision))
        object.__setattr__(
            self,
            "document_snapshot_hash",
            _optional_digest(self.document_snapshot_hash),
        )
        observed = _aware(self.observed_at, "observed_at")
        expires = _aware(self.expires_at, "expires_at") if self.expires_at is not None else None
        if expires is not None and expires <= observed:
            raise ValueError("evidence expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        _line_range(self.line_start, self.line_end)
        if (self.document_snapshot_id is None) != (self.document_snapshot_hash is None):
            raise ValueError("Document Snapshot identity and hash must be paired")
        if self.document_snapshot_id is not None and (
            self.source_kind is not EvidenceSourceKind.PRIVATE_SNAPSHOT
        ):
            raise ValueError("only private evidence can carry a Document Snapshot")
        if isinstance(self.workspace_generation, bool) or (
            self.workspace_generation is not None and self.workspace_generation < 1
        ):
            raise ValueError("workspace_generation must be positive")
        if self.relative_path is not None and self.source_kind not in _WORKSPACE_SOURCES:
            raise ValueError("only Workspace evidence can carry a relative path")
        if self.safe_url is not None and self.source_kind not in _WEB_SOURCES:
            raise ValueError("only public web evidence can carry a URL")
        if self.requirement_kind in {
            EvidenceRequirementKind.WORKSPACE_STRUCTURE,
            EvidenceRequirementKind.WORKSPACE_CONTENT,
        } and self.source_kind not in _WORKSPACE_SOURCES:
            raise ValueError("Workspace requirements require a Workspace source")
        if self.requirement_kind is EvidenceRequirementKind.WEB_CURRENT and (
            self.source_kind not in _WEB_SOURCES
        ):
            raise ValueError("web_current requires a public web source")
        if self.requirement_kind is EvidenceRequirementKind.RUNTIME_CURRENT and (
            self.source_kind is not EvidenceSourceKind.RUNTIME_SNAPSHOT
        ):
            raise ValueError("runtime_current requires a Runtime source")
        if self.requirement_kind is EvidenceRequirementKind.PRIVATE_CURRENT and (
            self.source_kind is not EvidenceSourceKind.PRIVATE_SNAPSHOT
        ):
            raise ValueError("private_current requires a private Snapshot source")


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    id: UUID
    tool_invocation_id: UUID
    turn_id: UUID
    task_id: UUID
    conversation_id: UUID
    requirement_kind: EvidenceRequirementKind
    source_kind: EvidenceSourceKind
    tool_name: str
    public_label: str
    scope_digest: str
    workspace_id: UUID
    project_id: UUID | None
    version_id: UUID | None
    workspace_generation: int | None
    memory_snapshot_id: UUID | None
    memory_snapshot_hash: str | None
    knowledge_snapshot_id: UUID | None
    knowledge_snapshot_hash: str | None
    document_snapshot_id: UUID | None
    document_snapshot_hash: str | None
    relative_path: str | None
    line_start: int | None
    line_end: int | None
    safe_url: str | None
    content_hash: str | None
    source_revision: str | None
    observed_at: datetime
    expires_at: datetime | None
    truncated: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_kind", EvidenceRequirementKind(self.requirement_kind))
        object.__setattr__(self, "source_kind", EvidenceSourceKind(self.source_kind))
        object.__setattr__(self, "tool_name", _required(self.tool_name, "tool_name", 255))
        object.__setattr__(self, "public_label", _label(self.public_label))
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path))
        object.__setattr__(self, "safe_url", _safe_url(self.safe_url))
        object.__setattr__(self, "content_hash", _optional_digest(self.content_hash))
        object.__setattr__(
            self,
            "memory_snapshot_hash",
            _optional_digest(self.memory_snapshot_hash),
        )
        object.__setattr__(
            self,
            "knowledge_snapshot_hash",
            _optional_digest(self.knowledge_snapshot_hash),
        )
        object.__setattr__(
            self,
            "document_snapshot_hash",
            _optional_digest(self.document_snapshot_hash),
        )
        object.__setattr__(self, "source_revision", _revision(self.source_revision))
        if _DIGEST.fullmatch(self.scope_digest) is None:
            raise ValueError("scope_digest must be a lowercase SHA-256 digest")
        _line_range(self.line_start, self.line_end)
        observed = _aware(self.observed_at, "observed_at")
        expires = _aware(self.expires_at, "expires_at") if self.expires_at is not None else None
        if expires is not None and expires <= observed:
            raise ValueError("evidence expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        for identifier, digest, name in (
            (self.memory_snapshot_id, self.memory_snapshot_hash, "Memory Snapshot"),
            (self.knowledge_snapshot_id, self.knowledge_snapshot_hash, "Knowledge Snapshot"),
            (self.document_snapshot_id, self.document_snapshot_hash, "Document Snapshot"),
        ):
            if (identifier is None) != (digest is None):
                raise ValueError(f"{name} identity and hash must be paired")
        if self.source_kind in _WORKSPACE_SOURCES and self.version_id is None:
            raise ValueError("Workspace evidence requires a Version")
        if self.source_kind is EvidenceSourceKind.PRIVATE_SNAPSHOT and not (
            self.memory_snapshot_id is not None
            or self.knowledge_snapshot_id is not None
            or self.document_snapshot_id is not None
        ):
            raise ValueError("private evidence requires a bound Snapshot")

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)


def seal_evidence_drafts(
    drafts: tuple[EvidenceDraft, ...],
    *,
    invocation_id: UUID,
    turn_id: UUID,
    tool_name: str,
    scope: ScopeContract,
) -> tuple[EvidenceReceipt, ...]:
    receipts: list[EvidenceReceipt] = []
    for draft in drafts:
        receipts.append(
            EvidenceReceipt(
                id=new_id(),
                tool_invocation_id=invocation_id,
                turn_id=turn_id,
                task_id=scope.task_id,
                conversation_id=scope.conversation_id,
                requirement_kind=draft.requirement_kind,
                source_kind=draft.source_kind,
                tool_name=tool_name,
                public_label=draft.public_label,
                scope_digest=scope.scope_digest,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                version_id=scope.target_version_id,
                workspace_generation=draft.workspace_generation,
                memory_snapshot_id=scope.memory_snapshot_id,
                memory_snapshot_hash=scope.memory_snapshot_hash,
                knowledge_snapshot_id=scope.knowledge_snapshot_id,
                knowledge_snapshot_hash=scope.knowledge_snapshot_hash,
                document_snapshot_id=draft.document_snapshot_id,
                document_snapshot_hash=draft.document_snapshot_hash,
                relative_path=draft.relative_path,
                line_start=draft.line_start,
                line_end=draft.line_end,
                safe_url=draft.safe_url,
                content_hash=draft.content_hash,
                source_revision=draft.source_revision,
                observed_at=draft.observed_at,
                expires_at=draft.expires_at,
                truncated=draft.truncated,
            )
        )
    identifiers = {receipt.id for receipt in receipts}
    if len(identifiers) != len(receipts):
        raise ValueError("evidence receipt ids must be unique")
    return tuple(receipts)


def evidence_receipt_record(receipt: EvidenceReceipt) -> dict[str, object]:
    return {
        "id": str(receipt.id),
        "tool_invocation_id": str(receipt.tool_invocation_id),
        "turn_id": str(receipt.turn_id),
        "task_id": str(receipt.task_id),
        "conversation_id": str(receipt.conversation_id),
        "requirement_kind": receipt.requirement_kind.value,
        "source_kind": receipt.source_kind.value,
        "tool_name": receipt.tool_name,
        "public_label": receipt.public_label,
        "scope_digest": receipt.scope_digest,
        "workspace_id": str(receipt.workspace_id),
        "project_id": str(receipt.project_id) if receipt.project_id is not None else None,
        "version_id": str(receipt.version_id) if receipt.version_id is not None else None,
        "workspace_generation": receipt.workspace_generation,
        "memory_snapshot_id": (
            str(receipt.memory_snapshot_id) if receipt.memory_snapshot_id is not None else None
        ),
        "memory_snapshot_hash": receipt.memory_snapshot_hash,
        "knowledge_snapshot_id": (
            str(receipt.knowledge_snapshot_id)
            if receipt.knowledge_snapshot_id is not None
            else None
        ),
        "knowledge_snapshot_hash": receipt.knowledge_snapshot_hash,
        "document_snapshot_id": (
            str(receipt.document_snapshot_id)
            if receipt.document_snapshot_id is not None
            else None
        ),
        "document_snapshot_hash": receipt.document_snapshot_hash,
        "relative_path": receipt.relative_path,
        "line_start": receipt.line_start,
        "line_end": receipt.line_end,
        "safe_url": receipt.safe_url,
        "content_hash": receipt.content_hash,
        "source_revision": receipt.source_revision,
        "observed_at": receipt.observed_at.isoformat(),
        "expires_at": receipt.expires_at.isoformat() if receipt.expires_at is not None else None,
        "truncated": receipt.truncated,
    }


def evidence_receipt_from_record(record: object) -> EvidenceReceipt:
    if not isinstance(record, dict):
        raise ValueError("stored Evidence Receipt is invalid")
    return EvidenceReceipt(
        id=UUID(str(record["id"])),
        tool_invocation_id=UUID(str(record["tool_invocation_id"])),
        turn_id=UUID(str(record["turn_id"])),
        task_id=UUID(str(record["task_id"])),
        conversation_id=UUID(str(record["conversation_id"])),
        requirement_kind=EvidenceRequirementKind(str(record["requirement_kind"])),
        source_kind=EvidenceSourceKind(str(record["source_kind"])),
        tool_name=str(record["tool_name"]),
        public_label=str(record["public_label"]),
        scope_digest=str(record["scope_digest"]),
        workspace_id=UUID(str(record["workspace_id"])),
        project_id=UUID(str(record["project_id"])) if record.get("project_id") else None,
        version_id=UUID(str(record["version_id"])) if record.get("version_id") else None,
        workspace_generation=(
            int(record["workspace_generation"])
            if record.get("workspace_generation") is not None
            else None
        ),
        memory_snapshot_id=(
            UUID(str(record["memory_snapshot_id"])) if record.get("memory_snapshot_id") else None
        ),
        memory_snapshot_hash=(
            str(record["memory_snapshot_hash"])
            if record.get("memory_snapshot_hash") is not None
            else None
        ),
        knowledge_snapshot_id=(
            UUID(str(record["knowledge_snapshot_id"]))
            if record.get("knowledge_snapshot_id")
            else None
        ),
        knowledge_snapshot_hash=(
            str(record["knowledge_snapshot_hash"])
            if record.get("knowledge_snapshot_hash") is not None
            else None
        ),
        document_snapshot_id=(
            UUID(str(record["document_snapshot_id"]))
            if record.get("document_snapshot_id")
            else None
        ),
        document_snapshot_hash=(
            str(record["document_snapshot_hash"])
            if record.get("document_snapshot_hash") is not None
            else None
        ),
        relative_path=(
            str(record["relative_path"]) if record.get("relative_path") is not None else None
        ),
        line_start=int(record["line_start"]) if record.get("line_start") is not None else None,
        line_end=int(record["line_end"]) if record.get("line_end") is not None else None,
        safe_url=str(record["safe_url"]) if record.get("safe_url") is not None else None,
        content_hash=(
            str(record["content_hash"]) if record.get("content_hash") is not None else None
        ),
        source_revision=(
            str(record["source_revision"])
            if record.get("source_revision") is not None
            else None
        ),
        observed_at=_parse_datetime(record["observed_at"]),
        expires_at=(
            _parse_datetime(record["expires_at"])
            if record.get("expires_at") is not None
            else None
        ),
        truncated=bool(record.get("truncated", False)),
    )


def evidence_context(receipts: tuple[EvidenceReceipt, ...]) -> str:
    if not receipts:
        return ""
    payload = [
        {
            "receipt_id": str(receipt.id),
            "requirement_kind": receipt.requirement_kind.value,
            "source_kind": receipt.source_kind.value,
            "label": receipt.public_label,
        }
        for receipt in receipts
    ]
    return (
        "\n[EVIDENCE_RECEIPTS]\n"
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n[/EVIDENCE_RECEIPTS]"
    )


def query_digest(values: object) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError as error:
        raise ValueError("evidence URL is invalid") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("evidence URL must be a credential-free HTTPS URL")
    host = parsed.hostname.encode("idna").decode("ascii").casefold()
    if parsed.port not in {None, 443}:
        host = f"{host}:{parsed.port}"
    sanitized = SplitResult("https", host, parsed.path or "/", "", "")
    result = urlunsplit(sanitized)
    if len(result) > 2_048:
        raise ValueError("evidence URL is too long")
    return result


def _relative_path(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > _MAX_PATH
        or path.is_absolute()
        or normalized.startswith("./")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("evidence path must be canonical and Workspace-relative")
    return path.as_posix()


def _line_range(start: int | None, end: int | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("evidence line range must be complete")
    if start is not None and (
        isinstance(start, bool)
        or isinstance(end, bool)
        or start < 1
        or end is None
        or end < start
    ):
        raise ValueError("evidence line range is invalid")


def _label(value: str) -> str:
    return _required(" ".join(value.split()), "public_label", _MAX_LABEL)


def _revision(value: str | None) -> str | None:
    if value is None:
        return None
    return _required(value, "source_revision", _MAX_REVISION)


def _optional_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if _DIGEST.fullmatch(value) is None:
        raise ValueError("evidence hash must be a lowercase SHA-256 digest")
    return value


def _required(value: str, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("stored evidence timestamp is invalid")
    parsed = datetime.fromisoformat(value)
    return _aware(parsed, "stored evidence timestamp")


__all__ = [
    "EvidenceClassificationFailedError",
    "EvidenceDraft",
    "EvidenceReceipt",
    "EvidenceRequirementKind",
    "EvidenceSourceKind",
    "evidence_context",
    "evidence_receipt_from_record",
    "evidence_receipt_record",
    "query_digest",
    "seal_evidence_drafts",
]
