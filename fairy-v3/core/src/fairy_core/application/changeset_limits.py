from __future__ import annotations

from collections.abc import Sequence

from fairy_core.contracts.models import FileMutation
from fairy_core.domain.models import Workspace
from fairy_core.workspace.models import ProjectIndex


def validate_changeset_limits(
    workspace: Workspace,
    current_index: ProjectIndex | None,
    mutations: Sequence[FileMutation],
) -> None:
    encoded_sizes = {mutation.path: len(mutation.content.encode("utf-8")) for mutation in mutations}
    if sum(encoded_sizes.values()) > 2 * 1024 * 1024:
        raise ValueError("Changeset batch exceeds the 2 MiB limit")
    indexed_sizes = {
        item.path: item.byte_length
        for item in (current_index.files if current_index is not None else ())
    }
    indexed_sizes.update(encoded_sizes)
    if len(indexed_sizes) > workspace.max_files:
        raise ValueError("Workspace file quota exceeded")
    if sum(indexed_sizes.values()) > workspace.max_bytes:
        raise ValueError("Workspace byte quota exceeded")


__all__ = ["validate_changeset_limits"]
