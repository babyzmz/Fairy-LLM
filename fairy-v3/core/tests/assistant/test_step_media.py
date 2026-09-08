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
from fairy_core.workflow.scheduler import WorkflowNodeResult, WorkflowPaused, WorkflowRetryableError
from tests.media import test_media_service as contracts
from tests.workflow.test_repository import _node, _run


def _create_media_turn(tmp_path, kind="image"):
    profile = "openrouter-deepseek-v4-pro"
    tool_name, model_id = {
        "image": ("media.images.generate", "google/gemini-3.1-flash-lite-image"),
        "music": ("media.audio.generate", "google/lyria-3-pro-preview"),
        "video": ("media.videos.start", "bytedance/seedance-2.0"),
    }[kind]
    goal = f"Create a Fairy {kind}"
    delta = contracts.ModelDelta
    coordinator = contracts.ScriptedProvider(
        [
            contracts._media_interpretation(profile, goal),
            (
                delta.tool_call(
                    profile_id=profile, sequence=1, tool_call_id="generate-once",
                    tool_name=tool_name,
                    arguments_fragment='{"prompt":"A translucent Fairy core"}',
                ),
                delta.done(profile_id=profile, sequence=2, finish_reason="tool_calls"),
            ),
            (
                delta.text(
                    profile_id=profile, sequence=1,
                    text="The image is ready." if kind == "image" else "Media request accepted.",
                ),
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
    if kind == "video":
        media.video_statuses.extend([contracts.MediaProviderVideoStatus.PENDING] * 20)
    service = contracts.build_local_service(
        tmp_path / "data", provider_registry=contracts.ProviderRegistry((coordinator,)),
        model_catalog_source=contracts.PricedCatalogSource(), media_provider=media,
    )
    try:
        service.invoke("models.catalog.refresh", {})
        current = service.invoke("models.selection.get", {})
        selection = service.invoke("models.selection.update", {
            "mode": "manual", "model_id": model_id, "allow_free_fallback": False,
            "zero_data_retention": False, "expected_revision": current["revision"],
            "idempotency_key": f"selection:{kind}",
        })
        task = contracts._scratch_task(service, goal)
        turn = service.invoke("assistant.turns.create", {
            "task_id": task["id"], "model_selection": {
                key: selection[key] for key in ("mode", "model_id", "revision")
            }, "idempotency_key": "turn:handoff",
        })
        return service, coordinator, media, turn, task
    except BaseException:
        service.close()
        raise


def _run_image_turn(tmp_path, expected_status="completed"):
    service, coordinator, media, turn, task = _create_media_turn(tmp_path)
    try:
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


def test_step_media_reopens_the_same_turn_after_archive_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    original_archive = MediaGenerationWorkflowAdapter._archive
    reached = Event()

    def interrupted_archive(adapter, job_id):
        with adapter._unit_of_work_factory() as unit:
            workflow = unit.workflows.get_by_owner(
                owner_kind="media_generation", owner_id=str(job_id), engine_version=1,
            )
            unit.workflows.request_pause(workflow.run.id)
            unit.workflows.request_pause(workflow.run.parent_run_id)
            unit.commit()
        reached.set()
        raise WorkflowPaused

    monkeypatch.setattr(MediaGenerationWorkflowAdapter, "_archive", interrupted_archive)
    first, coordinator, media, turn, task = _create_media_turn(tmp_path)
    try:
        first.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert reached.wait(5)
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with first._unit_of_work_factory() as unit:
                (job,) = unit.state.list_media_jobs(UUID(task["id"]))
                parent = unit.workflows.get_run(UUID(turn["workflow_run_id"]))
                domain = unit.workflows.get_by_owner(
                    owner_kind="media_generation", owner_id=str(job.id), engine_version=1,
                )
            if parent.status == domain.run.status == "paused":
                break
            sleep(0.02)
        assert parent.status == domain.run.status == "paused"
        assert job.status == "completed"
        assert len(media.image_requests) == 1
        assert len(coordinator.requests) == 2
    finally:
        first.close()

    monkeypatch.setattr(MediaGenerationWorkflowAdapter, "_archive", original_archive)
    next_provider = contracts.ScriptedProvider(
        list(coordinator.rounds), profile_id=coordinator.profile.id,
        model_id=coordinator.profile.model_id, capabilities=coordinator.profile.capabilities,
    )
    next_media = contracts.RecordingMediaProvider()
    restored = contracts.build_local_service(
        tmp_path / "data", provider_registry=contracts.ProviderRegistry((next_provider,)),
        model_catalog_source=contracts.PricedCatalogSource(), media_provider=next_media,
    )
    try:
        deadline = monotonic() + 6
        while monotonic() < deadline:
            completed = restored.invoke("assistant.turns.get", {"turn_id": turn["id"]})
            if completed["status"] in {"completed", "failed", "cancelled"}:
                break
            sleep(0.02)
        assert completed["status"] == "completed", completed.get("error_code")
        assert completed["workflow_run_id"] == turn["workflow_run_id"]
        with restored._unit_of_work_factory() as unit:
            (same_job,) = unit.state.list_media_jobs(UUID(task["id"]))
            assert same_job.id == job.id
            assert same_job.artifact_id == job.artifact_id
            assert unit.workflows.get_run(domain.run.id).status == "completed"
            (invocation,) = unit.assistant.list_tool_invocations(UUID(turn["id"]))
            assert invocation.status == "completed"
            assert invocation.command_run_id == job.command_run_id
        replies = restored.invoke("messages.list", {
            "conversation_id": task["conversation_id"],
        })["items"]
        assert [item["content"] for item in replies if item["role"] == "assistant"] == [
            "The image is ready.",
        ]
        assert len(next_provider.requests) == 1
        assert next_media.image_requests == []
    finally:
        restored.close()


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


@pytest.mark.parametrize("kind", ["music", "video"])
@pytest.mark.parametrize("approved", [False, True])
def test_step_music_and_video_keep_paid_approval_and_receipt_semantics(
    tmp_path, monkeypatch, kind, approved,
):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    service, coordinator, media, turn, task = _create_media_turn(tmp_path, kind)
    decided = set()
    tool_approvals = []
    try:
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        deadline = monotonic() + 8
        while monotonic() < deadline:
            for approval in service.invoke("approvals.list", {"task_id": task["id"]})["items"]:
                if approval["decision"] != "pending" or approval["id"] in decided:
                    continue
                assert media.music_requests == media.video_requests == []
                is_tool = approval["tool_invocation_id"] is not None
                if is_tool:
                    tool_approvals.append(approval["id"])
                service.invoke("approvals.decide", {
                    "approval_id": approval["id"], "approved": approved if is_tool else True,
                })
                decided.add(approval["id"])
            current = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
            if current["status"] in {"completed", "failed", "cancelled"}:
                break
            sleep(0.02)
        assert len(tool_approvals) == 1
        requests = media.music_requests if kind == "music" else media.video_requests
        if approved:
            assert current["status"] == "completed", current.get("error_code")
            assert len(requests) == 1
            assert len(coordinator.requests) == 3
            with service._unit_of_work_factory() as unit:
                (job,) = unit.state.list_media_jobs(UUID(task["id"]))
                (invocation,) = unit.assistant.list_tool_invocations(UUID(turn["id"]))
                domain = unit.workflows.get_by_owner(
                    owner_kind="media_generation", owner_id=str(job.id), engine_version=1,
                )
                assert invocation.status == "completed"
                assert invocation.command_run_id == job.command_run_id
                if kind == "music":
                    assert job.status == domain.run.status == "completed"
                    assert invocation.artifact_ids == (job.artifact_id,)
                else:
                    assert job.provider_job_id is not None
                    assert job.status in {"pending", "in_progress"}
                    assert domain.run.status not in {"completed", "failed", "cancelled"}
                    assert invocation.artifact_ids == ()
                    policy = next(
                        message.content for message in coordinator.requests[-1].messages
                        if "Do not call any tool or request" in message.content
                    )
                    assert "video job may still be pending" in policy
                    assert "output is already durable" not in policy
        else:
            assert current["status"] in {"failed", "cancelled"}
            assert requests == []
            assert len(coordinator.requests) == 2
    finally:
        service.close()
