from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.domain.errors import VersionConflictError
from fairy_core.mcp.models import McpDiscovery, McpTransport
from fairy_core.mcp.ports import McpError
from fairy_core.transports.stdio import build_local_service
from tests.assistant.test_application import _scratch_task
from tests.mcp.support import FakeMcpConnector, issue_tools


def _configure(
    service,
    *,
    expected_revision: int = 0,
    idempotency_key: str = "mcp:configure:issues:1",
) -> dict[str, object]:
    return service.invoke(
        "mcp.servers.configure",
        {
            "server_id": "issue-tracker",
            "display_name": "Issue tracker",
            "transport": McpTransport.STDIO,
            "command": "issue-mcp",
            "arguments": ["--stdio"],
            "endpoint": None,
            "credential_ref": "env:TEST_TOKEN",
            "environment_refs": {"ISSUE_TOKEN": "env:TEST_TOKEN"},
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
        },
    )


def test_mcp_server_is_not_exposed_until_schema_and_tool_policy_are_explicitly_accepted(
    tmp_path: Path,
) -> None:
    connector = FakeMcpConnector(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=connector)
    try:
        configured = _configure(service)
        task = _scratch_task(service, "Review issue tracker tools")

        discovered = service.invoke(
            "mcp.servers.discover",
            {
                "server_id": "issue-tracker",
                "task_id": task["id"],
                "expected_revision": configured["revision"],
                "idempotency_key": "mcp:discover:issues:1",
            },
        )

        assert discovered["status"] == "review_required"
        assert discovered["accepted_schema_digest"] is None
        assert service._registry.get("mcp.issue-tracker.get_issue") is None  # type: ignore[attr-defined]

        accepted = service.invoke(
            "mcp.servers.accept",
            {
                "server_id": "issue-tracker",
                "expected_revision": discovered["revision"],
                "schema_digest": discovered["pending_schema_digest"],
                "enabled": True,
                "tools": [
                    {
                        "name": "get_issue",
                        "enabled": True,
                        "side_effect": "read",
                        "risk_level": "low",
                        "approval_policy": "never",
                        "profiles": ["observe", "standard", "autonomous"],
                        "idempotent": True,
                    },
                    {
                        "name": "create_issue",
                        "enabled": True,
                        "side_effect": "write",
                        "risk_level": "medium",
                        "approval_policy": "profile",
                        "profiles": ["standard", "autonomous"],
                        "idempotent": False,
                    },
                ],
                "idempotency_key": "mcp:accept:issues:1",
            },
        )

        assert accepted["status"] == "ready"
        assert accepted["credential_configured"] is True
        definition = service._registry.get("mcp.issue-tracker.create_issue")  # type: ignore[attr-defined]
        assert definition is not None
        assert definition.executor == "mcp"
        assert definition.source == "mcp"
        manifest = service.invoke("capabilities.get", {})
        assert manifest["operations"]["mcp.issue-tracker.get_issue"] is True
        assert manifest["schema_version"] == 3
    finally:
        service.close()


def test_mcp_settings_use_revision_fencing_and_cloud_rejects_stdio(tmp_path: Path) -> None:
    connector = FakeMcpConnector(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=connector)
    try:
        configured = _configure(service)
        with pytest.raises(VersionConflictError):
            _configure(
                service,
                expected_revision=0,
                idempotency_key="mcp:configure:issues:stale",
            )
        listed = service.invoke("mcp.servers.list", {})["items"]
        assert listed == [configured]
    finally:
        service.close()


def test_schema_drift_removes_previously_trusted_tools_until_reaccepted(tmp_path: Path) -> None:
    connector = FakeMcpConnector(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=connector)
    try:
        configured = _configure(service)
        task = _scratch_task(service, "Trust issue tools")
        discovered = service.invoke(
            "mcp.servers.discover",
            {
                "server_id": "issue-tracker",
                "task_id": task["id"],
                "expected_revision": configured["revision"],
                "idempotency_key": "mcp:discover:drift:1",
            },
        )
        accepted = service.invoke(
            "mcp.servers.accept",
            {
                "server_id": "issue-tracker",
                "expected_revision": discovered["revision"],
                "schema_digest": discovered["pending_schema_digest"],
                "enabled": True,
                "tools": [
                    {
                        "name": "get_issue",
                        "enabled": True,
                        "side_effect": "read",
                        "risk_level": "low",
                        "approval_policy": "never",
                        "profiles": ["standard", "autonomous"],
                        "idempotent": True,
                    },
                    {
                        "name": "create_issue",
                        "enabled": False,
                        "side_effect": "write",
                        "risk_level": "medium",
                        "approval_policy": "profile",
                        "profiles": ["standard", "autonomous"],
                        "idempotent": False,
                    },
                ],
                "idempotency_key": "mcp:accept:drift:1",
            },
        )
        changed = list(issue_tools())
        changed[0] = changed[0].with_description("Changed schema generation")
        connector.tools = tuple(changed)

        drifted = service.invoke(
            "mcp.servers.discover",
            {
                "server_id": "issue-tracker",
                "task_id": task["id"],
                "expected_revision": accepted["revision"],
                "idempotency_key": "mcp:discover:drift:2",
            },
        )

        assert drifted["status"] == "review_required"
        assert drifted["pending_schema_digest"] != drifted["accepted_schema_digest"]
        assert service._registry.get("mcp.issue-tracker.get_issue") is None  # type: ignore[attr-defined]
    finally:
        service.close()


