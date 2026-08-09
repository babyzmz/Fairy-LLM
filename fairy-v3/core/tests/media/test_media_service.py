from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from uuid import UUID

import pytest

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.media.invariants import MediaJobFailedError
from fairy_core.media.ports import (
    GeneratedMedia,
    ImageGenerationRequest,
    MediaProviderVideoStatus,
    MusicGenerationRequest,
    VideoGenerationRequest,
    VideoProviderJob,
)
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ProviderCancelledError,
    ProviderCapability,
    ProviderProtocolError,
    ProviderRegistry,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_model_routing import PricedCatalogSource


def _media_interpretation(profile_id: str, goal: str) -> tuple[ModelDelta, ...]:
    return (
        ModelDelta.text(
            profile_id=profile_id,
            sequence=1,
            text=json.dumps(
                {
                    "evidence_requirements": [],
                    "requires_workspace_changes": False,
                    "public_summary": goal,
                    "interpretation": {
                        "normalized_goal": goal,
                        "action": "generate",
                        "objectives": [
                            {
                                "goal": goal,
                                "action": "generate",
                                "depends_on": [],
                            }
                        ],
                        "targets": [],
                        "constraints": [],
                        "deliverable": "Generated image",
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
        ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop"),
    )


class RecordingMediaProvider:
    def __init__(self) -> None:
        self.image_requests: list[ImageGenerationRequest] = []
        self.music_requests: list[MusicGenerationRequest] = []
        self.video_requests: list[VideoGenerationRequest] = []
        self.video_statuses: list[MediaProviderVideoStatus] = []
        self.execution_threads: list[str] = []
        self.video_poll_calls = 0
        self.closed = False

    @property
    def account_id(self) -> str:
        return "openrouter-default"

    @property
    def credential_configured(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True

    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        cancellation.raise_if_cancelled()
        self.execution_threads.append(current_thread().name)
        self.image_requests.append(request)
        return GeneratedMedia(
            content=b"\x89PNG\r\n\x1a\nfairy-image",
            media_type="image/png",
            usage_cost="0.04",
        )

    def generate_music(
        self,
        request: MusicGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        cancellation.raise_if_cancelled()
        self.execution_threads.append(current_thread().name)
        self.music_requests.append(request)
        return GeneratedMedia(
            content=b"RIFFfairy-music-WAVE",
            media_type="audio/wav",
            usage_cost="0.12",
            transcript="Instrumental music",
        )

    def start_video(
        self,
        request: VideoGenerationRequest,
        cancellation: CancellationToken,
    ) -> VideoProviderJob:
        cancellation.raise_if_cancelled()
        self.execution_threads.append(current_thread().name)
        self.video_requests.append(request)
        return VideoProviderJob(
            provider_job_id=f"video-{len(self.video_requests)}",
            status=MediaProviderVideoStatus.PENDING,
            usage_cost="0.20",
        )

    def get_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> VideoProviderJob:
        cancellation.raise_if_cancelled()
        self.execution_threads.append(current_thread().name)
        self.video_poll_calls += 1
        assert provider_job_id.startswith("video-")
        return VideoProviderJob(
            provider_job_id=provider_job_id,
            status=self.video_statuses.pop(0),
            usage_cost="0.30",
        )

    def download_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        cancellation.raise_if_cancelled()
        assert provider_job_id.startswith("video-")
        return GeneratedMedia(
            content=b"\x00\x00\x00\x18ftypisomfairy-video",
            media_type="video/mp4",
        )


class BlockingImageMediaProvider(RecordingMediaProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()

    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        self.execution_threads.append(current_thread().name)
        self.image_requests.append(request)
        self.started.set()
        while True:
            cancellation.raise_if_cancelled()
            sleep(0.01)


class JpegImageMediaProvider(RecordingMediaProvider):
    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        cancellation.raise_if_cancelled()
        self.image_requests.append(request)
        return GeneratedMedia(
            content=b"\xff\xd8\xfffairy-jpeg\xff\xd9",
            media_type="image/jpeg",
            usage_cost="0.04",
        )


class FailingImageMediaProvider(RecordingMediaProvider):
    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        cancellation.raise_if_cancelled()
        self.image_requests.append(request)
        raise ProviderProtocolError("image endpoint returned an unusable response")


class LateCompletingVideoProvider(RecordingMediaProvider):
    def __init__(self) -> None:
        super().__init__()
        self.poll_started = Event()
        self.poll_release = Event()

    def get_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> VideoProviderJob:
        self.execution_threads.append(current_thread().name)
        self.video_poll_calls += 1
        self.poll_started.set()
        assert self.poll_release.wait(timeout=3)
        return VideoProviderJob(
            provider_job_id=provider_job_id,
            status=MediaProviderVideoStatus.COMPLETED,
            usage_cost="0.30",
        )

    def download_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        return GeneratedMedia(
            content=b"\x00\x00\x00\x18ftypisomlate-video",
            media_type="video/mp4",
        )


def _scratch_task(service, request: str = "Generate media") -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": request,
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": f"task:{conversation['id']}",
        },
    )["task"]


def _manual_image_selection(service) -> dict[str, object]:
    service.invoke("models.catalog.refresh", {})
    current = service.invoke("models.selection.get", {})
    return service.invoke(
        "models.selection.update",
        {
            "mode": "manual",
            "model_id": "google/gemini-3.1-flash-lite-image",
            "allow_free_fallback": False,
            "zero_data_retention": False,
            "expected_revision": current["revision"],
            "idempotency_key": "selection:image",
        },
    )


def _wait_for_media_job(service, task_id: str, job_id: str, *, timeout: float = 3.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        jobs = service.invoke("media.jobs.list", {"task_id": task_id})["items"]
        job = next(item for item in jobs if item["id"] == job_id)
        if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return job
        sleep(0.01)
    raise AssertionError(f"media job {job_id} did not become terminal")


def test_image_generation_persists_workspace_artifact_events_and_replays(
    tmp_path: Path,
) -> None:
    provider = RecordingMediaProvider()
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service)
        request = {
            "task_id": task["id"],
            "prompt": "A translucent Fairy core",
            "idempotency_key": "media:image:fairy",
        }

        created = service.invoke("media.images.generate", request)
        replayed = service.invoke("media.images.generate", request)
        files = service.invoke(
            "workspaces.files.list",
            {
                "workspace_id": task["workspace_id"],
                "version_id": task["target_version_id"],
            },
        )
        artifacts = service.invoke("artifacts.list", {"task_id": task["id"]})["items"]
        jobs = service.invoke("media.jobs.list", {"task_id": task["id"]})["items"]
        media_events = [
            event
            for event in service.invoke("events.subscribe", {"cursor": 0})["items"]
            if event["event_type"].startswith("media.generation.")
        ]

        assert created == replayed
        assert jobs == [created]
        assert created["status"] == "completed"
        assert created["usage_cost"] == "0.04"
        assert [item["path"] for item in files["items"]] == [created["output_path"]]
        assert str(created["output_path"]).startswith("generated/image-")
        assert [(item["artifact_type"], item["media_type"]) for item in artifacts] == [
            ("generated_image", "image/png")
        ]
        assert [event["event_type"] for event in media_events] == [
            "media.generation.started",
            "media.generation.completed",
        ]
        assert all("prompt" not in event["payload"] for event in media_events)
        assert len(provider.image_requests) == 1
        assert provider.execution_threads == ["fairy-workflow_0"]
        assert (
            service._media_scheduler._workflow_scheduler  # type: ignore[attr-defined]
            is service._workflow_scheduler  # type: ignore[attr-defined]
        )
        assert (
            service._knowledge_sync_scheduler._workflow_scheduler  # type: ignore[attr-defined]
            is service._workflow_scheduler  # type: ignore[attr-defined]
        )
        with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
            workflow = connection.execute(
                """
                SELECT owner_kind, owner_id, status
                FROM core_workflow_runs
                WHERE tenant_id = 'local' AND owner_kind = 'media_generation'
                """
            ).fetchone()
            workflow_nodes = connection.execute(
                """
                SELECT node_key, status
                FROM core_workflow_nodes
                WHERE tenant_id = 'local' AND run_id = (
                    SELECT id FROM core_workflow_runs
                    WHERE tenant_id = 'local' AND owner_kind = 'media_generation'
                )
                ORDER BY CASE node_key
                    WHEN 'submit' THEN 1 WHEN 'wait' THEN 2
                    WHEN 'poll' THEN 3 ELSE 4 END
                """
            ).fetchall()
            legacy_queue_count = connection.execute(
                "SELECT COUNT(*) FROM core_media_generation_work WHERE tenant_id = 'local'"
            ).fetchone()
        assert workflow == ("media_generation", created["id"], "completed")
        assert workflow_nodes == [
            ("submit", "succeeded"),
            ("wait", "succeeded"),
            ("poll", "succeeded"),
            ("archive", "succeeded"),
        ]
        assert legacy_queue_count == (0,)

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "media.images.generate",
                {**request, "prompt": "A different image"},
            )
    finally:
        service.close()
    assert provider.closed is True


def test_default_image_path_adapts_to_the_provider_media_type(tmp_path: Path) -> None:
    provider = JpegImageMediaProvider()
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service)

        created = service.invoke(
            "media.images.generate",
            {
                "task_id": task["id"],
                "prompt": "A translucent Fairy core",
                "idempotency_key": "media:image:jpeg-default",
            },
        )
        files = service.invoke(
            "workspaces.files.list",
            {
                "workspace_id": task["workspace_id"],
                "version_id": task["target_version_id"],
            },
        )["items"]

        assert created["status"] == "completed"
        assert str(created["output_path"]).endswith(".jpg")
        assert [item["path"] for item in files] == [created["output_path"]]
    finally:
        service.close()


def test_image_idempotency_accepts_the_pre_output_path_auto_fingerprint(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    provider = RecordingMediaProvider()
    service = build_local_service(data_dir, media_provider=provider)
    try:
        task = _scratch_task(service)
        request = {
            "task_id": task["id"],
            "prompt": "A translucent Fairy core",
            "idempotency_key": "media:image:legacy-fingerprint",
        }
        created = service.invoke("media.images.generate", request)

        with sqlite3.connect(data_dir / "core.db") as connection:
            row = connection.execute(
                """
                SELECT scope_digest, kind, model_id, endpoint_kind, output_path, request_spec
                FROM core_media_generation_jobs
                WHERE tenant_id = 'local' AND id = ?
                """,
                (created["id"],),
            ).fetchone()
            assert row is not None
            request_spec = json.loads(row[5])
            request_spec.pop("output_path_auto")
            payload = {
                "scope_digest": row[0],
                "kind": row[1],
                "model_id": row[2],
                "endpoint_kind": row[3],
                "output_path": row[4],
                "request_spec": request_spec,
            }
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            legacy_fingerprint = hashlib.sha256(encoded).hexdigest()
            connection.execute(
                """
                UPDATE core_media_generation_jobs
                SET request_fingerprint = ?
                WHERE tenant_id = 'local' AND id = ?
                """,
                (legacy_fingerprint, created["id"]),
            )

        replayed = service.invoke("media.images.generate", request)

        assert replayed["id"] == created["id"]
        assert len(provider.image_requests) == 1
    finally:
        service.close()


def test_explicit_image_path_keeps_strict_media_type_validation(tmp_path: Path) -> None:
    provider = JpegImageMediaProvider()
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service)

        with pytest.raises(MediaJobFailedError) as failure:
            service.invoke(
                "media.images.generate",
                {
                    "task_id": task["id"],
                    "prompt": "A translucent Fairy core",
                    "output_path": "generated/strict.png",
                    "idempotency_key": "media:image:jpeg-strict",
                },
            )
        assert failure.value.error_code == "PROVIDER_PROTOCOL_ERROR"

        jobs = service.invoke("media.jobs.list", {"task_id": task["id"]})["items"]
        assert len(jobs) == 1
        assert jobs[0]["status"] == "failed"
        assert jobs[0]["error_code"] == "PROVIDER_PROTOCOL_ERROR"
    finally:
        service.close()


def test_video_worker_completes_without_client_poll(tmp_path: Path) -> None:
    provider = RecordingMediaProvider()
    provider.video_statuses.extend(
        [MediaProviderVideoStatus.IN_PROGRESS, MediaProviderVideoStatus.COMPLETED]
    )
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service, "Generate an autonomous video")
        started = service.invoke(
            "media.videos.start",
            {
                "task_id": task["id"],
                "prompt": "A Fairy core forming from glass",
                "idempotency_key": "media:video:background",
                "user_confirmed": True,
            },
        )

        completed = _wait_for_media_job(service, task["id"], started["id"])

        assert started["status"] in {"pending", "in_progress"}
        assert completed["status"] == "completed"
        assert completed["artifact_ids"]
        assert provider.video_poll_calls == 2
        assert all(name.startswith("fairy-workflow") for name in provider.execution_threads)
        with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
            workflow_run_id = connection.execute(
                """
                SELECT id FROM core_workflow_runs
                WHERE tenant_id = 'local' AND owner_kind = 'media_generation'
                """
            ).fetchone()
        assert workflow_run_id is not None
        service._workflow_scheduler.wait(  # type: ignore[attr-defined]
            UUID(workflow_run_id[0]),
            timeout=5,
        )
        with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
            node_attempts = connection.execute(
                """
                SELECT node_key, attempt_count
                FROM core_workflow_nodes
                WHERE tenant_id = 'local' AND run_id = (
                    SELECT id FROM core_workflow_runs
                    WHERE tenant_id = 'local' AND owner_kind = 'media_generation'
                )
                ORDER BY CASE node_key
                    WHEN 'submit' THEN 1 WHEN 'wait' THEN 2
                    WHEN 'poll' THEN 3 ELSE 4 END
                """
            ).fetchall()
        assert node_attempts == [
            ("submit", 1),
            ("wait", 2),
            ("poll", 2),
            ("archive", 1),
        ]
    finally:
        service.close()


def test_media_jobs_remain_task_scoped_after_core_restart(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    service = build_local_service(data_root, media_provider=RecordingMediaProvider())
    try:
        task_a = _scratch_task(service, request="Generate output A")
        task_b = _scratch_task(service, request="Generate output B")
        job_a = service.invoke(
            "media.images.generate",
            {
                "task_id": task_a["id"],
                "prompt": "Output A",
                "idempotency_key": "media:image:scope-a",
            },
        )
        job_b = service.invoke(
            "media.images.generate",
            {
                "task_id": task_b["id"],
                "prompt": "Output B",
                "idempotency_key": "media:image:scope-b",
            },
        )
    finally:
        service.close()

    reopened = build_local_service(data_root, media_provider=RecordingMediaProvider())
    try:
        listed_a = reopened.invoke("media.jobs.list", {"task_id": task_a["id"]})["items"]
        listed_b = reopened.invoke("media.jobs.list", {"task_id": task_b["id"]})["items"]
        assert [item["id"] for item in listed_a] == [job_a["id"]]
        assert [item["id"] for item in listed_b] == [job_b["id"]]
        assert listed_a[0]["conversation_id"] != listed_b[0]["conversation_id"]
    finally:
        reopened.close()


def test_interrupted_image_worker_reclaims_the_same_job_after_core_restart(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    blocking = BlockingImageMediaProvider()
    first = build_local_service(data_root, media_provider=blocking)
    task = _scratch_task(first, "Resume one image after restart")
    errors: list[BaseException] = []

    def invoke_image() -> None:
        try:
            first.invoke(
                "media.images.generate",
                {
                    "task_id": task["id"],
                    "prompt": "A durable glass Fairy",
                    "idempotency_key": "media:image:restart",
                },
            )
        except BaseException as error:
            errors.append(error)

    request_thread = Thread(target=invoke_image, name="media-request")
    request_thread.start()
    assert blocking.started.wait(timeout=2)
    first.close()
    request_thread.join(timeout=2)

    assert not request_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ProviderCancelledError)

    recovered_provider = RecordingMediaProvider()
    reopened = build_local_service(data_root, media_provider=recovered_provider)
    try:
        with reopened._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            jobs = unit_of_work.state.list_media_jobs(UUID(task["id"]))
        assert len(jobs) == 1

        completed = _wait_for_media_job(reopened, task["id"], str(jobs[0].id))

        assert completed["status"] == "completed"
        assert completed["artifact_ids"]
        assert len(blocking.image_requests) == 1
        assert len(recovered_provider.image_requests) == 1
        assert recovered_provider.image_requests[0].idempotency_key == (
            blocking.image_requests[0].idempotency_key
        )
    finally:
        reopened.close()


def test_pending_video_resumes_polling_after_core_restart(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    first_provider = RecordingMediaProvider()
    first = build_local_service(data_root, media_provider=first_provider)
    task = _scratch_task(first, "Resume one video after restart")
    started = first.invoke(
        "media.videos.start",
        {
            "task_id": task["id"],
            "prompt": "A durable Fairy animation",
            "idempotency_key": "media:video:restart",
            "user_confirmed": True,
        },
    )
    first.close()

    recovered_provider = RecordingMediaProvider()
    recovered_provider.video_statuses.append(MediaProviderVideoStatus.COMPLETED)
    reopened = build_local_service(data_root, media_provider=recovered_provider)
    try:
        completed = _wait_for_media_job(reopened, task["id"], started["id"])

        assert completed["status"] == "completed"
        assert completed["artifact_ids"]
        assert recovered_provider.video_requests == []
        assert recovered_provider.video_poll_calls == 1
    finally:
        reopened.close()


def test_video_cancel_wins_over_a_late_provider_completion(tmp_path: Path) -> None:
    provider = LateCompletingVideoProvider()
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service, "Cancel a late video")
        started = service.invoke(
            "media.videos.start",
            {
                "task_id": task["id"],
                "prompt": "A video that finishes after cancellation",
                "idempotency_key": "media:video:late-cancel",
                "user_confirmed": True,
            },
        )
        assert provider.poll_started.wait(timeout=3)
        current = service.invoke("media.videos.get", {"job_id": started["id"]})

        cancelled = service.invoke(
            "media.videos.cancel",
            {
                "job_id": started["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "media:video:late-cancel:confirm",
                "user_confirmed": True,
            },
        )
        provider.poll_release.set()
        sleep(0.2)
        projected = service.invoke("media.videos.get", {"job_id": started["id"]})
        files = service.invoke(
            "workspaces.files.list",
            {
                "workspace_id": task["workspace_id"],
                "version_id": task["target_version_id"],
            },
        )["items"]
        artifacts = service.invoke("artifacts.list", {"task_id": task["id"]})["items"]
        physical_output = (
            service._media_application._workspaces.version_path(  # type: ignore[attr-defined]
                UUID(task["workspace_id"]),
                UUID(task["target_version_id"]),
            )
            / started["output_path"]
        )

        assert cancelled["status"] == projected["status"] == "cancelled"
        assert files == []
        assert artifacts == []
        assert not physical_output.exists()
        assert provider.video_poll_calls == 1
    finally:
        provider.poll_release.set()
        service.close()


def test_music_requires_confirmation_and_remains_separate_from_voice(
    tmp_path: Path,
) -> None:
    provider = RecordingMediaProvider()
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service, "Generate music")
        request = {
            "task_id": task["id"],
            "prompt": "Calm glass harmonics",
            "idempotency_key": "media:music:theme",
        }

        with pytest.raises(ApprovalRequiredError):
            service.invoke("media.audio.generate", {**request, "user_confirmed": False})
        assert provider.music_requests == []

        generated = service.invoke(
            "media.audio.generate",
            {**request, "user_confirmed": True},
        )
        replayed = service.invoke(
            "media.audio.generate",
            {**request, "user_confirmed": True},
        )

        assert generated == replayed
        assert generated["kind"] == "music"
        assert generated["status"] == "completed"
        assert str(generated["output_path"]).startswith("generated/music-")
        assert len(provider.music_requests) == 1
        assert provider.music_requests[0].model_id == "google/lyria-3-pro-preview"
    finally:
        service.close()


def test_video_background_download_read_only_get_and_local_cancel_are_durable(
    tmp_path: Path,
) -> None:
    provider = RecordingMediaProvider()
    provider.video_statuses.extend(
        [MediaProviderVideoStatus.IN_PROGRESS, MediaProviderVideoStatus.COMPLETED]
    )
    service = build_local_service(tmp_path / "data", media_provider=provider)
    try:
        task = _scratch_task(service, "Generate video")
        request = {
            "task_id": task["id"],
            "prompt": "Fairy glass forming into a ring",
            "idempotency_key": "media:video:fairy",
            "user_confirmed": True,
        }
        started = service.invoke("media.videos.start", request)
        replayed_start = service.invoke("media.videos.start", request)
        poll_calls = provider.video_poll_calls
        projected_a = service.invoke("media.videos.get", {"job_id": started["id"]})
        projected_b = service.invoke("media.videos.get", {"job_id": started["id"]})
        completed = _wait_for_media_job(service, task["id"], started["id"])

        assert started["status"] == "pending"
        assert started == replayed_start
        assert str(started["output_path"]).startswith("generated/video-")
        assert projected_a == projected_b == started
        assert poll_calls == 0
        assert provider.video_poll_calls == 2
        assert completed["status"] == "completed"
        assert completed["usage_cost"] == "0.30"
        assert completed["artifact_ids"]

        cancellable = service.invoke(
            "media.videos.start",
            {
                "task_id": task["id"],
                "prompt": "A second animation",
                "output_path": "generated/cancelled.mp4",
                "idempotency_key": "media:video:cancelled",
                "user_confirmed": True,
            },
        )
        cancel_request = {
            "job_id": cancellable["id"],
            "expected_revision": cancellable["revision"],
            "idempotency_key": "media:video:cancel",
            "user_confirmed": True,
        }
        cancelled = service.invoke("media.videos.cancel", cancel_request)
        replayed = service.invoke("media.videos.cancel", cancel_request)

        assert cancelled == replayed
        assert cancelled["status"] == "cancelled"
        assert len(provider.video_requests) == 2
    finally:
        service.close()


def test_manual_image_model_uses_deepseek_coordinator_and_one_assistant_message(
    tmp_path: Path,
) -> None:
    profile_id = "openrouter-deepseek-v4-pro"
    coordinator = ScriptedProvider(
        [
            _media_interpretation(profile_id, "Create a Fairy image"),
            (
                ModelDelta.tool_call(
                    profile_id=profile_id,
                    sequence=1,
                    tool_call_id="generate-fairy",
                    tool_name="media.images.generate",
                    arguments_fragment=(
                        '{"prompt":"A translucent Fairy core","output_path":'
                        '"generated/assistant-fairy.png"}'
                    ),
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id=profile_id,
                    sequence=1,
                    text="The image is ready in your Workspace.",
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
        profile_id=profile_id,
        model_id="deepseek/deepseek-v4-pro",
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        ),
    )
    media = RecordingMediaProvider()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((coordinator,)),
        model_catalog_source=PricedCatalogSource(),
        media_provider=media,
    )
    try:
        service.invoke("models.catalog.refresh", {})
        current = service.invoke("models.selection.get", {})
        selection = service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": "google/gemini-3.1-flash-lite-image",
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": current["revision"],
                "idempotency_key": "selection:image",
            },
        )
        task = _scratch_task(service, "Create a Fairy image")
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "model_selection": {
                    "mode": selection["mode"],
                    "model_id": selection["model_id"],
                    "revision": selection["revision"],
                },
                "idempotency_key": "turn:manual-image",
            },
        )

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == ("deepseek/deepseek-v4-pro")
        assert completed["routing_decision"]["media_model_id"] == (
            "google/gemini-3.1-flash-lite-image"
        )
        assert completed["routing_decision"]["approval_required"] is False
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "Create a Fairy image"),
            ("assistant", "The image is ready in your Workspace."),
        ]
        assert len(media.image_requests) == 1
        assert len(coordinator.requests) == 3
        assert coordinator.requests[0].tools == ()
        assert [tool.name for tool in coordinator.requests[1].tools] == ["media.images.generate"]
        assert coordinator.requests[2].tools == ()
        assert any(
            "routed to image generation" in message.content
            for message in coordinator.requests[1].messages
        )
        with sqlite3.connect(tmp_path / "data" / "core.db") as connection:
            workflow_links = connection.execute(
                """
                SELECT owner_kind, parent_run_id
                FROM core_workflow_runs
                WHERE tenant_id = 'local'
                ORDER BY owner_kind
                """
            ).fetchall()
        assert workflow_links == [
            ("assistant_turn", None),
            ("media_generation", turn["workflow_run_id"]),
        ]
    finally:
        service.close()


