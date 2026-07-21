from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_mcp_server_tables(metadata: MetaData) -> tuple[Table, Table]:
    servers = Table(
        "core_mcp_servers",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("server_id", String(64), primary_key=True),
        Column("display_name", String(200), nullable=False),
        Column("transport", String(32), nullable=False),
        Column("command", Text),
        Column("arguments", JSON, nullable=False),
        Column("endpoint", Text),
        Column("credential_ref", String(288)),
        Column("environment_refs", JSON, nullable=False),
        Column("enabled", Boolean, nullable=False),
        Column("status", String(32), nullable=False),
        Column("accepted_schema_digest", String(64)),
        Column("pending_schema_digest", String(64)),
        Column("accepted_tools", JSON, nullable=False),
        Column("pending_tools", JSON, nullable=False),
        Column("policies", JSON, nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("last_error_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "server_id", name="pk_core_mcp_servers"),
        CheckConstraint(
            "transport IN ('stdio', 'streamable_http')",
            name="ck_core_mcp_servers_transport",
        ),
        CheckConstraint(
            "status IN ('disabled', 'untrusted', 'review_required', 'ready', 'unavailable')",
            name="ck_core_mcp_servers_status",
        ),
        CheckConstraint("revision >= 1", name="ck_core_mcp_servers_revision"),
        CheckConstraint(
            "(transport = 'stdio' AND command IS NOT NULL AND endpoint IS NULL) OR "
            "(transport = 'streamable_http' AND command IS NULL AND endpoint IS NOT NULL)",
            name="ck_core_mcp_servers_connection",
        ),
    )
    updates = Table(
        "core_mcp_server_updates",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True),
        Column("idempotency_key", String(512), primary_key=True),
        Column("request_fingerprint", String(64), nullable=False),
        Column("server_id", String(64), nullable=False),
        Column("result_record", JSON),
        Column("result_deleted", Boolean),
        Column("result_error_code", String(128)),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_mcp_server_updates",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_core_mcp_server_updates_fingerprint",
        ),
        CheckConstraint(
            "(result_record IS NULL AND result_deleted IS NULL AND result_error_code IS NULL) OR "
            "(result_record IS NOT NULL AND result_deleted = false AND result_error_code IS NULL) "
            "OR (result_record IS NULL AND result_deleted = true AND result_error_code IS NULL) "
            "OR (result_record IS NULL AND result_deleted = false "
            "AND result_error_code IS NOT NULL)",
            name="ck_core_mcp_server_updates_result",
        ),
    )
    return servers, updates


__all__ = ["build_mcp_server_tables"]
