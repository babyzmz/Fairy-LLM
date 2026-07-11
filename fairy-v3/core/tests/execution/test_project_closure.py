from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import (
    SandboxPurpose,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider


class ScriptedSandboxExecutor:
    def __init__(self, statuses: Iterable[SandboxResultStatus] = ()) -> None:
        self.statuses = list(statuses)
        self.requests: list[SandboxRequest] = []

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="wsl_fairy_sandbox",
            version="1.0.0",
            error_code=None,
            diagnostics=("fixture",),
        )

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else SandboxResultStatus.COMPLETED
        now = datetime.now(UTC)
        return SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=status,
            exit_code=0 if status is SandboxResultStatus.COMPLETED else 1,
            stdout=b"review output\n",
            stderr=b"" if status is SandboxResultStatus.COMPLETED else b"failed\n",
            output_truncated=False,
            started_at=now,
            finished_at=now,
        )

    def cancel(self, _job_id) -> None:
        pass


def test_assistant_dependency_install_uses_typed_template_and_persists_report(
    tmp_path: Path,
) -> None:
    sandbox = ScriptedSandboxExecutor()
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="deps-1",
                    tool_name="deps.install",
                    arguments_fragment="{}",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Dependencies are ready.",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=sandbox,
    )
    try:
        task_id = _project_task(service, tmp_path / "source", profile_id="dependency")
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {"deps.install": True},
                "expected_revision": 0,
                "idempotency_key": "closure:permissions",
            },
        )
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task_id,
                "profile_id": "scripted",
                "idempotency_key": "closure:dependency-turn",
            },
        )

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(sandbox.requests) == 1
        request = sandbox.requests[0]
        assert request.argv == (
            "npm",
            "ci",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        )
        assert request.purpose is SandboxPurpose.DEPENDENCY
        assert request.dependency_manager == "npm"
        assert request.dependency_key is not None and len(request.dependency_key) == 64
        artifacts = service.invoke("artifacts.list", {"task_id": task_id})["items"]
        assert [item["artifact_type"] for item in artifacts] == ["log"]
        assert artifacts[0]["metadata"]["dependency_key"] == request.dependency_key
        assert service.invoke("tasks.get", {"task_id": task_id})["status"] == "executing"
    finally:
        service.close()


def test_review_runs_all_core_owned_checks_before_checkpoint(tmp_path: Path) -> None:
    sandbox = ScriptedSandboxExecutor()
    provider = _direct_provider("review")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=sandbox,
    )
    try:
        task_id = _project_task(service, tmp_path / "source", profile_id="review")
        _run_direct_turn(service, task_id, "review")

        checkpoint = service.invoke("tasks.review", {"task_id": task_id})

        assert [request.argv for request in sandbox.requests] == [
            ("npm", "run", "typecheck", "--if-present"),
            ("npm", "run", "lint", "--if-present"),
            ("npm", "test", "--if-present"),
            ("npm", "run", "build", "--if-present"),
        ]
        assert all(request.purpose is SandboxPurpose.REVIEW for request in sandbox.requests)
        assert len(checkpoint["command_run_ids"]) == 4
        assert service.invoke("tasks.get", {"task_id": task_id})["status"] == "ready"
        artifacts = service.invoke("artifacts.list", {"task_id": task_id})["items"]
        assert len(artifacts) == 4
        assert {item["artifact_type"] for item in artifacts} == {"report"}
    finally:
        service.close()


def test_failed_review_repairs_on_a_new_workspace_generation_without_replay(
    tmp_path: Path,
) -> None:
    sandbox = ScriptedSandboxExecutor((SandboxResultStatus.FAILED,))
    provider = _direct_provider("repair")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        sandbox_executor=sandbox,
    )
    try:
        task_id = _project_task(service, tmp_path / "source", profile_id="repair")
        _run_direct_turn(service, task_id, "repair")

        with pytest.raises(RuntimeError, match=r"review\.typecheck"):
            service.invoke("tasks.review", {"task_id": task_id})

        assert service.invoke("tasks.get", {"task_id": task_id})["status"] == "repairing"
        failed_job_id = sandbox.requests[0].job_id
        proposal = service.invoke(
            "changesets.propose",
            {
                "task_id": task_id,
                "files": [{"path": "src.js", "content": "export const fixed = true;\n"}],
                "reason": "Repair the failed review",
                "idempotency_key": "closure:repair",
            },
        )
        service.invoke(
            "approvals.decide",
            {"approval_id": proposal["approval"]["id"], "approved": True},
        )

        checkpoint = service.invoke("tasks.review", {"task_id": task_id})

        assert service.invoke("tasks.get", {"task_id": task_id})["status"] == "ready"
        assert len(checkpoint["command_run_ids"]) == 4
        assert len(sandbox.requests) == 5
        assert sandbox.requests[1].workspace_generation > sandbox.requests[0].workspace_generation
        assert all(request.job_id != failed_job_id for request in sandbox.requests[1:])
    finally:
        service.close()


def _project_task(service, source: Path, *, profile_id: str) -> str:
    source.mkdir()
    (source / "index.html").write_text("<h1>Closure</h1>\n", encoding="utf-8")
    (source / "src.js").write_text("export const ready = true;\n", encoding="utf-8")
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "closure",
                "scripts": {
                    "typecheck": "tsc --noEmit",
                    "lint": "eslint .",
                    "test": "vitest run",
                    "build": "vite build",
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {}}),
        encoding="utf-8",
    )
    project = service.invoke(
        "projects.import",
        {"name": "Closure", "residency": "local_only", "source_path": str(source)},
    )
    conversation = service.invoke(
        "conversations.create",
        {"project_id": project["project"]["id"], "workspace_type": "project_chat"},
    )
    context = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Verify the project closure",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": f"closure:{profile_id}:task",
        },
    )
    return context["task"]["id"]


def _direct_provider(profile_id: str) -> ScriptedProvider:
    del profile_id
    return ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Ready for review."),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            )
        ]
    )


def _run_direct_turn(service, task_id: str, profile_id: str) -> None:
    turn = service.invoke(
        "assistant.turns.create",
        {
            "task_id": task_id,
            "profile_id": "scripted",
            "idempotency_key": f"closure:{profile_id}:turn",
        },
    )
    completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
    assert completed["status"] == "completed"
    assert service.invoke("tasks.get", {"task_id": task_id})["status"] == "executing"
