from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from fairy_core.domain.ids import new_id
from fairy_core.research.models import (
    FetchedDocument,
    ResearchCapabilityHealth,
    ResearchCapabilityStatus,
    ResearchEvidence,
)


def _evidence_values() -> dict[str, object]:
    document = FetchedDocument.create(
        requested_url="https://example.com/source",
        final_url="https://example.com/canonical",
        redirect_chain=(
            "https://example.com/source",
            "https://example.com/canonical",
        ),
        media_type="text/plain",
        byte_length=8,
        content_hash="a" * 64,
        title="Evidence",
        text="Evidence",
        fetched_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    evidence = ResearchEvidence.create(
        artifact_id=new_id(),
        project_id=None,
        conversation_id=new_id(),
        task_id=new_id(),
        version_id=None,
        ordinal=1,
        document=document,
        excerpt="Evidence",
        created_at=datetime(2026, 7, 11, 1, tzinfo=UTC),
    )
    return {field.name: getattr(evidence, field.name) for field in fields(evidence)}


def test_degraded_health_retains_a_stable_diagnostic_error_code() -> None:
    health = ResearchCapabilityHealth.create(
        provider="brave",
        status=ResearchCapabilityStatus.DEGRADED,
        observed_at=datetime(2026, 7, 11, tzinfo=UTC),
        error_code="UPSTREAM_UNSTABLE",
    )

    assert health.error_code == "UPSTREAM_UNSTABLE"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (ResearchCapabilityStatus.AVAILABLE, "SHOULD_NOT_EXIST"),
        (ResearchCapabilityStatus.UNAVAILABLE, None),
    ],
)
def test_health_rejects_inconsistent_availability(
    status: ResearchCapabilityStatus,
    error_code: str | None,
) -> None:
    with pytest.raises(ValueError, match="health"):
        ResearchCapabilityHealth.create(
            provider="brave",
            status=status,
            observed_at=datetime(2026, 7, 11, tzinfo=UTC),
            error_code=error_code,
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ordinal", 0),
        ("source_url", "HTTPS://EXAMPLE.COM/source"),
        ("redirect_chain", ("https://example.com/other",)),
        ("media_type", "plain"),
        ("byte_length", -1),
        ("content_hash", "A" * 64),
        ("excerpt", ""),
        ("fetched_at", datetime(2026, 7, 11)),
    ],
)
def test_evidence_restore_rejects_invalid_database_values(name: str, value: object) -> None:
    values = _evidence_values()
    values[name] = value

    with pytest.raises(ValueError):
        ResearchEvidence.restore(**values)


def test_evidence_restore_accepts_canonical_database_values() -> None:
    values = _evidence_values()

    restored = ResearchEvidence.restore(**values)

    assert restored.content_hash == "a" * 64
    assert restored.canonical_url == "https://example.com/canonical"