def test_assistant_stops_after_the_first_media_provider_failure(tmp_path: Path) -> None:
    profile_id = "openrouter-deepseek-v4-pro"
    coordinator = ScriptedProvider(
        [
            _media_interpretation(profile_id, "Generate one Fairy image"),
            (
                ModelDelta.tool_call(
                    profile_id=profile_id,
                    sequence=1,
                    tool_call_id="generate-once",
                    tool_name="media.images.generate",
                    arguments_fragment='{"prompt":"A Fairy core"}',
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.tool_call(
                    profile_id=profile_id,
                    sequence=1,
                    tool_call_id="must-not-run",
                    tool_name="media.images.generate",
                    arguments_fragment='{"prompt":"Retry the Fairy core"}',
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
        ],
        profile_id=profile_id,
        model_id="deepseek/deepseek-v4-pro",
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        ),
    )
    media = FailingImageMediaProvider()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((coordinator,)),
        model_catalog_source=PricedCatalogSource(),
        media_provider=media,
    )
    try:
        selection = _manual_image_selection(service)
        task = _scratch_task(service, "Generate one Fairy image")
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "model_selection": {
                    "mode": selection["mode"],
                    "model_id": selection["model_id"],
                    "revision": selection["revision"],
                },
                "idempotency_key": "turn:failed-image-once",
            },
        )

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        jobs = service.invoke("media.jobs.list", {"task_id": task["id"]})["items"]

        assert failed["status"] == "failed"
        assert failed["error_code"] == "PROVIDER_PROTOCOL_ERROR"
        assert len(coordinator.requests) == 2
        assert len(media.image_requests) == 1
        assert len(jobs) == 1
        assert jobs[0]["status"] == "failed"
    finally:
        service.close()


