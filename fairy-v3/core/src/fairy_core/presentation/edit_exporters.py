from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from fairy_core.domain.errors import CommandRejectedError
from fairy_core.presentation.collaboration import EditRecipe

MAX_EDITABLE_TEXT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExportedEdit:
    content: bytes
    content_hash: str


class TrustedEditExporter:
    def export(self, recipe: EditRecipe, source: bytes) -> ExportedEdit:
        if recipe.kind != "text_patch":
            raise CommandRejectedError(
                f"No trusted exporter is available for Edit Recipe kind {recipe.kind}",
                code="EDIT_NOT_EXPORTABLE",
            )
        return _export_text_patch(recipe.operations, source)


def _export_text_patch(operations: tuple[dict[str, Any], ...], source: bytes) -> ExportedEdit:
    if len(source) > MAX_EDITABLE_TEXT_BYTES:
        raise CommandRejectedError("Editable text exceeds 2 MiB", code="FILE_TOO_LARGE")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CommandRejectedError(
            "Text Edit Recipes require valid UTF-8 source content",
            code="EDIT_NOT_EXPORTABLE",
        ) from error
    edits: list[tuple[int, int, str]] = []
    for operation in operations:
        name = operation.get("operation")
        if name == "insert":
            start = end = _integer(operation, "offset")
            replacement = _text(operation, "text")
        elif name == "replace":
            start = _integer(operation, "start")
            end = _integer(operation, "end")
            replacement = _text(operation, "text")
        elif name == "delete":
            start = _integer(operation, "start")
            end = _integer(operation, "end")
            replacement = ""
        else:
            raise CommandRejectedError(
                "Text Edit Recipe contains an unsupported operation",
                code="EDIT_NOT_EXPORTABLE",
            )
        if start < 0 or end < start or end > len(text):
            raise CommandRejectedError("Text Edit Recipe range is invalid", code="SCOPE_MISMATCH")
        expected = operation.get("expected_text")
        if expected is not None and (not isinstance(expected, str) or text[start:end] != expected):
            raise CommandRejectedError(
                "Text Edit Recipe source no longer matches",
                code="SCOPE_MISMATCH",
            )
        edits.append((start, end, replacement))
    edits.sort(key=lambda item: (item[0], item[1]))
    for previous, current in pairwise(edits):
        if current[0] < previous[1] or (current[0] == previous[0] and current[1] == previous[1]):
            raise CommandRejectedError(
                "Text Edit Recipe ranges overlap",
                code="EDIT_NOT_EXPORTABLE",
            )
    output = text
    for start, end, replacement in reversed(edits):
        output = output[:start] + replacement + output[end:]
    encoded = output.encode("utf-8")
    if len(encoded) > MAX_EDITABLE_TEXT_BYTES:
        raise CommandRejectedError("Edited text exceeds 2 MiB", code="FILE_TOO_LARGE")
    return ExportedEdit(content=encoded, content_hash=hashlib.sha256(encoded).hexdigest())


def _integer(operation: dict[str, Any], key: str) -> int:
    value = operation.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandRejectedError(
            "Text Edit Recipe offset is invalid",
            code="EDIT_NOT_EXPORTABLE",
        )
    return value


def _text(operation: dict[str, Any], key: str) -> str:
    value = operation.get(key)
    if not isinstance(value, str):
        raise CommandRejectedError(
            "Text Edit Recipe replacement is invalid",
            code="EDIT_NOT_EXPORTABLE",
        )
    return value


__all__ = ["ExportedEdit", "TrustedEditExporter"]
