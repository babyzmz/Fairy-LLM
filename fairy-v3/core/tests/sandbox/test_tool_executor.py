from __future__ import annotations

import io
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import SandboxRequest, SandboxResult, SandboxResultStatus
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider


class RecordingSandboxExecutor:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.requests: list[SandboxRequest] = []
        self.cancelled_job_ids: list[object] = []

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=self.healthy,
            executor="wsl_fairy_sandbox",
            version="1.0.0" if self.healthy else None,
            error_code=None if self.healthy else "SANDBOX_UNAVAILABLE",
            diagnostics=("test attestation",),
        )

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=SandboxResultStatus.COMPLETED,
            exit_code=0,
            stdout=b"sandbox complete\n",
            stderr=b"",
            output_truncated=False,
            started_at=now,
            finished_at=now,
        )

    def cancel(self, job_id) -> None:
        self.cancelled_job_ids.append(job_id)


class BlockingSandboxExecutor(RecordingSandboxExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.released = threading.Event()

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        self.started.set()
        self.released.wait(timeout=2)
        now = datetime.now(UTC)
        status = (
            SandboxResultStatus.CANCELLED
            if request.job_id in self.cancelled_job_ids
            else SandboxResultStatus.FAILED
        )
        return SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=status,
            exit_code=None,
            stdout=b"",
            stderr=b"",
            output_truncated=False,
            started_at=now,
            finished_at=now,
        )

    def cancel(self, job_id) -> None:
        super().cancel(job_id)
        self.released.set()


class ClosingToolExecutor:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, _definition, _scope, _arguments):
        raise AssertionError("not used")

    def close(self) -> None:
        self.closed = True


def _provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-sandbox",
                    tool_name="run.sandboxed",
                    arguments_fragment=(
                        '{"argv":["python3","-c","print(42)"],"cwd":".",'
                        '"environment":[{"name":"FAIRY_MODE","value":"test"}],'
                        '"timeout_seconds":30,"output_limit_bytes":4096}'
                    ),
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Execution complete"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )


def _project_turn(service, source: Path) -> tuple[dict[str, object], dict[str, object]]:
    project = service.invoke(
        "projects.import",
        {"name": "Sandbox project", "residency": "local_only", "source_path": str(source)},
    )
    conversation = service.invoke(
        "conversations.create",
        {"project_id": project["project"]["id"], "workspace_type": "project_chat"},
    )
    context = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Run the governed command",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "sandbox:task",
        },
    )
    turn = service.invoke(
        "assistant.turns.create",
        {
            "task_id": context["task"]["id"],
            "profile_id": "scripted",
            "idempotency_key": "sandbox:turn",
        },
    )
    return context, turn


def _enable_autonomous(service) -> None:
    service.invoke(
        "permissions.update",
        {
            "profile": "autonomous",
            "capability_overrides": {"run.sandboxed": True},
            "expected_revision": 0,
            "idempotency_key": "sandbox:autonomous",
        },
    )


def test_sandbox_tool_uses_core_owned_scope_archive_generation_and_fence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_bytes(b"managed source\n")
    (source / ".env").write_text("OPENROUTER_API_KEY=must-not-cross\n", encoding="utf-8")
    provider = _provider()
    executor = RecordingSandboxExecutor()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=executor,
    )
    try:
        _enable_autonomous(service)
        context, turn = _project_turn(service, source)

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(executor.requests) == 1
        request = executor.requests[0]
        assert str(request.project_id) == context["task"]["project_id"]
        assert str(request.conversation_id) == context["task"]["conversation_id"]
        assert str(request.task_id) == context["task"]["id"]
        assert str(request.version_id) == context["target_version"]["id"]
        assert request.workspace_generation == 1
        assert request.lease_fence > 0
        assert request.network_policy.value == "none"
        assert request.environment == {"FAIRY_MODE": "test"}
        assert request.argv == ("python3", "-c", "print(42)")
        with zipfile.ZipFile(io.BytesIO(request.workspace_archive)) as archive:
            assert archive.namelist() == ["README.md"]
            assert archive.read("README.md") == b"managed source\n"
            assert ".env" not in archive.namelist()
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            command = unit_of_work.commands.get_run(request.job_id)
        assert command is not None
        assert request.job_id == command.id
        assert request.scope_digest == command.scope_digest
        assert request.lease_fence == command.lease_fence
        assert "sandbox complete" in provider.requests[1].messages[-1].content
    finally:
        service.close()


