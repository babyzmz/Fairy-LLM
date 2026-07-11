from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.research.models import FetchedDocument, ResearchEvidence
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_research_evidence_is_tenant_scoped(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"research-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"research-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    try:
        application_a, factory_a = _stack(engine, tmp_path, tenant_a)
        _, factory_b = _stack(engine, tmp_path, tenant_b)
        conversation = application_a.create_conversation(
            project_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
        )
        task = application_a.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Research tenant isolation",
                operation_mode=OperationMode.ANSWER,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="research:rls:task",
            )
        ).task
        body = b"tenant evidence"
        artifact = Artifact.create(
            project_id=None,
            conversation_id=conversation.id,
            task_id=task.id,
            version_id=None,
            artifact_type=ArtifactType.REPORT,
            visibility=ArtifactVisibility.CONVERSATION,
            storage_location="inline://research/tenant.md",
            media_type="text/markdown",
            byte_length=len(body),
            content_hash=hashlib.sha256(body).hexdigest(),
            metadata={"report": body.decode()},
        )
        fetched = FetchedDocument.create(
            requested_url="https://example.com/source",
            final_url="https://example.com/source",
            redirect_chain=("https://example.com/source",),
            media_type="text/plain",
            byte_length=len(body),
            content_hash=hashlib.sha256(body).hexdigest(),
            title="Source",
            text=body.decode(),
            fetched_at=datetime(2026, 7, 11, tzinfo=UTC),
        )
        evidence = ResearchEvidence.create(
            artifact_id=artifact.id,
            project_id=None,
            conversation_id=conversation.id,
            task_id=task.id,
            version_id=None,
            ordinal=1,
            document=fetched,
            excerpt=fetched.text,
        )
        with factory_a() as unit_of_work:
            unit_of_work.state.append_artifact(artifact)
            unit_of_work.state.append_research_evidence(evidence)
            unit_of_work.commit()

        with factory_b() as unit_of_work:
            assert unit_of_work.state.research_evidence_for_artifact(artifact.id) == []
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            assert (
                connection.execute(
                    text("SELECT id FROM core_research_evidence WHERE id = :evidence_id"),
                    {"evidence_id": str(evidence.id)},
                ).scalar_one_or_none()
                is None
            )
            transaction.rollback()
    finally:
        engine.dispose()


def _stack(engine, root: Path, tenant_id: str):
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    application = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(root / tenant_id),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    return application, factory
