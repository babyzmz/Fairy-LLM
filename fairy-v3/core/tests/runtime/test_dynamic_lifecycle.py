from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from fairy_core.application.core import CoreApplication, TaskContext
from fairy_core.application.runtime import PreviewStartRequest, RuntimeApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ChangesetProposal,
    ExecutionTarget,
    FileMutation,
    TaskCreate,
)
from fairy_core.domain.execution import (
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    PreviewStatus,
    RuntimeKind,
)
from fairy_core.domain.models import OperationMode, ProjectResidency, WorkspaceType
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
)
from fairy_core.runtime.templates import PORT_TOKEN, select_runtime_template
from tests.runtime_support import FakeRuntimeExecutor, FakeWorkspace


class DynamicRuntimeExecutor(FakeRuntimeExecutor):
    def __init__(self, *, response_target: str | None = None) -> None:
        super().__init__()
        self.dynamic_calls: list[DynamicRuntimeStart] = []
        self.response_target = response_target

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult:
        self.dynamic_calls.append(request)
        target = self.response_target or request.execution_target
        handle = f"dynamic:{request.preview_id}:{request.lease_fence}"
        if target == "local":
            host = "127.0.0.1"
            port = 43126
            url = f"http://{host}:{port}/{request.preview_id}/"
        else:
            host = "preview.fairy.test"
            port = 443
            url = f"https://{host}/v1/previews/{request.preview_id}/"
        result = RuntimeStartResult(
            executor_handle=handle,
            host=host,
            port=port,
            url=url,
            state=ExecutorRuntimeState.RUNNING,
            execution_target=target,
        )
        self.probes[handle] = RuntimeProbeResult(
            executor_handle=handle,
            state=ExecutorRuntimeState.RUNNING,
            host=host,
            port=port,
            url=url,
            execution_target=target,
        )
        if self.crash_after_start:
            raise SystemExit("crash after dynamic external start")
        return result

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        return f"dynamic:{target.preview_id}:{target.lease_fence}"


@dataclass(slots=True)
class DynamicStack:
    core: CoreApplication
    runtime: RuntimeApplication
    factory: SqlAlchemyUnitOfWorkFactory
    executor: DynamicRuntimeExecutor
    task: TaskContext


@pytest.mark.parametrize(
    ("execution_target", "expected_kind", "expected_scheme"),
    [
        (ExecutionTarget.LOCAL, RuntimeKind.WSL_PROJECT, "http://127.0.0.1:"),
        (ExecutionTarget.CLOUD, RuntimeKind.CLOUD_OCI, "https://preview.fairy.test/"),
    ],
)
def test_dynamic_preview_binds_template_archive_scope_and_endpoint(
    tmp_path: Path,
    execution_target: ExecutionTarget,
    expected_kind: RuntimeKind,
    expected_scheme: str,
) -> None:
    stack = _stack(tmp_path, execution_target=execution_target)
    _append_dependency_layer(stack)

    context = stack.runtime.start_preview(
        PreviewStartRequest(
            task_id=stack.task.task.id,
            idempotency_key=f"dynamic:{execution_target.value}:start",
        )
    )

    assert context.runtime.kind is expected_kind
    assert context.preview.status is PreviewStatus.READY
    assert context.preview.url is not None
    assert context.preview.url.startswith(expected_scheme)
    assert stack.executor.start_calls == []
    assert len(stack.executor.dynamic_calls) == 1
    request = stack.executor.dynamic_calls[0]
    assert request.project_id == stack.task.task.project_id
    assert request.task_id == stack.task.task.id
    assert request.version_id == stack.task.task.target_version_id
    assert request.runtime_id == context.runtime.id
    assert request.scope_digest == stack.task.scope.scope_digest
    assert request.workspace_generation >= 1
    assert request.lease_fence == 1
    assert request.argv.count(PORT_TOKEN) == 1
    assert request.workspace_archive.startswith(b"PK")
    assert hashlib.sha256(request.workspace_archive).hexdigest() == request.archive_sha256
    with stack.factory() as unit_of_work:
        manifests = [
            artifact
            for artifact in unit_of_work.state.artifacts_for_task(stack.task.task.id)
            if artifact.artifact_type is ArtifactType.PREVIEW_MANIFEST
        ]
    assert manifests[-1].metadata["kind"] == expected_kind.value
    assert manifests[-1].metadata["adapter"] == "vite"
    assert manifests[-1].metadata["dependency_key"] == request.dependency_key


def test_dynamic_preview_requires_current_dependency_layer(tmp_path: Path) -> None:
    stack = _stack(tmp_path, execution_target=ExecutionTarget.LOCAL)

    with pytest.raises(RuntimeExecutorError) as captured:
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="dynamic:missing-dependencies",
            )
        )

    assert captured.value.error_code == "DEPENDENCY_LAYER_MISSING"
    assert stack.executor.dynamic_calls == []


