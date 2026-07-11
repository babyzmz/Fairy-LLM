from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandStatus, EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ApprovalPolicy, build_default_registry
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.models import ExecutionTarget, TaskCreate
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.system_actions.application import (
    SystemActionApplication,
    SystemActionUnavailableError,
)
from fairy_core.system_actions.models import (
    CopyTextAction,
    NotificationLevel,
    NotifyAction,
    OpenSettingsAction,
    OpenUrlAction,
    RevealPathAction,
    SystemActionRequest,
    SystemActionWorkerResult,
    SystemSettings,
)
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


class RecordingSystemWorker:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], str]] = []

    def execute(
        self,
        *,
        action: dict[str, object],
        idempotency_key: str,
    ) -> SystemActionWorkerResult:
        self.calls.append((action, idempotency_key))
        return SystemActionWorkerResult(
            action_type=str(action["type"]),
            completed=True,
            replayed=False,
        )


def applications(tmp_path: Path):
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    worker = RecordingSystemWorker()
    actions = SystemActionApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=PolicyEngine(registry),
        scope_resolver=core.scope_for_task,
        worker=worker,
    )
    project = core.create_project(
        name="System actions",
        residency=ProjectResidency.LOCAL_ONLY,
    ).project
    conversation = core.create_conversation(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Use a typed host action",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="system-actions:task",
        )
    ).task
    return engine, factory, registry, task, actions, worker


def request(task_id, action, *, confirmed=False, profile=PermissionProfile.STANDARD):
    return SystemActionRequest(
        task_id=task_id,
        action=action,
        idempotency_key="system-actions:one",
        profile=profile,
        user_confirmed=confirmed,
    )


def test_standard_profile_creates_one_approval_bound_command_then_replays(
    tmp_path: Path,
) -> None:
    engine, factory, _registry, task, actions, worker = applications(tmp_path)
    pending = actions.execute(
        request(task.id, OpenUrlAction(type="open_url", url="https://example.com/a"))
    )

    assert pending.status is CommandStatus.WAITING_APPROVAL
    assert pending.requires_approval is True
    assert worker.calls == []
    with factory() as unit_of_work:
        run = unit_of_work.commands.get_run(pending.run_id)
        assert run is not None
        assert run.command_name == "system.open_url"
        assert run.input_payload == {"type": "open_url", "url": "https://example.com/a"}

    completed = actions.execute(
        request(
            task.id,
            OpenUrlAction(type="open_url", url="https://example.com/a"),
            confirmed=True,
        )
    )
    replayed = actions.execute(
        request(
            task.id,
            OpenUrlAction(type="open_url", url="https://example.com/a"),
            confirmed=True,
        )
    )

    assert completed.status is CommandStatus.SUCCEEDED
    assert completed.completed is True
    assert replayed.replayed is True
    assert len(worker.calls) == 1
    assert worker.calls[0][1] == f"system:{pending.run_id}"
    with factory() as unit_of_work:
        events = unit_of_work.commands.events_for_run(pending.run_id)
    public = [event for event in events if event.visibility is EventVisibility.USER]
    assert any(event.event_type == "system.action.completed" for event in public)
    assert "https://example.com/a" not in "\n".join(
        f"{event.message}{event.payload}" for event in public
    )
    engine.dispose()


def test_autonomous_executes_but_observe_cannot_create_host_effects(tmp_path: Path) -> None:
    engine, _factory, _registry, task, actions, worker = applications(tmp_path)
    completed = actions.execute(
        request(
            task.id,
            CopyTextAction(type="copy_text", text="bounded clipboard text"),
            profile=PermissionProfile.AUTONOMOUS,
        )
    )
    assert completed.status is CommandStatus.SUCCEEDED
    assert completed.requires_approval is False
    assert len(worker.calls) == 1

    with pytest.raises(SystemActionUnavailableError) as denied:
        actions.execute(
            request(
                task.id,
                OpenSettingsAction(type="open_settings", page=SystemSettings.DISPLAY),
                profile=PermissionProfile.OBSERVE,
            )
        )
    assert denied.value.error_code == "CAPABILITY_NOT_AVAILABLE"
    engine.dispose()


def test_advanced_capability_toggle_blocks_the_selected_host_action(tmp_path: Path) -> None:
    engine, _factory, _registry, task, actions, worker = applications(tmp_path)
    blocked = request(
        task.id,
        OpenUrlAction(type="open_url", url="https://example.com"),
        confirmed=True,
    ).model_copy(update={"capability_overrides": {"system.open_url": False}})

    with pytest.raises(SystemActionUnavailableError) as denied:
        actions.execute(blocked)

    assert denied.value.error_code == "CAPABILITY_NOT_AVAILABLE"
    assert worker.calls == []
    engine.dispose()


def test_reveal_path_uses_core_scope_identity_not_model_scope_fields(tmp_path: Path) -> None:
    engine, _factory, _registry, task, actions, worker = applications(tmp_path)
    result = actions.execute(
        request(
            task.id,
            RevealPathAction(type="reveal_path", relative_path="src/main.ts"),
            confirmed=True,
        )
    )

    assert result.status is CommandStatus.SUCCEEDED
    payload, _key = worker.calls[0]
    assert payload == {
        "type": "reveal_path",
        "project_id": str(task.project_id),
        "version_id": str(task.target_version_id),
        "relative_path": "src/main.ts",
    }
    assert "project_root" not in payload
    assert "scope_digest" not in payload

    with pytest.raises(ValidationError):
        RevealPathAction.model_validate(
            {
                "type": "reveal_path",
                "relative_path": "src/main.ts",
                "project_root": "C:/outside",
                "scope_digest": "model-value",
            }
        )
    engine.dispose()


@pytest.mark.parametrize(
    "action",
    [
        lambda: OpenUrlAction(type="open_url", url="http://example.com"),
        lambda: OpenUrlAction(type="open_url", url="https://user:secret@example.com"),
        lambda: RevealPathAction(type="reveal_path", relative_path="../outside.txt"),
        lambda: CopyTextAction(type="copy_text", text="x" * 32_769),
        lambda: NotifyAction(
            type="notify",
            title="x" * 81,
            body="body",
            level=NotificationLevel.INFO,
        ),
        lambda: NotifyAction(
            type="notify",
            title="Title",
            body="x" * 241,
            level=NotificationLevel.WARNING,
        ),
        lambda: OpenSettingsAction(type="open_settings", page="registry"),
    ],
)
def test_models_reject_unsafe_or_unbounded_inputs(action) -> None:
    with pytest.raises((ValidationError, ValueError)):
        action()


def test_registry_exposes_only_typed_profile_approved_actions() -> None:
    registry = build_default_registry()
    names = {
        "system.open_url",
        "system.reveal_path",
        "system.copy_text",
        "system.notify",
        "system.open_settings",
    }
    for name in names:
        definition = registry.get(name)
        assert definition is not None
        assert definition.approval_policy is ApprovalPolicy.PROFILE
        assert definition.idempotent is True
        assert definition.input_schema["additionalProperties"] is False
        serialized = repr(definition.input_schema).lower()
        assert all(
            forbidden not in serialized
            for forbidden in ("program", "args", "command", "shell", "script", "registry")
        )
