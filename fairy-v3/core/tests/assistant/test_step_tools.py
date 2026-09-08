from __future__ import annotations

from threading import Event, Lock
from uuid import UUID

import pytest

from fairy_core.assistant.tools import ToolResult
from fairy_core.providers import ModelDelta, ModelRole, ProviderCapability, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from fairy_core.workflow.scheduler import WorkflowRetryableError
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_tool_image_handoff import _image


class _TwoReads:
    def __init__(self, *, images=False):
        self.lock = Lock()
        self.started = Event()
        self.release = Event()
        self.second_done = Event()
        self.calls = []
        self.completed = []
        self.images_enabled = images
        self.images = []

    def execute(self, definition, scope, arguments):
        query = arguments["query"]
        with self.lock:
            self.calls.append((scope.task_id, query))
            if len(self.calls) == 2:
                self.started.set()
        assert self.release.wait(5)
        if query == "first":
            assert self.second_done.wait(3)
        with self.lock:
            self.completed.append(query)
        if query == "second":
            self.second_done.set()
        images = (_image(scope.task_id, query.encode()),) if self.images_enabled else ()
        self.images.extend(images)
        return ToolResult.create(
            public_summary=query,
            model_content=f"Result for {query}",
            artifact_ids=(),
            images=images,
        )


def _provider():
    return ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="first",
                    tool_name="web.search",
                    arguments_fragment='{"query":"first"}',
                ),
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=2,
                    tool_call_id="second",
                    tool_name="web.search",
                    arguments_fragment='{"query":"second"}',
                ),
                ModelDelta.done(profile_id="scripted", sequence=3, finish_reason="tool_calls"),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Research finished"),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            ),
        ],
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.VISION,
            }
        ),
    )


@pytest.mark.parametrize("lost_kind", [None, "assistant.step.model", "assistant.step.tool"])
@pytest.mark.parametrize("with_images", [False, True])
def test_real_tool_nodes_release_model_worker_and_replay_only_durable_results(
    tmp_path,
    monkeypatch,
    lost_kind,
    with_images,
):
    executor = _TwoReads(images=with_images)
    provider = _provider()
    original_complete = SqlAlchemyWorkflowRepository.complete
    injected = []

    def complete(repository, claim, **kwargs):
        snapshot = repository.get(claim.run_id)
        node = next(item for item in snapshot.nodes if item.id == claim.node_id)
        if node.kind == lost_kind and not injected:
            assert node.result is not None
            injected.append(node.id)
            raise WorkflowRetryableError("Lost node completion receipt")
        return original_complete(repository, claim, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "complete", complete)
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
        assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Search two independent public sources")
        turn = _turn(service, task, "real-tools")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert executor.started.wait(3), "Both reads must run before either one finishes"
        with service._unit_of_work_factory() as unit:
            running = unit.workflows.get(UUID(turn["workflow_run_id"]))
        assert (
            sum(
                node.kind == "assistant.step.tool" and node.status == "running"
                for node in running.nodes
            )
            == 2
        )
        assert all(
            node.status == "succeeded"
            for node in running.nodes
            if node.kind == "assistant.step.model"
        )
        executor.release.set()
        wait_for_turn(service, turn["id"])
        done = service._workflow_scheduler.wait(UUID(turn["workflow_run_id"]), timeout=5)
        assert done.run.status == "completed"
        assert done.run.tool_invocations_used == 2
        assert len(executor.calls) == 2
        assert executor.completed == ["second", "first"]
        assert len(provider.requests) == 2
        messages = provider.requests[-1].messages
        tool_results = [item for item in messages if item.role is ModelRole.TOOL]
        assert [item.tool_call_id for item in tool_results] == ["first", "second"]
        assert "Result for first" in tool_results[0].content
        assert "Result for second" in tool_results[1].content
        if with_images:
            assert provider.observed_image_bytes == [b"first", b"second"]
            assert all(not any(image.data) for image in executor.images)
        else:
            assert provider.observed_image_bytes == []
        if lost_kind:
            assert len(injected) == 1
        with service._unit_of_work_factory() as unit:
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
        assert len(invocations) == 2
        assert all(invocation.task_id == UUID(task["id"]) for invocation in invocations)
    finally:
        executor.release.set()
        executor.second_done.set()
        service.close()
