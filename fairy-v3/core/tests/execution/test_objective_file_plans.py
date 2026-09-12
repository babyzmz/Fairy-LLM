import hashlib
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


def _pending_approval(service, turn):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = [
            item
            for item in service.invoke(
                "approvals.list",
                {"task_id": turn["task_id"]},
            )["items"]
            if item["decision"] == "pending"
        ]
        if pending:
            assert len(pending) == 1
            return pending[0]
        time.sleep(0.01)
    raise AssertionError("The next distinct file approval was not created")


@pytest.mark.parametrize("restart", [False, True])
def test_each_mutation_objective_has_a_new_plan_approval_and_checkpoint(
    tmp_path,
    restart,
    worker_program=None,
):
    first_content, last_content = "Initial Fairy notes.\n", "Revised Fairy notes.\n"
    provider = _provider(
        profile_id=_PROFILE,
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _classification(
                "change",
                objectives=[
                    {"goal": "Create initial README.md", "action": "change", "depends_on": []},
                    {"goal": "Then refine README.md", "action": "change", "depends_on": [0]},
                ],
            ),
            _call(
                "execution.plan",
                {
                    "files": [
                        {"path": "README.md", "purpose": "Create notes", "batch": 1},
                    ],
                    "validation_commands": [],
                },
            ),
            _call(
                "edit.propose_changeset",
                {
                    "files": [{"path": "README.md", "content": first_content}],
                    "reason": "Create notes",
                },
            ),
            _text("Initial notes created."),
            _call("project.read", {"path": "README.md"}),
            _call(
                "execution.plan",
                {
                    "files": [
                        {
                            "path": "README.md",
                            "purpose": "Refine notes",
                            "batch": 1,
                            "expected_hash": hashlib.sha256(first_content.encode()).hexdigest(),
                        }
                    ],
                    "validation_commands": [],
                },
            ),
            _call(
                "edit.propose_changeset",
                {
                    "files": [{"path": "README.md", "content": last_content}],
                    "reason": "Refine notes",
                },
            ),
            _text("Saved revised notes."),
        ],
    )

    def build():
        return build_local_service(
            tmp_path / "data",
            provider_registry=ProviderRegistry((provider,)),
            assistant_workflow_engine_version=4,
            model_catalog_source=PricedCatalogSource(),
            environment=({"FAIRY_LOCAL_WORKER_PROGRAM": worker_program} if worker_program else {}),
        )

    service = build()
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
        task = _scratch_task(service, "Create initial README.md, then refine its wording.")
        turn = _auto_turn(service, task, "two-changes")
        other = _scratch_task(service, "Other conversation")
        service._assistant_scheduler.start(UUID(turn["id"]))
        first_approval = _pending_approval(service, turn)
        with service._unit_of_work_factory() as unit:
            first_plan = unit.state.execution_plan_for_task(UUID(task["id"]))
        service.invoke(
            "approvals.decide",
            {
                "approval_id": first_approval["id"],
                "approved": True,
            },
        )
        deadline = time.monotonic() + 5
        while len(provider.requests) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "project.read" in {tool.name for tool in provider.requests[4].tools}
        second_approval = _pending_approval(service, turn)
        assert first_approval["id"] != second_approval["id"]
        with service._unit_of_work_factory() as unit:
            second_plan = unit.state.execution_plan_for_task(UUID(task["id"]))
            assert second_plan.id != first_plan.id and second_plan.generation == 2
            assert second_plan.workflow_run_id == first_plan.workflow_run_id
            assert second_plan.workflow_plan_revision == first_plan.workflow_plan_revision == 1
            assert (first_plan.workflow_objective_index, second_plan.workflow_objective_index) == (
                0,
                1,
            )
            assert unit.state.get_execution_plan(first_plan.id).status == "completed"
            assert unit.state.execution_plan_for_task(UUID(other["id"])) is None
        if restart:
            service.close()
            service = build()
        service.invoke(
            "approvals.decide",
            {
                "approval_id": second_approval["id"],
                "approved": True,
            },
        )
        assert wait_for_turn(service, turn["id"])["status"] == "completed"
        with service._unit_of_work_factory() as unit:
            plan = unit.state.execution_plan_for_task(UUID(task["id"]))
            version = unit.state.get_version(plan.version_id)
            conversation = unit.state.get_conversation(UUID(task["conversation_id"]))
            assert conversation.base_version_id == version.id
            assert conversation.active_draft_version_id is None
        root = Path(version.project_root)
        assert (root / "README.md").read_text(encoding="utf-8") == last_content
        if worker_program:
            saved = subprocess.run(
                ["git", "show", "HEAD:README.md"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            assert saved.stdout == last_content
        messages = service.invoke("messages.list", {"conversation_id": task["conversation_id"]})
        assert [m["content"] for m in messages["items"] if m["role"] == "assistant"] == [
            "Saved revised notes.",
        ]
        assert len(provider.requests) == 8
    finally:
        service.close()


def test_native_multi_objective_checkpoint_contains_the_last_revision(tmp_path):
    program = os.environ.get("FAIRY_ACCEPTANCE_LOCAL_WORKER")
    if not program:
        pytest.skip("Set FAIRY_ACCEPTANCE_LOCAL_WORKER for the real Rust/Git gate")
    test_each_mutation_objective_has_a_new_plan_approval_and_checkpoint(
        tmp_path,
        True,
        worker_program=str(Path(program).resolve(strict=True)),
    )
