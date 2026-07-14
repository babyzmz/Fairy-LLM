from __future__ import annotations

from uuid import uuid4

import pytest

from fairy_core.domain.errors import CommandRejectedError, IdempotencyConflictError
from fairy_core.presentation.collaboration import EditRecipe
from fairy_core.presentation.edit_exporters import TrustedEditExporter


def _recipe(
    operations: tuple[dict[str, object], ...],
    *,
    kind: str = "text_patch",
) -> EditRecipe:
    return EditRecipe(
        id=uuid4(),
        workspace_id=uuid4(),
        version_id=uuid4(),
        file_set_id=uuid4(),
        source_hash="a" * 64,
        kind=kind,
        operations=operations,
    )


def test_text_exporter_applies_insert_replace_and_delete_deterministically() -> None:
    exported = TrustedEditExporter().export(
        _recipe(
            (
                {"operation": "replace", "start": 0, "end": 5, "text": "Fairy"},
                {"operation": "delete", "start": 5, "end": 6},
                {"operation": "insert", "offset": 10, "text": "!"},
            )
        ),
        b"hello core",
    )

    assert exported.content == b"Fairycore!"
    assert len(exported.content_hash) == 64


@pytest.mark.parametrize(
    ("operations", "source", "code"),
    [
        (
            (
                {"operation": "replace", "start": 0, "end": 3, "text": "a"},
                {"operation": "delete", "start": 2, "end": 4},
            ),
            b"hello",
            "EDIT_NOT_EXPORTABLE",
        ),
        (
            (
                {
                    "operation": "replace",
                    "start": 0,
                    "end": 5,
                    "expected_text": "other",
                    "text": "Fairy",
                },
            ),
            b"hello",
            "SCOPE_MISMATCH",
        ),
        (
            ({"operation": "replace", "start": 0, "end": 1, "text": "x"},),
            b"\xff",
            "EDIT_NOT_EXPORTABLE",
        ),
    ],
)
def test_text_exporter_rejects_ambiguous_or_stale_edits(
    operations: tuple[dict[str, object], ...], source: bytes, code: str
) -> None:
    with pytest.raises(CommandRejectedError) as rejected:
        TrustedEditExporter().export(_recipe(operations), source)

    assert rejected.value.code == code


def test_exporter_rejects_untrusted_format() -> None:
    with pytest.raises(CommandRejectedError) as rejected:
        TrustedEditExporter().export(_recipe((), kind="spreadsheet_formula"), b"data")

    assert rejected.value.code == "EDIT_NOT_EXPORTABLE"


def test_edit_recipe_apply_state_is_recoverable_and_idempotent() -> None:
    recipe = _recipe(())
    applying = recipe.begin_apply(expected_revision=1, idempotency_key="recipe:1")

    assert applying.status.value == "applying"
    assert applying.begin_apply(expected_revision=1, idempotency_key="recipe:1") == applying
    with pytest.raises(IdempotencyConflictError):
        applying.begin_apply(expected_revision=1, idempotency_key="recipe:2")

    version_id = uuid4()
    applied = applying.finish_apply(applied_version_id=version_id, output_hash="b" * 64)

    assert applied.status.value == "applied"
    assert applied.applied_version_id == version_id
    assert applied.begin_apply(expected_revision=1, idempotency_key="recipe:1") == applied
    assert applied.discard() == applied