def test_assistant_does_not_reexpose_media_tool_after_success(tmp_path: Path) -> None:
    profile_id = "openrouter-deepseek-v4-pro"
    coordinator = ScriptedProvider(
        [
            _media_interpretation(profile_id, "Generate exactly one Fairy image"),
            (
                ModelDelta.tool_call(
                    profile_id=profile_id,
                    sequence=1,
                    tool_call_id="first-image",
                    tool_name="media.images.generate",
                    arguments_fragment=(
                        '{"prompt":"First Fairy core","output_path":"generated/first.png"}'
                    ),
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.tool_call(
                    profile_id=profile_id,
                    sequence=1,
                    tool_call_id="second-image",
                    tool_name="media.images.generate",
                    arguments_fragment=(
                        '{"prompt":"Second Fairy core","output_path":"generated/second.png"}'
                    ),
                ),
                ModelDelta.done(
                    profile_id=profile_id,
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
        ],
        profile_id=profile_id,
        model_id="deepseek/deepseek-v4-pro",
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        ),
    )
    media = RecordingMediaProvider()
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((coordinator,)),
        model_catalog_source=PricedCatalogSource(),
        media_provider=media,
    )
    try:
        selection = _manual_image_selection(service)
        task = _scratch_task(service, "Generate exactly one Fairy image")
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "model_selection": {
                    "mode": selection["mode"],
                    "model_id": selection["model_id"],
                    "revision": selection["revision"],
                },
                "idempotency_key": "turn:one-image-limit",
            },
        )

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        jobs = service.invoke("media.jobs.list", {"task_id": task["id"]})["items"]

        assert failed["status"] == "failed"
        assert failed["error_code"] == "PROVIDER_PROTOCOL_ERROR"
        assert len(coordinator.requests) == 3
        assert coordinator.requests[0].tools == ()
        assert [tool.name for tool in coordinator.requests[1].tools] == ["media.images.generate"]
        assert coordinator.requests[2].tools == ()
        assert len(media.image_requests) == 1
        assert len(jobs) == 1
        assert jobs[0]["status"] == "completed"
    finally:
        service.close()
