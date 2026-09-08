import hashlib
import json
import time
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import ChangesetStatus
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import wait_for_turn
from tests.assistant.test_application import _scratch_task
from tests.assistant.test_model_routing import (
    DEEPSEEK_MODEL_ID,
    PricedCatalogSource,
    _auto_turn,
    _provider,
)

_PROFILE = "openrouter-deepseek-v4-pro"
_INSTRUCTION = "Only explain the existing README. Do not modify or execute anything."


def _call(name, arguments, *, suffix=""):
    return (
        ModelDelta.tool_call(
            profile_id=_PROFILE,
            sequence=1,
            tool_call_id=f"call-{name}{suffix}",
            tool_name=name,
            arguments_fragment=json.dumps(arguments),
        ),
        ModelDelta.done(profile_id=_PROFILE, sequence=2, finish_reason="tool_calls"),
    )


def _classification(action):
    goal = "Update README.md" if action == "change" else _INSTRUCTION
    return (
        ModelDelta.text(
            profile_id=_PROFILE,
            sequence=1,
            text=json.dumps(
                {
                    "evidence_requirements": [],
                    "requires_workspace_changes": action == "change",
                    "public_summary": goal,
                    "interpretation": {
                        "normalized_goal": goal,
                        "action": action,
                        "objectives": [{"goal": goal, "action": action, "depends_on": []}],
                        "targets": ["README.md"],
                        "constraints": [],
                        "deliverable": "README explanation",
                        "assumptions": [],
                        "missing_information": [],
                        "confidence": "high",
                        "disposition": "ready",
                        "public_summary": goal,
                        "clarification_question": None,
                    },
                }
            ),
        ),
        ModelDelta.done(profile_id=_PROFILE, sequence=2, finish_reason="stop"),
    )


