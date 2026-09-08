import sqlite3
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest

from fairy_core.domain.errors import WorkerFenceError
from fairy_core.media.workflow import MediaGenerationWorkflowAdapter
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from fairy_core.workflow.scheduler import WorkflowNodeResult, WorkflowRetryableError
from tests.media import test_media_service as contracts
from tests.workflow.test_repository import _node, _run


def _run_image_turn(tmp_path, expected_status="completed"):
    profile = "openrouter-deepseek-v4-pro"
    delta = contracts.ModelDelta
    coordinator = contracts.ScriptedProvider(
        [
            contracts._media_interpretation(profile, "Create a Fairy image"),
            (
                delta.tool_call(
                    profile_id=profile, sequence=1, tool_call_id="generate-once",
                    tool_name="media.images.generate",
                    arguments_fragment='{"prompt":"A translucent Fairy core"}',
                ),
                delta.done(profile_id=profile, sequence=2, finish_reason="tool_calls"),
            ),
            (
                delta.text(profile_id=profile, sequence=1, text="The image is ready."),
                delta.done(profile_id=profile, sequence=2, finish_reason="stop"),
            ),
        ],
        profile_id=profile, model_id="deepseek/deepseek-v4-pro",
        capabilities=frozenset({
            contracts.ProviderCapability.TEXT, contracts.ProviderCapability.TOOLS,
            contracts.ProviderCapability.STRUCTURED_OUTPUT,
        }),
    )
    media = contracts.RecordingMediaProvider()
    service = contracts.build_local_service(
        tmp_path / "data", provider_registry=contracts.ProviderRegistry((coordinator,)),
        model_catalog_source=contracts.PricedCatalogSource(), media_provider=media,
    )
    try:
        selection = contracts._manual_image_selection(service)
        task = contracts._scratch_task(service, "Create a Fairy image")
        turn = service.invoke("assistant.turns.create", {
            "task_id": task["id"], "model_selection": {
                key: selection[key] for key in ("mode", "model_id", "revision")
            }, "idempotency_key": "turn:handoff",
        })
        if expected_status == "cancelled":
            service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
            deadline = monotonic() + 6
            while monotonic() < deadline:
                completed = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
                if completed["status"] == "cancelled" and not completed["cancellation_pending"]:
                    break
                sleep(0.02)
            assert completed["status"] == "cancelled"
            assert not completed["cancellation_pending"], "Cancelled Media must settle its Tool"
        else:
            completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert completed["status"] == expected_status, completed.get("error_code")
        messages = service.invoke("messages.list", {
            "conversation_id": task["conversation_id"],
        })["items"]
        expected_messages = [("user", "Create a Fairy image")]
        if expected_status == "completed":
            expected_messages.append(("assistant", "The image is ready."))
            assert len(media.image_requests) == 1
        assert [(item["role"], item["content"]) for item in messages] == expected_messages
        assert len(coordinator.requests) == (3 if expected_status == "completed" else 2)
        with service._unit_of_work_factory() as unit:
            jobs = unit.state.list_media_jobs(UUID(task["id"]))
            assert len(jobs) == 1
            workflow = unit.workflows.get_by_owner(
                owner_kind="media_generation", owner_id=str(jobs[0].id), engine_version=1,
            )
            assert str(workflow.run.parent_run_id) == turn["workflow_run_id"]
            assert workflow.run.status == expected_status
    finally:
        service.close()


