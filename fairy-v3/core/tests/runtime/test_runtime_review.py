from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

from fairy_core.application.runtime import PreviewStartRequest
from fairy_core.application.runtime_review import RuntimeReviewApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.runtime.review import (
    BrowserCapture,
    RuntimeHealthCheck,
    RuntimeReviewExecutorHealth,
    RuntimeReviewRequest,
    StoredRuntimeEvidence,
)
from tests.runtime_support import build_runtime_stack


class FakeReviewer:
    def __init__(self) -> None:
        self.requests: list[RuntimeReviewRequest] = []

    def health(self) -> RuntimeReviewExecutorHealth:
        return RuntimeReviewExecutorHealth(
            executor="fixture_browser",
            version="1",
            http_available=True,
            browser_available=True,
            diagnostics=(),
        )

    def check(self, request: RuntimeReviewRequest) -> RuntimeHealthCheck:
        self.requests.append(request)
        return RuntimeHealthCheck(
            status_code=200,
            latency_ms=12,
            content_type="text/html; charset=utf-8",
            body_sha256="a" * 64,
            body_bytes=42,
        )

    def capture(self, request: RuntimeReviewRequest) -> BrowserCapture:
        self.requests.append(request)
        return BrowserCapture(
            png=b"\x89PNG\r\n\x1a\nfixture",
            width=1280,
            height=720,
            device_scale_factor=1.0,
        )


class FakeEvidenceStore:
    def __init__(self) -> None:
        self.contents: list[bytes] = []

    def put(self, *, content: bytes, media_type: str) -> StoredRuntimeEvidence:
        assert media_type == "image/png"
        self.contents.append(content)
        return StoredRuntimeEvidence.create(
            storage_location=f"memory://runtime-evidence/{len(self.contents)}",
            content=content,
        )


def test_runtime_health_and_browser_evidence_join_current_checkpoint(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    preview = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="runtime-review:preview",
        )
    )
    reviewer = FakeReviewer()
    evidence_store = FakeEvidenceStore()
    registry = build_default_registry()
    application = RuntimeReviewApplication(
        unit_of_work_factory=stack.factory,
        reviewer=reviewer,
        evidence_store=evidence_store,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=stack.core.scope_for_task,
    )

    evidence = application.review_task(stack.task.task.id)
    checkpoint = stack.core.review_task(stack.task.task.id)

    assert [artifact.artifact_type for artifact in evidence] == [
        ArtifactType.REPORT,
        ArtifactType.SCREENSHOT,
    ]
    assert checkpoint.evidence_artifact_ids == tuple(artifact.id for artifact in evidence)
    assert checkpoint.preview_artifact_id is not None
    assert set(checkpoint.command_run_ids) == {
        UUID(str(artifact.metadata["command_run_id"])) for artifact in evidence
    }
    assert len(reviewer.requests) == 2
    assert all(request.preview_id == preview.preview.id for request in reviewer.requests)
    assert all(
        request.preview_manifest_id == checkpoint.preview_artifact_id
        for request in reviewer.requests
    )
    assert evidence_store.contents == [b"\x89PNG\r\n\x1a\nfixture"]


def test_checkpoint_ignores_runtime_evidence_from_an_old_generation(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="runtime-review:stale-preview",
        )
    )
    reviewer = FakeReviewer()
    registry = build_default_registry()
    application = RuntimeReviewApplication(
        unit_of_work_factory=stack.factory,
        reviewer=reviewer,
        evidence_store=FakeEvidenceStore(),
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=stack.core.scope_for_task,
    )
    stale = application.review_task(stack.task.task.id)
    with stack.factory() as unit_of_work:
        index = unit_of_work.project_indexes.get(stack.task.task.target_version_id)
        assert index is not None
        unit_of_work.project_indexes.replace_generation(
            replace(index, generation=index.generation + 1),
            expected_generation=index.generation,
        )
        unit_of_work.commit()

    checkpoint = stack.core.review_task(stack.task.task.id)

    assert not ({artifact.id for artifact in stale} & set(checkpoint.evidence_artifact_ids))


def test_checkpoint_ignores_runtime_evidence_from_an_old_preview_manifest(
    tmp_path: Path,
) -> None:
    stack = build_runtime_stack(tmp_path)
    stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key="runtime-review:old-manifest",
        )
    )
    reviewer = FakeReviewer()
    registry = build_default_registry()
    application = RuntimeReviewApplication(
        unit_of_work_factory=stack.factory,
        reviewer=reviewer,
        evidence_store=FakeEvidenceStore(),
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=stack.core.scope_for_task,
    )
    stale = application.review_task(stack.task.task.id)
    with stack.factory() as unit_of_work:
        index = unit_of_work.project_indexes.get(stack.task.task.target_version_id)
        assert index is not None
        manifest = next(
            artifact
            for artifact in reversed(unit_of_work.state.artifacts_for_task(stack.task.task.id))
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
        )
        replacement = Artifact.create(
            project_id=manifest.project_id,
            conversation_id=manifest.conversation_id,
            task_id=manifest.task_id,
            version_id=manifest.version_id,
            artifact_type=ArtifactType.PREVIEW_MANIFEST,
            visibility=ArtifactVisibility.CONVERSATION,
            storage_location=f"previews/{manifest.id}/replacement.json",
            media_type="application/json",
            byte_length=manifest.byte_length,
            content_hash="c" * 64,
            metadata=dict(manifest.metadata),
        )
        unit_of_work.state.append_artifact(replacement)
        unit_of_work.commit()

    checkpoint = stack.core.review_task(stack.task.task.id)

    assert not ({artifact.id for artifact in stale} & set(checkpoint.evidence_artifact_ids))