@pytest.mark.parametrize(
    "approved,restart,apply_started,new_plan,repeat_arguments",
    [
        (False, False, False, False, False),
        (True, False, False, False, False),
        (False, True, False, False, False),
        (True, True, False, False, False),
        (True, False, True, False, False),
        (False, False, False, True, False),
        (True, True, False, True, False),
        (False, False, False, True, True),
        (True, True, False, True, True),
    ],
)
def test_explain_update_supersedes_unapplied_changeset_and_its_file_plan(
    tmp_path,
    approved,
    restart,
    apply_started,
    new_plan,
    repeat_arguments,
):
    revised_content = "draft" if repeat_arguments else "revised"
    revised_reason = "Update copy" if repeat_arguments else "Revised copy"
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
    provider = _provider(
        profile_id=_PROFILE,
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _classification("change"),
            _call("project.read", {"path": "README.md"}),
            _call(
                "execution.plan",
                {
                    "files": [
                        {
                            "path": "README.md",
                            "purpose": "Update copy",
                            "batch": 1,
                            "expected_hash": hashlib.sha256(b"base").hexdigest(),
                        }
                    ],
                    "validation_commands": [],
                },
            ),
            _call(
                "edit.propose_changeset",
                {
                    "files": [{"path": "README.md", "content": "draft"}],
                    "reason": "Update copy",
                },
            ),
            _classification("change" if new_plan else "explain"),
            *(
                [
                    _call(
                        "execution.plan",
                        {
                            "files": [
                                {
                                    "path": "README.md",
                                    "purpose": revised_reason,
                                    "batch": 1,
                                    "expected_hash": hashlib.sha256(b"base").hexdigest(),
                                }
                            ],
                            "validation_commands": [],
                        },
                        suffix="-revised",
                    ),
                    _call(
                        "edit.propose_changeset",
                        {
                            "files": [{"path": "README.md", "content": revised_content}],
                            "reason": revised_reason,
                        },
                        suffix="-revised",
                    ),
                ]
                if new_plan
                else []
            ),
            (
                ModelDelta.text(
                    profile_id=_PROFILE,
                    sequence=1,
                    text="README.md contains the revised copy."
                    if new_plan
                    else "The README remains unchanged.",
                ),
                ModelDelta.done(profile_id=_PROFILE, sequence=2, finish_reason="stop"),
            ),
        ],
    )
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=4,
        model_catalog_source=PricedCatalogSource(),
    )
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
                "idempotency_key": "changeset-model",
            },
        )
        project = service.invoke(
            "projects.import",
            {
                "name": "Steering project",
                "residency": "local_only",
                "source_path": str(source),
            },
        )
        conversation = service.invoke(
            "conversations.create",
            {
                "project_id": project["project"]["id"],
                "workspace_type": "project_chat",
            },
        )
        context = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Update README.md",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "changeset-task",
            },
        )
        turn = _auto_turn(service, context["task"], "steered-changeset")
        other_task = _scratch_task(service, "Keep this other conversation untouched")
        other = _auto_turn(service, other_task, "other-changeset-steer")
        assert (
            service.invoke("assistant.turns.run", {"turn_id": turn["id"]})["status"]
            == "waiting_for_tool"
        )
        approval = service.invoke("approvals.list", {"task_id": turn["task_id"]})["items"][0]
        with service._unit_of_work_factory() as unit:
            original_plan_id = unit.state.execution_plan_for_task(UUID(turn["task_id"])).id
        if approved:
            # Real durable boundary between approval commit and Workspace apply.
            service._application.record_approval_decision(
                approval_id=UUID(approval["id"]),
                approved=True,
                decided_by="user",
            )
        request = {
            "turn_id": turn["id"],
            "instruction": "Update README.md with the revised copy instead."
            if new_plan
            else _INSTRUCTION,
            "expected_revision": 1,
            "idempotency_key": "explain-existing-readme",
        }
        if apply_started:
            # Persist the start boundary without writing files. The deliberately
            # unresolved operation must not be relabelled as an unstarted proposal.
            with service._application._transaction() as (unit, commands):
                pending = unit.state.get_approval(UUID(approval["id"]))
                changeset = unit.state.get_changeset(pending.changeset_id)
                task = unit.state.get_task(pending.task_id)
                commands.start(pending.command_run_id)
                changeset.transition_to(ChangesetStatus.APPLYING)
                task.transition_to(TaskStatus.EXECUTING)
                unit.state.save_changeset(changeset)
                unit.state.save_task(task)
                unit.commit()
            with pytest.raises(InvalidTransitionError, match="Resolve the current approval"):
                service.invoke("assistant.turns.steer", request)
            with service._unit_of_work_factory() as unit:
                snapshot = unit.workflows.get(UUID(turn["workflow_run_id"]))
                assert snapshot.run.active_plan_revision == 1
                assert not snapshot.instructions
                assert unit.state.get_changeset(changeset.id).status is ChangesetStatus.APPLYING
                assert unit.commands.get_run(pending.command_run_id).status == "running"
            assert len(provider.requests) == 4
            return
        if restart:
            service._workflow_scheduler.close()
            service._assistant_scheduler._ledger.steer_turn(
                **{**request, "turn_id": UUID(turn["id"])},
            )
            service.close()
            service = build_local_service(
                tmp_path / "data",
                provider_registry=ProviderRegistry((provider,)),
                assistant_workflow_engine_version=4,
                model_catalog_source=PricedCatalogSource(),
            )
        else:
            service.invoke("assistant.turns.steer", request)
        if new_plan:
            # WAITING_FOR_TOOL also covers brief ordinary tool execution; wait
            # for the durable second approval, not that transient Turn status.
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                approvals = service.invoke("approvals.list", {"task_id": turn["task_id"]})["items"]
                with service._unit_of_work_factory() as unit:
                    run = unit.workflows.get_run(UUID(turn["workflow_run_id"]))
                if len(approvals) == 2 and run.status == "waiting_for_approval":
                    break
                time.sleep(0.01)
            assert len(approvals) == 2
            assert run.status == "waiting_for_approval"
            revised = next(item for item in approvals if item["id"] != approval["id"])
            assert revised["decision"] == "pending"
            service.invoke("approvals.decide", {"approval_id": revised["id"], "approved": True})
        assert wait_for_turn(service, turn["id"], timeout_seconds=6)["status"] == "completed"
        with service._unit_of_work_factory() as unit:
            old_approval = unit.state.get_approval(UUID(approval["id"]))
            changeset = unit.state.get_changeset(old_approval.changeset_id)
            command = unit.commands.get_run(old_approval.command_run_id)
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
            plan = unit.state.get_execution_plan(original_plan_id)
            latest = unit.state.execution_plan_for_task(UUID(turn["task_id"]))
            trace = unit.assistant.list_trace_steps(UUID(turn["id"]))
            assert unit.assistant.get_turn(UUID(other["id"])).status == "created"
        assert old_approval.decision == ("approved" if approved else "rejected")
        assert changeset.status == "rejected"
        assert command.status in {"cancelled", "rejected"}
        assert plan.status == "cancelled"
        receipt = json.loads(invocations[2].model_content)
        assert receipt["status"] == "rejected"
        assert receipt["reason_code"] == "EXECUTION_INTENT_CHANGED"
        assert (source / "README.md").read_text(encoding="utf-8") == "base"
        managed = Path(context["target_version"]["project_root"]) / "README.md"
        assert managed.read_text(encoding="utf-8") == (revised_content if new_plan else "base")
        assert len(provider.requests) == (8 if new_plan else 6)
        assert [
            (step.kind.value, step.status.value, step.public_summary)
            for step in trace
            if step.status in {"pending", "running", "waiting"}
        ] == []
        assert "execution.plan" in {tool.name for tool in provider.requests[5].tools}
        if new_plan:
            assert latest.id != plan.id
            assert latest.generation == latest.workflow_plan_revision == 2
            assert [item.workflow_plan_revision for item in invocations] == [1, 1, 1, 2, 2]
            if repeat_arguments:
                assert invocations[1].argument_hash == invocations[3].argument_hash
                assert invocations[2].argument_hash == invocations[4].argument_hash
                assert invocations[2].command_run_id != invocations[4].command_run_id
        service.invoke("assistant.turns.steer", request)
        messages = service.invoke("messages.list", {"conversation_id": conversation["id"]})["items"]
        assert len([item for item in messages if item["role"] == "assistant"]) == 1
        if not approved:
            with pytest.raises(InvalidTransitionError):
                service.invoke(
                    "approvals.decide",
                    {
                        "approval_id": approval["id"],
                        "approved": True,
                    },
                )
        else:
            replayed = service.invoke(
                "approvals.decide",
                {
                    "approval_id": approval["id"],
                    "approved": True,
                },
            )
            assert replayed["resume_requested"] is False
            assert replayed["assistant_turn_id"] is None
    finally:
        service.close()
