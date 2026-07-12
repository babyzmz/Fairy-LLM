from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, null, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.mcp.models import (
    McpConnection,
    McpServerRecord,
    McpServerStatus,
    McpToolDescriptor,
    McpToolPolicy,
    McpTransport,
)
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import mcp_server_updates, mcp_servers


@dataclass(frozen=True, slots=True)
class McpRequestReplay:
    server_id: str
    record: McpServerRecord | None
    deleted: bool | None
    error_code: str | None

    @property
    def pending(self) -> bool:
        return self.record is None and self.deleted is None and self.error_code is None


class McpServerRepository(Protocol):
    def get(self, server_id: str) -> McpServerRecord | None: ...

    def list(self) -> tuple[McpServerRecord, ...]: ...

    def save(self, record: McpServerRecord, *, expected_revision: int) -> None: ...

    def delete(self, server_id: str, *, expected_revision: int) -> None: ...

    def reserve_request(
        self,
        *,
        idempotency_key: str,
        fingerprint: str,
        server_id: str,
    ) -> McpRequestReplay | None: ...

    def complete_record(self, idempotency_key: str, record: McpServerRecord) -> None: ...

    def complete_delete(self, idempotency_key: str) -> None: ...

    def complete_error(self, idempotency_key: str, *, error_code: str) -> None: ...


class SqlAlchemyMcpServerRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)

    def get(self, server_id: str) -> McpServerRecord | None:
        row = (
            self._connection.execute(
                select(mcp_servers).where(
                    mcp_servers.c.tenant_id == self._tenant_id,
                    mcp_servers.c.server_id == server_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _record_from_row(row) if row is not None else None

    def list(self) -> tuple[McpServerRecord, ...]:
        rows = (
            self._connection.execute(
                select(mcp_servers)
                .where(mcp_servers.c.tenant_id == self._tenant_id)
                .order_by(mcp_servers.c.server_id)
            )
            .mappings()
            .all()
        )
        return tuple(_record_from_row(row) for row in rows)

    def save(self, record: McpServerRecord, *, expected_revision: int) -> None:
        values = _record_values(record)
        if expected_revision == 0:
            inserted = self._connection.execute(
                self._insert(mcp_servers)
                .values(tenant_id=self._tenant_id, **values)
                .on_conflict_do_nothing(
                    index_elements=[mcp_servers.c.tenant_id, mcp_servers.c.server_id]
                )
                .returning(mcp_servers.c.server_id)
            ).scalar_one_or_none()
            if inserted is not None:
                return
        else:
            result = self._connection.execute(
                update(mcp_servers)
                .where(
                    mcp_servers.c.tenant_id == self._tenant_id,
                    mcp_servers.c.server_id == record.connection.server_id,
                    mcp_servers.c.revision == expected_revision,
                )
                .values(**values)
            )
            if result.rowcount == 1:
                return
        raise VersionConflictError("MCP server revision changed")

    def delete(self, server_id: str, *, expected_revision: int) -> None:
        result = self._connection.execute(
            delete(mcp_servers).where(
                mcp_servers.c.tenant_id == self._tenant_id,
                mcp_servers.c.server_id == server_id,
                mcp_servers.c.revision == expected_revision,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("MCP server revision changed")

    def reserve_request(
        self,
        *,
        idempotency_key: str,
        fingerprint: str,
        server_id: str,
    ) -> McpRequestReplay | None:
        canonical_key = _idempotency_key(idempotency_key)
        reserved = self._connection.execute(
            self._insert(mcp_server_updates)
            .values(
                tenant_id=self._tenant_id,
                idempotency_key=canonical_key,
                request_fingerprint=fingerprint,
                server_id=server_id,
                result_record=null(),
                result_deleted=None,
                result_error_code=None,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    mcp_server_updates.c.tenant_id,
                    mcp_server_updates.c.idempotency_key,
                ]
            )
            .returning(mcp_server_updates.c.idempotency_key)
        ).scalar_one_or_none()
        if reserved is not None:
            return None
        row = self._request(canonical_key)
        if row is None:
            raise VersionConflictError("MCP request could not be reserved")
        if row["request_fingerprint"] != fingerprint:
            raise IdempotencyConflictError("MCP settings idempotency key was reused")
        return McpRequestReplay(
            server_id=str(row["server_id"]),
            record=(
                _record_from_json(row["result_record"])
                if row["result_record"] is not None
                else None
            ),
            deleted=(bool(row["result_deleted"]) if row["result_deleted"] is not None else None),
            error_code=(
                str(row["result_error_code"]) if row["result_error_code"] is not None else None
            ),
        )

    def complete_record(self, idempotency_key: str, record: McpServerRecord) -> None:
        self._complete_request(
            idempotency_key,
            result_record=_record_json(record),
            result_deleted=False,
            result_error_code=None,
        )

    def complete_delete(self, idempotency_key: str) -> None:
        self._complete_request(
            idempotency_key,
            result_record=None,
            result_deleted=True,
            result_error_code=None,
        )

    def complete_error(self, idempotency_key: str, *, error_code: str) -> None:
        self._complete_request(
            idempotency_key,
            result_record=None,
            result_deleted=False,
            result_error_code=error_code.strip()[:128] or "MCP_UNAVAILABLE",
        )

    def _complete_request(
        self,
        idempotency_key: str,
        *,
        result_record: dict[str, Any] | None,
        result_deleted: bool,
        result_error_code: str | None,
    ) -> None:
        changed = self._connection.execute(
            update(mcp_server_updates)
            .where(
                mcp_server_updates.c.tenant_id == self._tenant_id,
                mcp_server_updates.c.idempotency_key == _idempotency_key(idempotency_key),
                mcp_server_updates.c.result_record.is_(None),
                mcp_server_updates.c.result_deleted.is_(None),
                mcp_server_updates.c.result_error_code.is_(None),
            )
            .values(
                result_record=result_record if result_record is not None else null(),
                result_deleted=result_deleted,
                result_error_code=result_error_code,
            )
        )
        if changed.rowcount != 1:
            raise VersionConflictError("MCP request result was already completed")

    def _request(self, idempotency_key: str):
        return (
            self._connection.execute(
                select(mcp_server_updates).where(
                    mcp_server_updates.c.tenant_id == self._tenant_id,
                    mcp_server_updates.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )

    def _insert(self, table):
        return (
            postgresql_insert(table)
            if self._connection.dialect.name == "postgresql"
            else sqlite_insert(table)
        )


def _record_values(record: McpServerRecord) -> dict[str, Any]:
    connection = record.connection
    return {
        "server_id": connection.server_id,
        "display_name": connection.display_name,
        "transport": connection.transport.value,
        "command": connection.command,
        "arguments": list(connection.arguments),
        "endpoint": connection.endpoint,
        "credential_ref": connection.credential_ref,
        "environment_refs": dict(connection.environment_refs),
        "enabled": record.enabled,
        "status": record.status.value,
        "accepted_schema_digest": record.accepted_schema_digest,
        "pending_schema_digest": record.pending_schema_digest,
        "accepted_tools": [tool.as_dict() for tool in record.accepted_tools],
        "pending_tools": [tool.as_dict() for tool in record.pending_tools],
        "policies": [policy.as_dict() for policy in record.policies],
        "revision": record.revision,
        "last_error_code": record.last_error_code,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _record_json(record: McpServerRecord) -> dict[str, Any]:
    values = _record_values(record)
    values["created_at"] = record.created_at.isoformat()
    values["updated_at"] = record.updated_at.isoformat()
    return values


def _record_from_row(row: Mapping[str, Any]) -> McpServerRecord:
    return _record_from_values(row)


def _record_from_json(values: Mapping[str, Any]) -> McpServerRecord:
    return _record_from_values(values)


def _record_from_values(values: Mapping[str, Any]) -> McpServerRecord:
    connection = McpConnection(
        server_id=str(values["server_id"]),
        display_name=str(values["display_name"]),
        transport=McpTransport(str(values["transport"])),
        command=str(values["command"]) if values.get("command") is not None else None,
        arguments=tuple(str(value) for value in values["arguments"]),
        endpoint=str(values["endpoint"]) if values.get("endpoint") is not None else None,
        credential_ref=(
            str(values["credential_ref"]) if values.get("credential_ref") is not None else None
        ),
        environment_refs={
            str(key): str(value) for key, value in values["environment_refs"].items()
        },
    )
    return McpServerRecord(
        connection=connection,
        enabled=bool(values["enabled"]),
        status=McpServerStatus(str(values["status"])),
        accepted_schema_digest=(
            str(values["accepted_schema_digest"])
            if values.get("accepted_schema_digest") is not None
            else None
        ),
        pending_schema_digest=(
            str(values["pending_schema_digest"])
            if values.get("pending_schema_digest") is not None
            else None
        ),
        accepted_tools=tuple(
            McpToolDescriptor.from_dict(value) for value in values["accepted_tools"]
        ),
        pending_tools=tuple(
            McpToolDescriptor.from_dict(value) for value in values["pending_tools"]
        ),
        policies=tuple(McpToolPolicy.from_dict(value) for value in values["policies"]),
        revision=int(values["revision"]),
        last_error_code=(
            str(values["last_error_code"]) if values.get("last_error_code") is not None else None
        ),
        created_at=_aware(values["created_at"]),
        updated_at=_aware(values["updated_at"]),
    )


def _aware(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _idempotency_key(value: str) -> str:
    if value != value.strip() or not value or len(value) > 512:
        raise ValueError("MCP idempotency key is invalid")
    if any(ord(character) < 32 for character in value):
        raise ValueError("MCP idempotency key is invalid")
    return value


__all__ = [
    "McpRequestReplay",
    "McpServerRepository",
    "SqlAlchemyMcpServerRepository",
]