def test_unhealthy_executor_keeps_sandbox_tool_out_of_the_manifest(tmp_path: Path) -> None:
    executor = RecordingSandboxExecutor(healthy=False)
    service = build_local_service(tmp_path / "data", sandbox_executor=executor)
    try:
        _enable_autonomous(service)
        capabilities = service.invoke("capabilities.get", {})

        assert capabilities["sandbox_healthy"] is False
        assert capabilities["operations"]["run.sandboxed"] is False
        assert executor.requests == []
    finally:
        service.close()


def test_sandbox_archive_rejects_a_file_changed_after_project_indexing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("indexed\n", encoding="utf-8")
    provider = _provider()
    executor = RecordingSandboxExecutor()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=executor,
    )
    try:
        _enable_autonomous(service)
        context, turn = _project_turn(service, source)
        managed = Path(context["target_version"]["project_root"]) / "README.md"
        managed.write_text("changed after authorization\n", encoding="utf-8")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert executor.requests == []
        assert "SCOPE_MISMATCH" in provider.requests[1].messages[-1].content
        assert "changed after authorization" not in provider.requests[1].messages[-1].content
    finally:
        service.close()


def test_scratch_sandbox_uses_an_empty_managed_archive_and_scope_network(
    tmp_path: Path,
) -> None:
    provider = _provider()
    executor = RecordingSandboxExecutor()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=executor,
    )
    try:
        _enable_autonomous(service)
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        context = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Run an isolated calculation",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "sandbox:scratch-task",
            },
        )
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": context["task"]["id"],
                "profile_id": "scripted",
                "idempotency_key": "sandbox:scratch-turn",
            },
        )

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        request = executor.requests[0]
        assert request.project_id is None
        assert request.version_id is None
        assert request.network_policy.value == "public"
        with zipfile.ZipFile(io.BytesIO(request.workspace_archive)) as archive:
            assert archive.namelist() == [".fairy-scratch"]
            assert archive.read(".fairy-scratch") == b""
    finally:
        service.close()


def test_empty_project_uses_a_valid_nonempty_workspace_archive(tmp_path: Path) -> None:
    source = tmp_path / "empty-source"
    source.mkdir()
    provider = _provider()
    executor = RecordingSandboxExecutor()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=executor,
    )
    try:
        _enable_autonomous(service)
        _context, turn = _project_turn(service, source)

        service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        with zipfile.ZipFile(io.BytesIO(executor.requests[0].workspace_archive)) as archive:
            assert archive.namelist() == [".fairy-project-empty"]
            assert archive.read(".fairy-project-empty") == b""
    finally:
        service.close()


def test_sandbox_tool_schema_is_closed_and_has_no_scope_or_network_fields(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        definition = service._registry.get("run.sandboxed")  # type: ignore[attr-defined]
        assert definition is not None
        assert definition.input_schema["additionalProperties"] is False
        properties = definition.input_schema["properties"]
        assert set(properties) == {
            "argv",
            "cwd",
            "environment",
            "timeout_seconds",
            "output_limit_bytes",
        }
    finally:
        service.close()


def test_cancelling_a_running_turn_propagates_to_the_sandbox_job(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("managed", encoding="utf-8")
    provider = _provider()
    executor = BlockingSandboxExecutor()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=executor,
    )
    failures: list[BaseException] = []
    results: list[dict[str, object]] = []
    try:
        _enable_autonomous(service)
        _context, turn = _project_turn(service, source)

        def run_turn() -> None:
            try:
                results.append(service.invoke("assistant.turns.run", {"turn_id": turn["id"]}))
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run_turn)
        thread.start()
        assert executor.started.wait(timeout=3)

        cancelled = service.invoke(
            "assistant.turns.cancel",
            {
                "turn_id": turn["id"],
                "expected_cancellation_revision": turn["cancellation_revision"],
            },
        )
        thread.join(timeout=3)

        assert thread.is_alive() is False
        assert failures == []
        assert cancelled["status"] == "cancelled"
        assert results[0]["status"] == "cancelled"
        assert executor.cancelled_job_ids == [executor.requests[0].job_id]
    finally:
        executor.released.set()
        service.close()


def test_service_close_reaches_the_composed_capability_executor(tmp_path: Path) -> None:
    capability_executor = ClosingToolExecutor()
    service = build_local_service(
        tmp_path / "data",
        tool_executor=capability_executor,
        sandbox_executor=RecordingSandboxExecutor(),
    )

    service.close()

    assert capability_executor.closed is True