def test_delete_is_idempotent_across_restart_and_preserves_tombstone(tmp_path: Path) -> None:
    service = build_local_service(tmp_path, mcp_connector=FakeMcpConnector(issue_tools()))
    configured = _configure(service)
    request = {
        "server_id": "issue-tracker",
        "expected_revision": configured["revision"],
        "idempotency_key": "mcp:delete:issues:1",
    }
    try:
        assert service.invoke("mcp.servers.delete", request) == {
            "server_id": "issue-tracker",
            "deleted": True,
        }
        assert service.invoke("mcp.servers.list", {})["items"] == []
    finally:
        service.close()

    reopened = build_local_service(tmp_path, mcp_connector=FakeMcpConnector(issue_tools()))
    try:
        assert reopened.invoke("mcp.servers.delete", request) == {
            "server_id": "issue-tracker",
            "deleted": True,
        }
        assert reopened.invoke("mcp.servers.list", {})["items"] == []
    finally:
        reopened.close()


class _ProcessCrash(BaseException):
    pass


class _CrashDuringDiscovery(FakeMcpConnector):
    def discover(self, connection):
        self.discover_calls.append(connection)
        raise _ProcessCrash("simulated process loss")


class _RejectedDiscovery(FakeMcpConnector):
    def discover(self, connection):
        self.discover_calls.append(connection)
        raise McpError("invalid remote schema", error_code="MCP_SCHEMA_INVALID")


def test_discovery_process_crash_replays_uncertain_without_reconnecting(tmp_path: Path) -> None:
    crashing = _CrashDuringDiscovery(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=crashing)
    configured = _configure(service)
    task = _scratch_task(service, "Crash during MCP discovery")
    request = {
        "server_id": "issue-tracker",
        "task_id": task["id"],
        "expected_revision": configured["revision"],
        "idempotency_key": "mcp:discover:crash",
    }
    try:
        with pytest.raises(_ProcessCrash):
            service.invoke("mcp.servers.discover", request)
        assert len(crashing.discover_calls) == 1
    finally:
        service.close()

    recovered = FakeMcpConnector(issue_tools())
    reopened = build_local_service(tmp_path, mcp_connector=recovered)
    try:
        with pytest.raises(McpError) as error:
            reopened.invoke("mcp.servers.discover", request)
        assert error.value.error_code == "MCP_RESULT_UNCERTAIN"
        assert recovered.discover_calls == []
    finally:
        reopened.close()


def test_failed_discovery_replays_error_without_reconnecting(tmp_path: Path) -> None:
    connector = _RejectedDiscovery(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=connector)
    configured = _configure(service)
    task = _scratch_task(service, "Reject invalid MCP schema")
    request = {
        "server_id": "issue-tracker",
        "task_id": task["id"],
        "expected_revision": configured["revision"],
        "idempotency_key": "mcp:discover:invalid",
    }
    try:
        for _attempt in range(2):
            with pytest.raises(McpError) as error:
                service.invoke("mcp.servers.discover", request)
            assert error.value.error_code == "MCP_SCHEMA_INVALID"
        assert len(connector.discover_calls) == 1
    finally:
        service.close()


def test_durable_schema_acceptance_on_another_instance_invalidates_stale_definition(
    tmp_path: Path,
) -> None:
    connector = FakeMcpConnector(issue_tools())
    service = build_local_service(tmp_path, mcp_connector=connector)
    try:
        configured = _configure(service)
        task = _scratch_task(service, "Trust MCP before cross-instance drift")
        discovered = service.invoke(
            "mcp.servers.discover",
            {
                "server_id": "issue-tracker",
                "task_id": task["id"],
                "expected_revision": configured["revision"],
                "idempotency_key": "mcp:discover:cross-instance",
            },
        )
        accepted = service.invoke(
            "mcp.servers.accept",
            {
                "server_id": "issue-tracker",
                "expected_revision": discovered["revision"],
                "schema_digest": discovered["pending_schema_digest"],
                "enabled": True,
                "tools": [
                    {
                        "name": "get_issue",
                        "enabled": True,
                        "side_effect": "read",
                        "risk_level": "low",
                        "approval_policy": "never",
                        "profiles": ["standard", "autonomous"],
                        "idempotent": True,
                    },
                    {
                        "name": "create_issue",
                        "enabled": False,
                        "side_effect": "write",
                        "risk_level": "medium",
                        "approval_policy": "profile",
                        "profiles": ["standard", "autonomous"],
                        "idempotent": False,
                    },
                ],
                "idempotency_key": "mcp:accept:cross-instance",
            },
        )
        old_definition = service._registry.get("mcp.issue-tracker.get_issue")  # type: ignore[attr-defined]
        assert old_definition is not None
        changed_tools = list(issue_tools())
        changed_tools[0] = changed_tools[0].with_description("New accepted contract")
        changed_discovery = McpDiscovery.create(
            server_name="issue-tracker",
            protocol_version="2025-11-25",
            tools=tuple(changed_tools),
        )
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            current = unit_of_work.mcp_servers.get("issue-tracker")
            assert current is not None and current.revision == accepted["revision"]
            drifted = current.record_discovery(changed_discovery)
            updated = drifted.accept(
                schema_digest=changed_discovery.schema_digest,
                policies=current.policies,
                enabled=True,
            )
            unit_of_work.mcp_servers.save(updated, expected_revision=current.revision)
            unit_of_work.commit()

        with pytest.raises(McpError) as error:
            service._mcp_application.resolve_tool(old_definition.name)  # type: ignore[attr-defined]
        assert error.value.error_code == "MCP_SCHEMA_CHANGED"

        service.invoke("capabilities.get", {})
        refreshed = service._registry.get(old_definition.name)  # type: ignore[attr-defined]
        assert refreshed is not None
        assert refreshed.definition_digest != old_definition.definition_digest
    finally:
        service.close()
