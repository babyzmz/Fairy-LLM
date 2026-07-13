from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from fairy_core.contracts.models import FileMutation, FileMutationOperation

_PREFIX = "fairy-file-mutation-v1:"


@dataclass(frozen=True, slots=True)
class WorkspaceFileMutation:
    operation: FileMutationOperation
    path: str
    destination_path: str | None
    content: bytes | None
    expected_hash: str | None
    expected_workspace_revision: int | None

    @property
    def affected_paths(self) -> tuple[str, ...]:
        if self.destination_path is None:
            return (self.path,)
        return (self.path, self.destination_path)

    def validate_current(self, current: bytes | None, *, destination_exists: bool) -> None:
        if self.operation is FileMutationOperation.CREATE:
            if current is not None:
                raise FileExistsError(self.path)
            return
        if self.operation is FileMutationOperation.UPSERT:
            if self.expected_hash is not None:
                _require_hash(self.path, current, self.expected_hash)
            return
        _require_hash(self.path, current, self.expected_hash)
        if self.operation is FileMutationOperation.RENAME and destination_exists:
            raise FileExistsError(self.destination_path)


def encode_mutation(mutation: FileMutation, *, expected_workspace_revision: int | None) -> str:
    if (
        mutation.operation is FileMutationOperation.UPSERT
        and mutation.content is not None
        and mutation.content_base64 is None
        and mutation.expected_hash is None
        and expected_workspace_revision is None
    ):
        return mutation.content
    content = mutation.content_bytes()
    payload = {
        "operation": mutation.operation.value,
        "destination_path": mutation.destination_path,
        "content_base64": (
            base64.b64encode(content).decode("ascii") if content is not None else None
        ),
        "expected_hash": mutation.expected_hash,
        "expected_workspace_revision": expected_workspace_revision,
    }
    return _PREFIX + json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_mutation(path: str, patch: str) -> WorkspaceFileMutation:
    if not patch.startswith(_PREFIX):
        return WorkspaceFileMutation(
            operation=FileMutationOperation.UPSERT,
            path=path,
            destination_path=None,
            content=patch.encode("utf-8"),
            expected_hash=None,
            expected_workspace_revision=None,
        )
    payload = json.loads(patch.removeprefix(_PREFIX))
    if not isinstance(payload, dict):
        raise ValueError("invalid encoded file mutation")
    encoded = payload.get("content_base64")
    content = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else None
    return WorkspaceFileMutation(
        operation=FileMutationOperation(str(payload["operation"])),
        path=path,
        destination_path=(
            str(payload["destination_path"])
            if payload.get("destination_path") is not None
            else None
        ),
        content=content,
        expected_hash=(
            str(payload["expected_hash"]) if payload.get("expected_hash") is not None else None
        ),
        expected_workspace_revision=(
            int(payload["expected_workspace_revision"])
            if payload.get("expected_workspace_revision") is not None
            else None
        ),
    )


def expected_workspace_revision(mutations: tuple[WorkspaceFileMutation, ...]) -> int | None:
    revisions = {
        mutation.expected_workspace_revision
        for mutation in mutations
        if mutation.expected_workspace_revision is not None
    }
    if len(revisions) > 1:
        raise ValueError("Changeset contains inconsistent Workspace revisions")
    return next(iter(revisions), None)


def _require_hash(path: str, current: bytes | None, expected: str | None) -> None:
    if current is None:
        raise FileNotFoundError(path)
    if expected is None or hashlib.sha256(current).hexdigest() != expected:
        raise ValueError(f"Workspace file hash conflict: {path}")


__all__ = [
    "WorkspaceFileMutation",
    "decode_mutation",
    "encode_mutation",
    "expected_workspace_revision",
]
