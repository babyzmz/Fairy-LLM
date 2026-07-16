from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.media.models import MediaGenerationJob, MediaGenerationKind
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_media_work_is_single_claim_fenced_and_tenant_scoped(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"media-work-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"media-work-b-{uuid4().hex}")
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
                user_request="Generate one durable image",
                operation_mode=OperationMode.CREATE_NEW_VERSION,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="media:work:task",
            )
        ).task
        scope = application_a.scope_for_task(task.id)
        assert scope.workspace_id is not None
        assert scope.target_version_id is not None
        job = MediaGenerationJob.create(
            project_id=scope.project_id,
            workspace_id=scope.workspace_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id,
            turn_id=None,
            command_run_id=uuid4(),
            scope_digest=scope.scope_digest,
            kind=MediaGenerationKind.IMAGE,
            model_id="google/gemini-3.1-flash-lite-image",
            endpoint_kind=ModelEndpointKind.IMAGES,
            output_path="generated/postgres-media.png",
            request_spec={"prompt": "A durable image"},
            request_fingerprint="a" * 64,
            idempotency_key="media:work:image",
        )
        with factory_a() as unit_of_work:
            unit_of_work.state.save_media_job(job)
            unit_of_work.state.enqueue_media_work(job.id)
            unit_of_work.commit()

        with factory_b() as unit_of_work:
            assert unit_of_work.state.pending_media_work_ids() == ()
            assert (
                unit_of_work.state.claim_next_media_work(
                    worker_id="tenant-b-worker",
                    lease_until=datetime.now(UTC) + timedelta(seconds=30),
                )
                is None
            )

        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait(timeout=5)
            with factory_a() as unit_of_work:
                result = unit_of_work.state.claim_next_media_work(
                    worker_id=worker_id,
                    lease_until=datetime.now(UTC) + timedelta(seconds=30),
                )
                unit_of_work.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(claim, ("media-worker-a", "media-worker-b")))
        winners = tuple(claim for claim in claims if claim is not None)
        assert len(winners) == 1
        first_claim = winners[0]

        with factory_a() as unit_of_work:
            assert unit_of_work.state.abandon_media_work(first_claim)
            unit_of_work.commit()
        with factory_a() as unit_of_work:
            second_claim = unit_of_work.state.claim_next_media_work(
                worker_id="media-worker-c",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            unit_of_work.commit()
        assert second_claim is not None
        assert second_claim.lease_fence > first_claim.lease_fence
        with factory_a() as unit_of_work:
            assert not unit_of_work.state.renew_media_work(
                first_claim,
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
            assert unit_of_work.state.settle_media_work(
                second_claim,
                available_at=None,
                error_code=None,
            )
            unit_of_work.commit()

        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            visible = connection.execute(
                text(
                    """
                    SELECT job_id
                    FROM core_media_generation_work
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": str(job.id)},
            ).scalar_one_or_none()
            transaction.rollback()
        assert visible is None
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
