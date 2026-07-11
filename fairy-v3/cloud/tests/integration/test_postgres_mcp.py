from __future__ import annotations

from uuid import uuid4

import pytest
from fairy_core.mcp.models import McpConnection, McpServerRecord, McpTransport
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from sqlalchemy import create_engine

pytestmark = pytest.mark.integration


def test_postgres_mcp_trust_is_tenant_scoped_and_delete_replay_survives(
    postgres_test_context,
) -> None:
    tenant_a = postgres_test_context.track_tenant(f"mcp-a-{uuid4().hex}")
    tenant_b = postgres_test_context.track_tenant(f"mcp-b-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory_a = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_a)
    factory_b = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_b)
    connection = McpConnection(
        server_id="docs",
        display_name="Docs",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.example.test/mcp",
        credential_ref="vault:docs",
    )
    configure_key = "postgres:mcp:configure:docs"
    delete_key = "postgres:mcp:delete:docs"
    try:
        with factory_a() as unit_of_work:
            assert (
                unit_of_work.mcp_servers.reserve_request(
                    idempotency_key=configure_key,
                    fingerprint="a" * 64,
                    server_id="docs",
                )
                is None
            )
            record = McpServerRecord.create(connection)
            unit_of_work.mcp_servers.save(record, expected_revision=0)
            unit_of_work.mcp_servers.complete_record(configure_key, record)
            unit_of_work.commit()

        with factory_b() as unit_of_work:
            assert unit_of_work.mcp_servers.list() == ()
            assert (
                unit_of_work.mcp_servers.reserve_request(
                    idempotency_key=configure_key,
                    fingerprint="b" * 64,
                    server_id="docs",
                )
                is None
            )
            tenant_b_record = McpServerRecord.create(connection)
            unit_of_work.mcp_servers.save(tenant_b_record, expected_revision=0)
            unit_of_work.mcp_servers.complete_record(configure_key, tenant_b_record)
            unit_of_work.commit()

        with factory_a() as unit_of_work:
            assert (
                unit_of_work.mcp_servers.reserve_request(
                    idempotency_key=delete_key,
                    fingerprint="c" * 64,
                    server_id="docs",
                )
                is None
            )
            unit_of_work.mcp_servers.delete("docs", expected_revision=record.revision)
            unit_of_work.mcp_servers.complete_delete(delete_key)
            unit_of_work.commit()

        with factory_a() as unit_of_work:
            replay = unit_of_work.mcp_servers.reserve_request(
                idempotency_key=delete_key,
                fingerprint="c" * 64,
                server_id="docs",
            )
            assert replay is not None and replay.deleted is True
            assert unit_of_work.mcp_servers.list() == ()

        with factory_b() as unit_of_work:
            assert [item.connection.server_id for item in unit_of_work.mcp_servers.list()] == [
                "docs"
            ]
    finally:
        engine.dispose()
