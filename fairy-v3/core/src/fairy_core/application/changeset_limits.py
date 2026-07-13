from __future__ import annotations

from collections.abc import Sequence

from fairy_core.contracts.models import FileMutation, FileMutationOperation
from fairy_core.domain.models import Workspace
from fairy_core.workspace.models import ProjectIndex


def validate_changeset_limits(
    workspace: Workspace,
    current_index: ProjectIndex | None,
    mutations: Sequence[FileMutation],
) -> None:
    payload_bytes = sum(
        len(content) for mutation in mutations if (content := mutation.content_bytes()) is not None
    )
    if payload_bytes > 2 * 1024 * 1024:
        raise ValueError("Changeset batch exceeds the 2 MiB limit")
    indexed_sizes = {
        item.path: item.byte_length
        for item in (current_index.files if current_index is not None else ())
    }
    for mutation in mutations:
        if mutation.operation is FileMutationOperation.DELETE:
            indexed_sizes.pop(mutation.path, None)
            continue
        if mutation.operation is FileMutationOperation.RENAME:
            existing_size = indexed_sizes.pop(mutation.path, 0)
            assert mutation.destination_path is not None
            indexed_sizes[mutation.destination_path] = existing_size
            continue
        content = mutation.content_bytes()
        assert content is not None
        indexed_sizes[mutation.path] = len(content)
    if len(indexed_sizes) > workspace.max_files:
        raise ValueError("Workspace file quota exceeded")
    if sum(indexed_sizes.values()) > workspace.max_bytes:
        raise ValueError("Workspace byte quota exceeded")


__all__ = ["validate_changeset_limits"]