def test_dynamic_executor_cannot_rebind_local_scope_to_cloud_endpoint(tmp_path: Path) -> None:
    stack = _stack(
        tmp_path,
        execution_target=ExecutionTarget.LOCAL,
        response_target="cloud",
    )
    _append_dependency_layer(stack)

    with pytest.raises(RuntimeExecutorError) as captured:
        stack.runtime.start_preview(
            PreviewStartRequest(
                task_id=stack.task.task.id,
                idempotency_key="dynamic:target-rebind",
            )
        )

    assert captured.value.error_code == "SCOPE_MISMATCH"
    with stack.factory() as unit_of_work:
        preview = unit_of_work.state.preview_for_task(
            stack.task.task.id,
            include_terminal=True,
        )
    assert preview is not None and preview.status is PreviewStatus.FAILED
    assert preview.error_code == "SCOPE_MISMATCH"


def test_dynamic_preview_recovers_external_start_without_redispatch(tmp_path: Path) -> None:
    stack = _stack(tmp_path, execution_target=ExecutionTarget.LOCAL)
    _append_dependency_layer(stack)
    stack.executor.crash_after_start = True
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="dynamic:recover-external-start",
    )

    with pytest.raises(SystemExit):
        stack.runtime.start_preview(request)
    stack.executor.crash_after_start = False

    recovered = stack.runtime.recover_interrupted()

    assert recovered[-1].status is PreviewStatus.READY
    assert len(stack.executor.dynamic_calls) == 1
    assert stack.executor.probe_calls == [f"dynamic:{recovered[-1].id}:1"]
    with stack.factory() as unit_of_work:
        runtime = unit_of_work.state.get_runtime(recovered[-1].runtime_id)
    assert runtime is not None
    assert runtime.status.value == "running"


def test_dynamic_preview_retry_uses_a_higher_durable_runtime_fence(tmp_path: Path) -> None:
    stack = _stack(
        tmp_path,
        execution_target=ExecutionTarget.LOCAL,
        response_target="cloud",
    )
    _append_dependency_layer(stack)
    request = PreviewStartRequest(
        task_id=stack.task.task.id,
        idempotency_key="dynamic:retry-fence",
    )

    with pytest.raises(RuntimeExecutorError):
        stack.runtime.start_preview(request)
    stack.executor.response_target = None

    recovered = stack.runtime.start_preview(request)

    assert recovered.preview.status is PreviewStatus.READY
    assert [call.lease_fence for call in stack.executor.dynamic_calls] == [1, 3]


def _stack(
    tmp_path: Path,
    *,
    execution_target: ExecutionTarget,
    response_target: str | None = None,
) -> DynamicStack:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    workspace = FakeWorkspace(tmp_path / "managed")
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=PolicyEngine(registry),
    )
    executor = DynamicRuntimeExecutor(response_target=response_target)
    runtime = RuntimeApplication(
        unit_of_work_factory=factory,
        executor=executor,
        registry=registry,
        policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
    )
    project = core.create_project(name="Dynamic", residency=ProjectResidency.LOCAL_ONLY)
    conversation = core.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Run the Vite project",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=execution_target,
            idempotency_key=f"dynamic:{execution_target.value}:task",
        )
    )
    package = json.dumps(
        {
            "name": "dynamic-fixture",
            "dependencies": {"vite": "8.1.4"},
            "scripts": {"dev": "attacker-controlled command"},
        }
    )
    lock = json.dumps({"lockfileVersion": 3, "packages": {}})
    pending = core.propose_changeset(
        ChangesetProposal(
            task_id=task.task.id,
            files=(
                FileMutation(path="index.html", content="<main id='app'></main>"),
                FileMutation(path="package.json", content=package),
                FileMutation(path="package-lock.json", content=lock),
            ),
            reason="Create a locked Vite project",
            idempotency_key=f"dynamic:{execution_target.value}:changeset",
        )
    )
    core.decide_approval(
        approval_id=pending.approval.id,
        approved=True,
        decided_by="user",
    )
    with factory() as unit_of_work:
        persisted = unit_of_work.state.get_task(task.task.id)
        assert persisted is not None
        refreshed = TaskContext(
            task=persisted,
            target_version=unit_of_work.state.get_version(persisted.target_version_id),
            scope=core.scope_for_task(unit_of_work.state, persisted),
        )
    return DynamicStack(core, runtime, factory, executor, refreshed)


def _append_dependency_layer(stack: DynamicStack) -> None:
    template = select_runtime_template(
        stack.task.scope.project_root,
        execution_target=stack.task.scope.execution_target,
    )
    assert template.dependency_key is not None
    with stack.factory() as unit_of_work:
        index = unit_of_work.project_indexes.get(stack.task.task.target_version_id)
        assert index is not None
        artifact = Artifact.create(
            project_id=stack.task.task.project_id,
            conversation_id=stack.task.task.conversation_id,
            task_id=stack.task.task.id,
            version_id=stack.task.task.target_version_id,
            artifact_type=ArtifactType.LOG,
            visibility=ArtifactVisibility.CONVERSATION,
            storage_location=f"dependency://{template.dependency_key}",
            media_type="application/json",
            byte_length=2,
            content_hash=hashlib.sha256(b"{}").hexdigest(),
            metadata={
                "tool_name": "deps.install",
                "status": "completed",
                "workspace_generation": index.generation,
                "dependency_key": template.dependency_key,
            },
        )
        unit_of_work.state.append_artifact(artifact)
        unit_of_work.commit()
