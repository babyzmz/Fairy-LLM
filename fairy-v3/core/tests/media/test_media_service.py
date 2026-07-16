from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.media.ports import (
    GeneratedMedia,
    ImageGenerationRequest,
    MediaProviderVideoStatus,
    MusicGenerationRequest,
    VideoGenerationRequest,
    VideoProviderJob,
)
from fairy_core.providers import CancellationToken, ModelDelta, ProviderCapability, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_model_routing import PricedCatalogSource


class RecordingMediaProvider:
    def __init__(self) -> None:
        self.image_requests: list[ImageGenerationRequest] = []
        self.music_requests: list[MusicGenerationRequest] = []
        self.video_requests: list[VideoGenerationRequest] = []
        self.video_statuses: list[MediaProviderVideoStatus] = []
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

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "media.images.generate",
                {**request, "prompt": "A different image"},
            )
    finally:
        service.close()
    assert provider.closed is True


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


def test_video_poll_download_and_local_cancel_are_durable(tmp_path: Path) -> None:
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
        running = service.invoke("media.videos.get", {"job_id": started["id"]})
        completed = service.invoke("media.videos.get", {"job_id": started["id"]})

        assert started["status"] == "pending"
        assert started == replayed_start
        assert str(started["output_path"]).startswith("generated/video-")
        assert running["status"] == "in_progress"
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
        assert len(coordinator.requests) == 2
        assert all(
            [tool.name for tool in request.tools] == ["media.images.generate"]
            for request in coordinator.requests
        )
        assert any(
            "routed to image generation" in message.content
            for message in coordinator.requests[0].messages
        )
    finally:
        service.close()
