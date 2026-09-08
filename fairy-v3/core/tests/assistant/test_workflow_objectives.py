import hashlib
import time
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import update

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.storage.schema import workflow_nodes, workflow_runs
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


def _text(value):
    return (
        ModelDelta.text(profile_id=_PROFILE, sequence=1, text=value),
        ModelDelta.done(profile_id=_PROFILE, sequence=2, finish_reason="stop"),
    )


@pytest.mark.parametrize(
    "premature_analysis,restart", [(False, False), (True, False), (False, True)]
)
def test_review_then_change_uses_real_objective_boundaries_and_one_final_reply(
    tmp_path,
    premature_analysis,
    restart,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
    analysis = "README contains only base. The requested change replaces that placeholder copy."
    provider = _provider(
        profile_id=_PROFILE,
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _classification(
                "change",
                objectives=[
                    {"goal": "Read and explain README.md", "action": "review", "depends_on": []},
                    {
                        "goal": "Apply the requested README.md fix",
                        "action": "change",
                        "depends_on": [0],
                    },
                ],
            ),
            *([_text("I have analyzed the file; proceed to edits.")] if premature_analysis else []),
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
            _text(analysis),
            _call(
                "edit.propose_changeset",
                {
                    "files": [{"path": "README.md", "content": "updated"}],
                    "reason": "Update copy",
                },
            ),
            _text("README.md was updated."),
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
                "idempotency_key": "model",
            },
        )
        project = service.invoke(
            "projects.import",
            {
                "name": "Compound request",
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
                "user_request": (
                    "First read and explain README.md, then replace its copy as requested."
                ),
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "compound-task",
            },
        )
        turn = _auto_turn(service, context["task"], "compound-turn")
        other = _auto_turn(service, _scratch_task(service, "Other conversation"), "other-turn")
        service._assistant_scheduler.start(UUID(turn["id"]))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            approvals = service.invoke("approvals.list", {"task_id": turn["task_id"]})["items"]
            if approvals:
                break
            time.sleep(0.01)
        assert len(approvals) == 1
        offset = int(premature_analysis)
        assert "edit.propose_changeset" not in {
            tool.name for request in provider.requests[1 : 4 + offset] for tool in request.tools
        }
        assert "edit.propose_changeset" in {
            tool.name for tool in provider.requests[4 + offset].tools
        }
        managed = Path(context["target_version"]["project_root"]) / "README.md"
        assert managed.read_text(encoding="utf-8") == "base"
        with service._unit_of_work_factory() as unit:
            intent = unit.assistant.get_execution_intent(UUID(turn["id"]))
            assert intent.active_objective_index == 1
            assert "active_objective_index" not in intent.model_dump(mode="json")
            assert unit.assistant.get_execution_intent(UUID(other["id"])) is None
        if premature_analysis:
            assert any(
                "needs actual current file contents" in message.content
                for message in provider.requests[2].messages
            )
        if restart:
            service.close()
            service = build_local_service(
                tmp_path / "data",
                provider_registry=ProviderRegistry((provider,)),
                assistant_workflow_engine_version=4,
                model_catalog_source=PricedCatalogSource(),
            )
            with service._unit_of_work_factory() as unit:
                assert (
                    unit.assistant.get_execution_intent(
                        UUID(turn["id"]),
                    ).active_objective_index
                    == 1
                )
        service.invoke("approvals.decide", {"approval_id": approvals[0]["id"], "approved": True})
        assert wait_for_turn(service, turn["id"])["status"] == "completed"
        with service._unit_of_work_factory() as unit:
            snapshot = unit.workflows.get(UUID(turn["workflow_run_id"]))
            assert unit.assistant.get_turn(UUID(other["id"])).status == "created"
        completed = [node for node in snapshot.nodes if node.kind == "assistant.objective.complete"]
        assert len(completed) == 2
        assert all(node.status == "succeeded" for node in completed)
        assert sorted(node.payload["objective_index"] for node in completed) == [0, 1]
        messages = service.invoke("messages.list", {"conversation_id": conversation["id"]})["items"]
        assert [m["content"] for m in messages if m["role"] == "assistant"] == [
            "README.md was updated."
        ]
        assert (source / "README.md").read_text(encoding="utf-8") == "base"
        assert managed.read_text(encoding="utf-8") == "updated"
        assert len(provider.requests) == 6 + offset
        assert analysis in "\n".join(m.content for m in provider.requests[-1].messages)
        # Corrupt progress must not silently fall back to the root write capability.
        first = next(node for node in completed if node.payload["objective_index"] == 0)
        engine = service._unit_of_work_factory._engine
        for field, bad_value in (("scope_digest", "0" * 64), ("interpretation_revision", 99)):
            with engine.begin() as connection:
                connection.execute(
                    update(workflow_nodes)
                    .where(
                        workflow_nodes.c.id == str(first.id),
                    )
                    .values(result={**first.result, field: bad_value})
                )
            with service._unit_of_work_factory() as unit, pytest.raises(InvalidTransitionError):
                unit.assistant.get_execution_intent(UUID(turn["id"]))
            with engine.begin() as connection:
                connection.execute(
                    update(workflow_nodes)
                    .where(
                        workflow_nodes.c.id == str(first.id),
                    )
                    .values(result=dict(first.result))
                )
        with engine.begin() as connection:
            connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.id == turn["workflow_run_id"],
                )
                .values(conversation_id=other["conversation_id"])
            )
        with service._unit_of_work_factory() as unit, pytest.raises(InvalidTransitionError):
            unit.assistant.get_execution_intent(UUID(turn["id"]))
        with engine.begin() as connection:
            connection.execute(
                update(workflow_runs)
                .where(
                    workflow_runs.c.id == turn["workflow_run_id"],
                )
                .values(conversation_id=turn["conversation_id"])
            )
    finally:
        service.close()