@pytest.mark.parametrize("block_at", ["provider", "archive"])
@pytest.mark.parametrize("lost_handoff_receipt", [False, True])
def test_media_provider_wait_releases_parent_tool_worker(
    tmp_path, monkeypatch, block_at, lost_handoff_receipt,
):
    services = []
    build = contracts.build_local_service

    def create(*args, **kwargs):
        service = build(*args, **kwargs, assistant_workflow_engine_version=4)
        services.append(service)
        return service

    monkeypatch.setattr(contracts, "build_local_service", create)
    entered, release = Event(), Event()
    original = contracts.RecordingMediaProvider.generate_image
    original_archive = MediaGenerationWorkflowAdapter._archive
    original_defer = SqlAlchemyWorkflowRepository.defer
    lost = []

    def defer(repository, claim, **kwargs):
        node = repository.get_node(claim.run_id, claim.node_id)
        if lost_handoff_receipt and not lost and node.kind == "assistant.step.tool":
            lost.append(claim.node_id)
            raise WorkflowRetryableError("Injected lost parent handoff receipt")
        return original_defer(repository, claim, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "defer", defer)

    def blocked(self, request, cancellation):
        if block_at == "provider":
            entered.set()
            assert release.wait(8)
        return original(self, request, cancellation)

    def archive(adapter, job_id):
        if block_at == "archive":
            entered.set()
            assert release.wait(8)
        return original_archive(adapter, job_id)

    monkeypatch.setattr(contracts.RecordingMediaProvider, "generate_image", blocked)
    monkeypatch.setattr(MediaGenerationWorkflowAdapter, "_archive", archive)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_image_turn, tmp_path)
        try:
            assert entered.wait(5)
            deadline = monotonic() + 1
            while monotonic() < deadline:
                with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
                    (running,) = connection.execute(
                        "SELECT COUNT(*) FROM core_workflow_nodes "
                        "WHERE kind = 'assistant.step.tool' AND status = 'running'"
                    ).fetchone()
                if running == 0:
                    break
                sleep(0.02)
            assert running == 0, "Media domain work must not occupy its parent tool worker"
            service = services[0]
            assert not future.done(), "Assistant completion must wait for Media archive"
            with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
                (command_id,) = connection.execute(
                    "SELECT command_run_id FROM core_media_generation_jobs"
                ).fetchone()
            other_task = contracts._scratch_task(service, "An unrelated conversation")
            with service._unit_of_work_factory() as unit:
                command = unit.commands.get_run(UUID(command_id))
                other = unit.state.get_task(UUID(other_task["id"]))
                foreign_scope = service._application.scope_for_task(unit.state, other)
            with pytest.raises(WorkerFenceError):
                service._media_scheduler.read_handoff_result(foreign_scope, command)

            started = [Event() for _ in range(3)]
            runs = [_run(f"independent:{index}") for index in range(3)]
            nodes = [_node(run, str(index)) for index, run in enumerate(runs)]

            class Adapter:
                def execute(self, node, cancellation):
                    started[int(node.node_key)].set()
                    assert release.wait(5)
                    return WorkflowNodeResult(output={"done": True})

            service._workflow_adapters.register("test.echo", Adapter())
            with service._unit_of_work_factory() as unit:
                for run, node in zip(runs, nodes, strict=True):
                    unit.workflows.create(run, nodes=(node,), edges=())
                unit.commit()
            service._workflow_scheduler.wake()
            assert all(event.wait(2) for event in started), "All three remaining slots are usable"
        finally:
            release.set()
        future.result(timeout=10)
    assert bool(lost) is lost_handoff_receipt


@pytest.mark.parametrize("contract", [
    contracts.test_assistant_stops_after_the_first_media_provider_failure,
    contracts.test_assistant_does_not_reexpose_media_tool_after_success,
])
def test_step_engine_preserves_media_single_attempt_contract(tmp_path, monkeypatch, contract):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    contract(tmp_path)


def test_cancelling_a_deferred_media_tool_stops_the_original_domain_job(tmp_path, monkeypatch):
    services = []
    build = contracts.build_local_service
    entered, stopped, release = Event(), Event(), Event()

    def create(*args, **kwargs):
        service = build(*args, **kwargs, assistant_workflow_engine_version=4)
        services.append(service)
        return service

    def blocked(self, request, cancellation):
        entered.set()
        try:
            while not release.wait(0.02):
                cancellation.raise_if_cancelled()
            cancellation.raise_if_cancelled()
            raise AssertionError("Test must cancel the provider")
        finally:
            stopped.set()

    monkeypatch.setattr(contracts, "build_local_service", create)
    monkeypatch.setattr(contracts.RecordingMediaProvider, "generate_image", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_image_turn, tmp_path, "cancelled")
        try:
            assert entered.wait(4)
            service = services[0]
            with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
                (turn_id,) = connection.execute(
                    "SELECT turn_id FROM core_media_generation_jobs"
                ).fetchone()
            turn = service.invoke("assistant.turns.get", {"turn_id": turn_id})
            service.invoke("assistant.turns.cancel", {
                "turn_id": turn_id,
                "expected_cancellation_revision": turn["cancellation_revision"],
            })
            assert stopped.wait(2), "Cancellation must reach the existing domain worker"
            future.result(timeout=8)
        finally:
            release.set()
