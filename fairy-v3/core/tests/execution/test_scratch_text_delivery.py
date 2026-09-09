import os
import subprocess
import time
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import wait_for_turn
from tests.assistant.test_application import _scratch_task
from tests.assistant.test_model_routing import (
    DEEPSEEK_MODEL_ID,
    PricedCatalogSource,
    _auto_turn,
    _provider,
)
from tests.assistant.test_steering_changeset_plan import _PROFILE, _call, _classification
from tests.assistant.test_workflow_objectives import _text


@pytest.mark.parametrize("engine_version", [3, 4])
@pytest.mark.parametrize("checkpoint_failure", [False, True])
def test_plain_scratch_file_is_checkpointed_without_starting_preview(
    tmp_path,
    engine_version,
    checkpoint_failure,
    monkeypatch,
    worker_program=None,
):
    content = "# Fairy notes\n\nA complete plain-text deliverable.\n"
    provider = _provider(
        profile_id=_PROFILE,
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _classification("change"),
            _call(
                "execution.plan",
                {
                    "files": [
                        {"path": "README.md", "purpose": "Write requested notes", "batch": 1}
                    ],
                    "validation_commands": [],
                },
            ),
            _call(
                "edit.propose_changeset",
                {
                    "files": [{"path": "README.md", "content": content}],
                    "reason": "Write notes",
                },
            ),
            _text("Saved README.md."),
            *([_text("Saved README.md.")] if checkpoint_failure else []),
        ],
    )
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=engine_version,
        model_catalog_source=PricedCatalogSource(),
        environment=({"FAIRY_LOCAL_WORKER_PROGRAM": worker_program} if worker_program else {}),
    )
    checkpoint_calls = []
    provisioner = service._application._workspaces
    original_checkpoint = provisioner.checkpoint

    def checkpoint(**kwargs):
        root = provisioner.version_path(kwargs["project_id"], kwargs["version_id"])
        checkpoint_calls.append((root / "README.md").read_text(encoding="utf-8"))
        if checkpoint_failure:
            raise RuntimeError("private-checkpoint-detail")
        return original_checkpoint(**kwargs)

    monkeypatch.setattr(provisioner, "checkpoint", checkpoint)
    try:
        service.invoke("models.catalog.refresh", {})
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": DEEPSEEK_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "model",
            },
        )
        task = _scratch_task(service, "Create README.md with complete plain-text Fairy notes.")
        turn = _auto_turn(service, task, "plain-text-delivery")
        other = _scratch_task(service, "Unrelated notes")
        service._assistant_scheduler.start(UUID(turn["id"]))
        deadline = time.monotonic() + 5
        approvals = []
        while time.monotonic() < deadline:
            approvals = service.invoke("approvals.list", {"task_id": turn["task_id"]})["items"]
            if approvals:
                break
            time.sleep(0.01)
        assert len(approvals) == 1
        service.invoke("approvals.decide", {"approval_id": approvals[0]["id"], "approved": True})
        completed = wait_for_turn(
            service,
            turn["id"],
            status="failed" if checkpoint_failure else "completed",
        )
        assert "private-checkpoint-detail" not in str(completed)
        with service._unit_of_work_factory() as unit:
            plan = unit.state.execution_plan_for_task(UUID(task["id"]))
            version = unit.state.get_version(plan.version_id)
            conversation = unit.state.get_conversation(UUID(task["conversation_id"]))
            assert (conversation.base_version_id == version.id) is not checkpoint_failure
            assert conversation.active_draft_version_id is None
            assert unit.state.execution_plan_for_task(UUID(other["id"])) is None
            assert not unit.state.runtimes_for_task(UUID(task["id"]))
            if not checkpoint_failure:
                assert all(
                    step.status in {"completed", "skipped"}
                    for step in unit.state.task_steps_for_plan(plan.id)
                )
        root = Path(version.project_root)
        assert (root / "README.md").read_text(encoding="utf-8") == content
        assert checkpoint_calls and all(value == content for value in checkpoint_calls)
        messages = service.invoke("messages.list", {"conversation_id": task["conversation_id"]})
        assert [m["content"] for m in messages["items"] if m["role"] == "assistant"] == (
            [] if checkpoint_failure else ["Saved README.md."]
        )
        if not checkpoint_failure:
            assert len(checkpoint_calls) == 1
            assert len(provider.requests) == 4
            if worker_program:
                saved = subprocess.run(
                    ["git", "show", "HEAD:README.md"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                assert saved.stdout == content
    finally:
        service.close()


@pytest.mark.parametrize("engine_version", [3, 4])
def test_native_worker_saves_plain_scratch_file_to_git(tmp_path, engine_version, monkeypatch):
    program = os.environ.get("FAIRY_ACCEPTANCE_LOCAL_WORKER")
    if not program:
        pytest.skip("Set FAIRY_ACCEPTANCE_LOCAL_WORKER to run the real Rust/Git gate")
    worker = Path(program).resolve(strict=True)
    assert worker.is_file()
    test_plain_scratch_file_is_checkpointed_without_starting_preview(
        tmp_path,
        engine_version,
        False,
        monkeypatch,
        worker_program=str(worker),
    )
