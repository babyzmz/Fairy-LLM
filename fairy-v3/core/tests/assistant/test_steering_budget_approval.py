from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import wait_for_turn
from tests.assistant.test_model_routing import (
    DEEPSEEK_MODEL_ID,
    GLM_MODEL_ID,
    _auto_turn,
    _provider,
    _route_delta,
    _task,
)


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_new_requirements_do_not_inherit_unstarted_budget_approval(
    tmp_path, monkeypatch, approved, restart,
):
    profile = "openrouter-deepseek-v4-pro"
    provider = _provider(profile_id=profile, model_id=DEEPSEEK_MODEL_ID, rounds=[
        _route_delta(profile_id=profile), _route_delta(profile_id=profile),
        (
            ModelDelta.text(profile_id=profile, sequence=1, text="The revised explanation."),
            ModelDelta.done(profile_id=profile, sequence=2, finish_reason="stop"),
        ),
    ])
    reviewer = _provider(profile_id="openrouter-glm-5-2", model_id=GLM_MODEL_ID, rounds=[])
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider, reviewer)),
        assistant_workflow_engine_version=4,
    )
    try:
        task = _task(service, "Explain this task using the configured model")
        turn = _auto_turn(service, task, "budget-steer")
        other_task = _task(service, "Keep this conversation unchanged")
        other = _auto_turn(service, other_task, "other-budget-steer")
        assert service.invoke("assistant.turns.run", {"turn_id": turn["id"]})[
            "status"
        ] == "waiting_for_tool"
        original = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        if approved:
            with monkeypatch.context() as patch:
                patch.setattr(service._assistant_scheduler, "start", lambda *a, **k: None)
                service.invoke("approvals.decide", {
                    "approval_id": original["id"], "approved": True,
                })
        instruction = "Explain the revised task in one paragraph. Do not execute tools."
        update = {
            "turn_id": turn["id"], "instruction": instruction,
            "expected_revision": 1, "idempotency_key": "budget-revision-2",
        }
        service.invoke("assistant.turns.steer", update)
        service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        waiting = wait_for_turn(service, turn["id"], status="waiting_for_tool")
        approvals = service.invoke("approvals.list", {"task_id": task["id"]})["items"]
        assert len(approvals) == 2
        current = next(item for item in approvals if item["id"] != original["id"])
        assert waiting["budget_approval_run_id"] == current["command_run_id"]
        assert current["decision"] == "pending"
        assert len(provider.requests) == 2  # classification only; no execution model call
        if restart:
            service.close()
            service = build_local_service(
                tmp_path, provider_registry=ProviderRegistry((provider, reviewer)),
                assistant_workflow_engine_version=4,
            )
        service.invoke("assistant.turns.steer", update)
        with service._unit_of_work_factory() as unit:
            old = unit.state.get_approval(UUID(original["id"]))
            assert old.decision == ("approved" if approved else "rejected")
            assert unit.commands.get_run(old.command_run_id).status in {"cancelled", "rejected"}
            assert unit.assistant.get_turn(UUID(other["id"])).status == "created"
        if not approved:
            with pytest.raises(InvalidTransitionError):
                service.invoke("approvals.decide", {
                    "approval_id": original["id"], "approved": True,
                })
        else:
            replayed = service.invoke("approvals.decide", {
                "approval_id": original["id"], "approved": True,
            })
            assert replayed["resume_requested"] is False
            assert replayed["assistant_turn_id"] is None
        assert service.invoke("assistant.turns.get", {"turn_id": turn["id"]})[
            "status"
        ] == "waiting_for_tool"
        assert len(provider.requests) == 2
        service.invoke("approvals.decide", {"approval_id": current["id"], "approved": True})
        assert wait_for_turn(service, turn["id"])["status"] == "completed"
        assert len(provider.requests) == 3
        context = "\n".join(message.content for message in provider.requests[-1].messages)
        assert instruction in context
        messages = service.invoke("messages.list", {"conversation_id": task["conversation_id"]})[
            "items"
        ]
        assert len([item for item in messages if item["role"] == "assistant"]) == 1
        assert len(service.invoke("approvals.list", {"task_id": task["id"]})["items"]) == 2
    finally:
        service.close()
