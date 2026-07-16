from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from fairy_core.mcp.models import McpTransport
from fairy_core.mcp.ports import McpCancelledError, McpTransportInterrupted
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.mcp.support import FakeMcpConnector, issue_tools


def _trusted_service(tmp_path: Path, provider: ScriptedProvider, connector: FakeMcpConnector):
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        mcp_connector=connector,
    )
    configured = service.invoke(
        "mcp.servers.configure",
        {
            "server_id": "issue-tracker",
            "display_name": "Issue tracker",
            "transport": McpTransport.STDIO,
            "command": "issue-mcp",
            "arguments": [],
            "endpoint": None,
            "credential_ref": None,
            "environment_refs": {},
            "expected_revision": 0,
            "idempotency_key": "mcp:tool:configure",
        },
    )
    task = _scratch_task(service, "Use issue tracker")
    discovered = service.invoke(
        "mcp.servers.discover",
        {
            "server_id": "issue-tracker",
            "task_id": task["id"],
            "expected_revision": configured["revision"],
            "idempotency_key": "mcp:tool:discover",
        },
    )
    service.invoke(
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
            "idempotency_key": "mcp:tool:accept",
        },
    )
    return service, task


def test_mcp_tool_call_rejects_reserved_fields_uses_core_scope_and_creates_artifact(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="mcp-1",
                    tool_name="mcp.issue-tracker.get_issue",
                    arguments_fragment=(
                        '{"issue_id":"FAIRY-42","task_id":"forged",'
                        '"endpoint":"https://evil.test","credential":"stolen"}'
                    ),
                ),
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=2,
                    tool_call_id="mcp-2",
                    tool_name="mcp.issue-tracker.get_issue",
                    arguments_fragment='{"issue_id":"FAIRY-42"}',
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=3,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Issue is open."),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            ),
        ]
    )
    connector = FakeMcpConnector(issue_tools())
    service, task = _trusted_service(tmp_path, provider, connector)
    try:
        turn = _turn(service, task, "mcp:turn:scope")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(connector.calls) == 1
        _connection, name, arguments, context = connector.calls[0]
        assert name == "get_issue"
        assert arguments == {"issue_id": "FAIRY-42"}
        assert str(context.task_id) == task["id"]
        assert context.scope_digest == completed["scope_digest"]
        rejected_messages = [
            message.content
            for message in provider.requests[1].messages
            if message.role.value == "tool" and message.tool_call_id == "mcp-1"
        ]
        assert len(rejected_messages) == 1
        assert "reserved" in rejected_messages[0].lower()
        artifacts = service.invoke("artifacts.list", {"task_id": task["id"]})["items"]
        mcp_artifacts = [item for item in artifacts if item["metadata"].get("source") == "mcp"]
        assert len(mcp_artifacts) == 1, artifacts
        assert mcp_artifacts[0]["metadata"]["server_id"] == "issue-tracker"
        assert mcp_artifacts[0]["metadata"]["tool_name"] == "get_issue"
    finally:
        service.close()


def test_idempotent_read_reconnects_once_but_uncertain_write_never_replays(
    tmp_path: Path,
) -> None:
    read_provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="mcp-read",
                    tool_name="mcp.issue-tracker.get_issue",
                    arguments_fragment='{"issue_id":"FAIRY-42"}',
                ),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Recovered."),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            ),
        ]
    )
    connector = FakeMcpConnector(issue_tools())
    connector.failures.append(McpTransportInterrupted("disconnect", response_started=False))
    service, task = _trusted_service(tmp_path / "read", read_provider, connector)
    try:
        turn = _turn(service, task, "mcp:turn:retry-read")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert completed["status"] == "completed"
        assert len(connector.calls) == 2
    finally:
        service.close()

    write_provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="mcp-write",
                    tool_name="mcp.issue-tracker.create_issue",
                    arguments_fragment='{"title":"Do not duplicate"}',
                ),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="The write result is uncertain and was not replayed.",
                ),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            ),
        ]
    )
    connector = FakeMcpConnector(issue_tools())
    connector.failures.append(McpTransportInterrupted("disconnect", response_started=True))
    service, task = _trusted_service(tmp_path / "write", write_provider, connector)
    try:
        permissions = service.invoke("permissions.get", {})
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": permissions["revision"],
                "idempotency_key": "mcp:permissions:autonomous",
            },
        )
        turn = _turn(service, task, "mcp:turn:no-replay-write")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert completed["status"] == "completed"
        assert len(connector.calls) == 1
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocation = unit_of_work.assistant.list_tool_invocations(turn["id"])[0]
        assert invocation.error_code == "MCP_RESULT_UNCERTAIN"
    finally:
        service.close()


def test_standard_write_waits_for_generic_approval_before_mcp_call(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="mcp-approval",
                    tool_name="mcp.issue-tracker.create_issue",
                    arguments_fragment='{"title":"Approved issue"}',
                ),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Created."),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            ),
        ]
    )
    connector = FakeMcpConnector(issue_tools())
    service, task = _trusted_service(tmp_path, provider, connector)
    try:
        turn = _turn(service, task, "mcp:turn:approval")
        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert waiting["status"] == "waiting_for_tool"
        assert connector.calls == []
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        service.invoke(
            "approvals.decide",
            {"approval_id": approval["id"], "approved": True},
        )
        completed = wait_for_turn(service, turn["id"])
        assert completed["status"] == "completed"
        assert len(connector.calls) == 1
    finally:
        service.close()


class _BlockingMcpConnector(FakeMcpConnector):
    def __init__(self) -> None:
        super().__init__(issue_tools())
        self.started = Event()
        self.cancelled_call = Event()

    def call_tool(self, connection, tool_name, arguments, context):
        self.calls.append((connection, tool_name, arguments, context))
        self.started.set()
        if not self.cancelled_call.wait(timeout=10):
            raise TimeoutError("MCP cancellation was not delivered")
        raise McpCancelledError("cancelled by Core")

    def cancel(self, command_run_id) -> None:
        self.cancelled.append(command_run_id)
        self.cancelled_call.set()


def test_mcp_cancellation_reaches_connector_and_cancels_durable_run(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="mcp-cancel",
                    tool_name="mcp.issue-tracker.get_issue",
                    arguments_fragment='{"issue_id":"FAIRY-42"}',
                ),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
            )
        ]
    )
    connector = _BlockingMcpConnector()
    service, task = _trusted_service(tmp_path, provider, connector)
    try:
        turn = _turn(service, task, "mcp:turn:cancel")
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(
                service.invoke,
                "assistant.turns.run",
                {"turn_id": turn["id"]},
            )
            assert connector.started.wait(timeout=10)
            cancelled = service.invoke(
                "assistant.turns.cancel",
                {
                    "turn_id": turn["id"],
                    "expected_cancellation_revision": turn["cancellation_revision"],
                },
            )
            completed = running.result(timeout=10)

        assert cancelled["status"] == "cancelled"
        assert completed["status"] == "cancelled"
        assert len(connector.cancelled) == 1
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocation = unit_of_work.assistant.list_tool_invocations(turn["id"])[0]
            command = unit_of_work.commands.get_run(invocation.command_run_id)
        assert invocation.status.value == "cancelled"
        assert command is not None and command.status.value == "cancelled"
    finally:
        service.close()
