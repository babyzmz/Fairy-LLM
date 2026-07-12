from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.models import DynamicRuntimeStart
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.runtime.repository import (
    AsyncCloudRuntimeRepository,
    CloudPreviewRouteRepository,
    CloudRuntimeRepository,
)
from fairy_cloud.runtime.tokens import CloudPreviewBinding, CloudPreviewSigner

pytestmark = pytest.mark.integration


def test_runtime_route_is_idempotent_tenant_bound_and_not_an_internal_endpoint(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"runtime-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    request = _request(tmp_path)
    repository = CloudRuntimeRepository(engine, tenant_id=tenant_id)
    signer = CloudPreviewSigner(b"k" * 32)
    try:
        first = repository.enqueue(request)
        replay = repository.enqueue(request)
        assert replay.request_fingerprint == first.request_fingerprint
        assert (
            CloudRuntimeRepository(engine, tenant_id="other-tenant").get(request.runtime_id) is None
        )

        with create_engine(postgres_test_context.admin_sync_dsn).begin() as connection:
            connection.execute(
                text(
                    "UPDATE runtime_leases SET status='running', "
                    "internal_url=:url, worker_id='runtime-worker-1', lease_fence=1 "
                    "WHERE tenant_id=:tenant AND runtime_id=:runtime"
                ),
                {
                    "url": f"http://runtime:8082/internal/{request.runtime_id}/",
                    "tenant": tenant_id,
                    "runtime": str(request.runtime_id),
                },
            )
        lease = repository.get(request.runtime_id)
        assert lease is not None
        binding = CloudPreviewBinding(
            tenant_id=tenant_id,
            runtime_id=request.runtime_id,
            preview_id=request.preview_id,
            task_id=request.task_id,
            version_id=request.version_id,
            lease_fence=request.lease_fence,
            expires_at=lease.expires_at,
        )
        token = signer.issue(binding)
        repository.register_route(
            binding,
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        )
        assert CloudPreviewRouteRepository(engine, signer=signer).resolve(token) == (
            f"http://runtime:8082/internal/{request.runtime_id}/"
        )
    finally:
        engine.dispose()


def test_interrupted_runtime_retry_atomically_requires_a_higher_core_fence(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"runtime-retry-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    request = _request(tmp_path)
    repository = CloudRuntimeRepository(engine, tenant_id=tenant_id)
    try:
        repository.enqueue(request)
        with create_engine(postgres_test_context.admin_sync_dsn).begin() as connection:
            connection.execute(
                text(
                    "UPDATE runtime_leases SET status='interrupted', "
                    "error_code='WORKER_INTERRUPTED' "
                    "WHERE tenant_id=:tenant AND runtime_id=:runtime"
                ),
                {"tenant": tenant_id, "runtime": str(request.runtime_id)},
            )

        retry = replace(request, lease_fence=3)
        queued = repository.enqueue(retry)

        assert queued.request_lease_fence == 3
        assert queued.status.value == "queued"
        assert queued.error_code is None
        with pytest.raises(IdempotencyConflictError):
            repository.enqueue(request)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_runtime_worker_claim_is_skip_locked_and_fenced(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    runtime_dsn = os.environ.get("FAIRY_TEST_RUNTIME_POSTGRES_DSN")
    if runtime_dsn is None:
        pytest.skip("FAIRY_TEST_RUNTIME_POSTGRES_DSN is not set")
    tenant_id = postgres_test_context.track_tenant(f"runtime-claim-{uuid4().hex}")
    app_engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    admin_engine = create_engine(postgres_test_context.admin_sync_dsn, pool_pre_ping=True)
    request = _request(tmp_path)
    CloudRuntimeRepository(app_engine, tenant_id=tenant_id).enqueue(request)
    engine = create_async_engine(runtime_dsn, pool_pre_ping=True)
    try:
        stores = (
            AsyncCloudRuntimeRepository(
                engine,
                attestation_digest="a" * 64,
                gateway_base_url="http://runtime:8082",
            ),
            AsyncCloudRuntimeRepository(
                engine,
                attestation_digest="b" * 64,
                gateway_base_url="http://runtime:8082",
            ),
        )
        claims = await asyncio.gather(
            stores[0].claim_next(owner_id="runtime-a", lease_seconds=30),
            stores[1].claim_next(owner_id="runtime-b", lease_seconds=30),
        )
        local_claims = [claim for claim in claims if claim is not None]
        assert len(local_claims) <= 1
        with admin_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, attempts, lease_fence FROM runtime_leases "
                    "WHERE tenant_id=:tenant AND runtime_id=:runtime"
                ),
                {"tenant": tenant_id, "runtime": str(request.runtime_id)},
            ).one()
        assert row.attempts == 1
        assert row.lease_fence == 1
        if local_claims:
            assert local_claims[0].lease.lease_fence == 1
        else:
            # The live Compose worker is a third legitimate claimant.
            assert row.status != "queued"
    finally:
        await engine.dispose()
        admin_engine.dispose()
        app_engine.dispose()


def test_runtime_database_role_cannot_read_domain_or_execution_tables() -> None:
    dsn = os.environ.get("FAIRY_TEST_RUNTIME_SYNC_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("FAIRY_TEST_RUNTIME_SYNC_POSTGRES_DSN is not set")
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            privileges = connection.execute(
                text(
                    "SELECT "
                    "has_table_privilege(current_user, 'public.runtime_leases', 'SELECT'), "
                    "has_table_privilege(current_user, 'public.runtime_workers', 'UPDATE'), "
                    "has_table_privilege(current_user, 'public.execution_jobs', 'SELECT'), "
                    "has_table_privilege(current_user, 'public.core_projects', 'SELECT')"
                )
            ).one()
        assert tuple(privileges) == (True, True, False, False)
        for forbidden in ("execution_jobs", "core_projects"):
            with pytest.raises(ProgrammingError), engine.connect() as connection:
                connection.execute(text(f"SELECT * FROM {forbidden} LIMIT 1"))
    finally:
        engine.dispose()


def _request(tmp_path: Path) -> DynamicRuntimeStart:
    archive = b"PK\x03\x04runtime-integration"
    return DynamicRuntimeStart(
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        project_root=tmp_path,
        execution_target="cloud",
        kind=RuntimeKind.CLOUD_OCI,
        adapter="vite",
        scope_digest="a" * 64,
        workspace_generation=1,
        lease_fence=1,
        argv=(
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
            "--strictPort",
        ),
        cwd=".",
        readiness_path="/",
        startup_timeout_seconds=45,
        dependency_key="b" * 64,
        workspace_archive=archive,
        archive_sha256=hashlib.sha256(archive).hexdigest(),
    )
