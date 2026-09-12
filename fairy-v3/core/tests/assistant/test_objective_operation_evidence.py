from types import SimpleNamespace
from uuid import uuid4

import pytest

from fairy_core.assistant.workflow_objective_evidence import (
    objective_operation_receipts,
    operation_objective_issue,
)


def _case(action="run", tool="run.sandboxed"):
    turn = SimpleNamespace(
        id=uuid4(), task_id=uuid4(), conversation_id=uuid4(), workflow_run_id=uuid4()
    )
    run = SimpleNamespace(
        id=turn.workflow_run_id,
        engine_version=4,
        active_plan_revision=2,
        task_id=turn.task_id,
        conversation_id=turn.conversation_id,
    )
    intent = SimpleNamespace(
        active_objective_index=1,
        scope_digest="a" * 64,
        objectives=[None, SimpleNamespace(action=SimpleNamespace(value=action))],
        target_descriptions=(),
    )
    call = SimpleNamespace(
        workflow_run_id=run.id,
        workflow_plan_revision=2,
        workflow_objective_index=1,
        turn_id=turn.id,
        task_id=turn.task_id,
        scope_digest=intent.scope_digest,
        status="completed",
        error_code=None,
        command_run_id=uuid4(),
        tool_name=tool,
    )
    command = SimpleNamespace(
        status="succeeded",
        command_name=tool,
        task_id=turn.task_id,
        conversation_id=turn.conversation_id,
        scope_digest=intent.scope_digest,
    )
    unit = SimpleNamespace(
        workflows=SimpleNamespace(get_run=lambda _: run),
        commands=SimpleNamespace(get_run=lambda _: command),
    )
    return unit, turn, intent, call, command, run


@pytest.mark.parametrize(
    "action,tool",
    [
        ("run", "run.sandboxed"),
        ("generate", "media.images.generate"),
        ("browse", "browser.snapshot"),
        ("manage", "system.notify"),
    ],
)
def test_operation_objective_requires_its_governed_receipt(action, tool):
    unit, turn, intent, call, _, _ = _case(action, tool)
    assert operation_objective_issue(unit, turn, intent, []) is not None
    assert operation_objective_issue(unit, turn, intent, [call]) is None


@pytest.mark.parametrize(
    "owner,field,value",
    [
        ("call", "workflow_objective_index", 0),
        ("call", "workflow_plan_revision", 1),
        ("call", "workflow_run_id", uuid4()),
        ("call", "turn_id", uuid4()),
        ("call", "task_id", uuid4()),
        ("call", "scope_digest", "b" * 64),
        ("call", "status", "failed"),
        ("call", "error_code", "FAILED"),
        ("command", "status", "awaiting_approval"),
        ("command", "command_name", "browser.status"),
        ("command", "task_id", uuid4()),
        ("command", "conversation_id", uuid4()),
        ("command", "scope_digest", "b" * 64),
        ("run", "task_id", uuid4()),
        ("run", "conversation_id", uuid4()),
    ],
)
def test_old_failed_unapproved_or_foreign_operation_is_not_completion(owner, field, value):
    unit, turn, intent, call, command, run = _case()
    setattr({"call": call, "command": command, "run": run}[owner], field, value)
    assert objective_operation_receipts(unit, turn, intent, [call]) == ()
    assert operation_objective_issue(unit, turn, intent, [call]) is not None


def test_browser_status_is_not_a_research_operation_and_readonly_needs_no_mutation():
    unit, turn, intent, call, _, _ = _case("browse", "browser.status")
    assert operation_objective_issue(unit, turn, intent, [call]) is not None
    intent.objectives[1].action.value = "review"
    assert operation_objective_issue(unit, turn, intent, []) is None


def test_explicit_mcp_operation_is_supported_without_accepting_unrelated_receipts():
    unit, turn, intent, call, command, _ = _case("manage", "mcp.notes.update")
    intent.target_descriptions = ("mcp.notes.update",)
    assert operation_objective_issue(unit, turn, intent, [call]) is None
    call.tool_name = command.command_name = "mcp.other.read"
    assert operation_objective_issue(unit, turn, intent, [call]) is not None
