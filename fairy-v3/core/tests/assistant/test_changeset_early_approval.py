import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest

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
from tests.assistant.test_steering_changeset_plan import _PROFILE, _call, _classification


@pytest.mark.parametrize("approved,hold_apply", [(True, False), (False, False), (True, True)])
@pytest.mark.parametrize("engine_version", [3, 4])
def test_early_changeset_decision_converges_receipt_and_trace(
    tmp_path,
    monkeypatch,
    approved,
    hold_apply,
    engine_version,
):
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
                    "files": [{"path": "README.md", "content": "updated"}],
                    "reason": "Update copy",
                },
            ),
            (
                ModelDelta.text(profile_id=_PROFILE, sequence=1, text="README.md was updated."),
                ModelDelta.done(profile_id=_PROFILE, sequence=2, finish_reason="stop"),
            ),
        ],
    )
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=engine_version,
        model_catalog_source=PricedCatalogSource(),
    )
    proposal_ready, release_receipt, applying, release_apply = (Event() for _ in range(4))
    apply_calls = []
    pending = []
    original_propose = service._application.propose_changeset
    original_apply = service._application._workspaces.apply_changeset

    def blocked_propose(request):
        result = original_propose(request)
        pending.append(result)
        proposal_ready.set()
        assert release_receipt.wait(8), "test must release proposal receipt"
        return result

    def controlled_apply(*args, **kwargs):
        apply_calls.append(1)
        if hold_apply:
            applying.set()
            assert release_apply.wait(8), "test must release file application"
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(service._application, "propose_changeset", blocked_propose)
    monkeypatch.setattr(service._application._workspaces, "apply_changeset", controlled_apply)
    pool = ThreadPoolExecutor(max_workers=1)
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
                "idempotency_key": "early-model",
            },
        )
        project = service.invoke(
            "projects.import",
            {
                "name": "Early approval",
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
                "idempotency_key": "early-task",
            },
        )
        turn = _auto_turn(service, context["task"], "early-approval")
        other = _auto_turn(service, _scratch_task(service, "Other conversation"), "early-other")
        service._assistant_scheduler.start(UUID(turn["id"]))
        assert proposal_ready.wait(6)
        approval_id = str(pending[0].approval.id)
        decision = pool.submit(
            service.invoke,
            "approvals.decide",
            {
                "approval_id": approval_id,
                "approved": approved,
            },
        )
        if hold_apply:
            assert applying.wait(6)
            release_receipt.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with service._unit_of_work_factory() as unit:
                    invocation = unit.assistant.list_tool_invocations(UUID(turn["id"]))[-1]
                    workflow = unit.workflows.get_run(UUID(turn["workflow_run_id"]))
                if workflow.status == "waiting_for_approval":
                    break
                time.sleep(0.01)
            assert invocation.status == "completed"
            assert workflow.status == "waiting_for_approval"
            assert len(provider.requests) == 4
            release_apply.set()
            decision.result(timeout=6)
        else:
            decision.result(timeout=6)
            release_receipt.set()
        completed = wait_for_turn(
            service,
            turn["id"],
            status="completed" if approved else "cancelled",
            timeout_seconds=6,
        )
        assert completed["status"] == ("completed" if approved else "cancelled")
        with service._unit_of_work_factory() as unit:
            invocation = unit.assistant.list_tool_invocations(UUID(turn["id"]))[-1]
            trace = unit.assistant.list_trace_steps(UUID(turn["id"]))
            assert unit.assistant.get_turn(UUID(other["id"])).status == "created"
        receipt = json.loads(invocation.model_content)
        assert receipt["status"] == ("applied" if approved else "rejected")
        assert [
            (step.kind.value, step.status.value)
            for step in trace
            if step.status in {"pending", "running", "waiting"}
        ] == []
        assert len(apply_calls) == int(approved)
        assert len(provider.requests) == (5 if approved else 4)
        messages = service.invoke("messages.list", {"conversation_id": conversation["id"]})["items"]
        assert len([item for item in messages if item["role"] == "assistant"]) == int(approved)
        assert (source / "README.md").read_text(encoding="utf-8") == "base"
        managed = Path(context["target_version"]["project_root"]) / "README.md"
        assert managed.read_text(encoding="utf-8") == ("updated" if approved else "base")
    finally:
        release_receipt.set()
        release_apply.set()
        pool.shutdown(wait=True, cancel_futures=True)
        service.close()
