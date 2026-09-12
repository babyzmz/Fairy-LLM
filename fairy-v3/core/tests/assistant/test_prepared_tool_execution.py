from __future__ import annotations

import json
from uuid import UUID

import pytest

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.models import ToolInvocation, ToolInvocationStatus
from fairy_core.providers import CancellationToken, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


def _prepare(service, key, *, notification=False):
    task = _scratch_task(service, "Notify me after approval" if notification else
                         "Search public sources for the latest Python release")
    turn = _turn(service, task, key)
    app = service._assistant_application
    app.prepare_turn(UUID(turn["id"]), CancellationToken())
    started, command = app._start_model_round(UUID(turn["id"]), 1)
    app._wait_for_tools(turn_id=started.id, run=command, candidate_count=1)
    invocation = ToolInvocation.create(
        turn=started, model_round=1, sequence=1, provider_call_id="call-search",
        tool_name="system.notify" if notification else "web.search",
        scope_digest=started.scope_digest,
        workflow_run_id=started.workflow_run_id if started.execution_engine_version == 4 else None,
        arguments=({"title": "Fairy", "body": "first", "level": "info"}
                   if notification else {"query": "Python release"}),
    )
    with service._unit_of_work_factory() as unit:
        unit.assistant.save_tool_invocation(invocation)
        unit.commit()
    return started, invocation


def _execute(service, turn, invocation, *, prepared_id=None):
    return service._assistant_application._execute_candidate(
        turn_id=turn.id,
        candidate=ToolCandidate(
            call_id=invocation.provider_call_id, name=invocation.tool_name,
            argument_fragments=[json.dumps(invocation.arguments)],
        ),
        model_round=invocation.model_round, sequence=invocation.sequence,
        cancellation=CancellationToken(),
        offered_definitions={invocation.tool_name: service._registry.get(invocation.tool_name)},
        prepared_invocation_id=prepared_id or invocation.id,
    )


def test_prepared_invocation_uses_original_identity_and_reserves_budget_once(tmp_path):
    executor = RecordingToolExecutor()
    provider = ScriptedProvider([])
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
    )
    try:
        turn, invocation = _prepare(service, "prepared-tool")
        waiting, message, error, images = _execute(service, turn, invocation)
        assert not waiting and message is not None and error is None and images == ()
        assert len(executor.calls) == 1
        assert provider.requests == []
        with service._unit_of_work_factory() as unit:
            saved = unit.assistant.list_tool_invocations(turn.id)
            snapshot = unit.workflows.get(turn.workflow_run_id)
        assert len(saved) == 1 and saved[0].id == invocation.id
        assert saved[0].status is ToolInvocationStatus.COMPLETED
        assert snapshot.run.tool_invocations_used == 1
        with pytest.raises(ValueError, match="prepared"):
            _execute(service, turn, invocation)
        assert len(executor.calls) == 1
    finally:
        service.close()


def test_prepared_identity_from_another_turn_cannot_be_executed(tmp_path):
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
        tool_executor=executor,
    )
    try:
        left, a = _prepare(service, "left")
        right, b = _prepare(service, "right")
        for turn, invocation, foreign in ((left, a, b.id), (right, b, a.id)):
            with pytest.raises(ValueError, match="prepared"):
                _execute(service, turn, invocation, prepared_id=foreign)
        assert executor.calls == []
        with service._unit_of_work_factory() as unit:
            assert unit.assistant.get_tool_invocation(a.id).status is ToolInvocationStatus.CREATED
            assert unit.assistant.get_tool_invocation(b.id).status is ToolInvocationStatus.CREATED
    finally:
        service.close()


def test_approval_resume_targets_one_prepared_invocation_without_running_its_sibling(tmp_path):
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
        tool_executor=executor,
    )
    try:
        turn, first = _prepare(service, "prepared-approval", notification=True)
        assert _execute(service, turn, first)[0] is True
        second = ToolInvocation.create(
            turn=turn, model_round=1, sequence=2, provider_call_id="call-second",
            tool_name="system.notify", scope_digest=turn.scope_digest,
            workflow_run_id=turn.workflow_run_id if turn.execution_engine_version == 4 else None,
            arguments={"title": "Fairy", "body": "second", "level": "info"},
        )
        with service._unit_of_work_factory() as unit:
            unit.assistant.save_tool_invocation(second)
            unit.commit()
        assert _execute(service, turn, second)[0] is True
        approvals = service.invoke("approvals.list", {"task_id": str(turn.task_id)})["items"]
        approval = next(item for item in approvals if item["tool_invocation_id"] == str(second.id))
        service._application.record_approval_decision(
            approval_id=UUID(approval["id"]), approved=True, decided_by="user",
        )
        waiting = service._assistant_application._resume_pending_tool(
            turn.id, CancellationToken(), invocation_id=second.id,
        )
        assert waiting is False
        assert [arguments["body"] for _, _, arguments in executor.calls] == ["second"]
        with service._unit_of_work_factory() as unit:
            first_status = unit.assistant.get_tool_invocation(first.id).status
            second_status = unit.assistant.get_tool_invocation(second.id).status
        assert first_status is ToolInvocationStatus.QUEUED
        assert second_status is ToolInvocationStatus.COMPLETED
    finally:
        service.close()
