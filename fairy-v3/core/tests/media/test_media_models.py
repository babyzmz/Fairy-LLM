from __future__ import annotations

from uuid import uuid4

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.media.models import (
    MediaGenerationJob,
    MediaGenerationKind,
    MediaGenerationStatus,
)
from fairy_core.model_catalog.models import ModelEndpointKind


def _job(*, kind: MediaGenerationKind = MediaGenerationKind.IMAGE) -> MediaGenerationJob:
    model_id, endpoint, output_path = {
        MediaGenerationKind.IMAGE: (
            "google/gemini-3.1-flash-lite-image",
            ModelEndpointKind.IMAGES,
            "generated/image.png",
        ),
        MediaGenerationKind.MUSIC: (
            "google/lyria-3-pro-preview",
            ModelEndpointKind.AUDIO,
            "generated/music.wav",
        ),
        MediaGenerationKind.VIDEO: (
            "bytedance/seedance-2.0",
            ModelEndpointKind.VIDEOS,
            "generated/video.mp4",
        ),
    }[kind]
    return MediaGenerationJob.create(
        project_id=None,
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        turn_id=None,
        command_run_id=uuid4(),
        scope_digest="a" * 64,
        kind=kind,
        model_id=model_id,
        endpoint_kind=endpoint,
        output_path=output_path,
        request_spec={"prompt": "A precise media prompt"},
        request_fingerprint="b" * 64,
        idempotency_key=f"media:{kind.value}:1",
    )


def test_synchronous_media_job_has_strict_terminal_transitions() -> None:
    job = _job()

    job.begin()
    job.complete(usage_cost="0.018")

    assert job.status is MediaGenerationStatus.COMPLETED
    assert job.progress == 100
    assert job.revision == 2
    with pytest.raises(InvalidTransitionError):
        job.fail("PROVIDER_ERROR")


def test_video_progress_cannot_regress_or_rebind_provider_identity() -> None:
    job = _job(kind=MediaGenerationKind.VIDEO)
    job.bind_video(
        provider_job_id="video-provider-1",
        status=MediaGenerationStatus.PENDING,
    )
    job.update_video(status=MediaGenerationStatus.IN_PROGRESS, progress=42)

    with pytest.raises(InvalidTransitionError):
        job.update_video(status=MediaGenerationStatus.IN_PROGRESS, progress=41)
    with pytest.raises(InvalidTransitionError):
        job.bind_video(
            provider_job_id="video-provider-2",
            status=MediaGenerationStatus.IN_PROGRESS,
        )


def test_media_kind_must_match_allowlisted_model_endpoint() -> None:
    with pytest.raises(ValueError, match="approved endpoint"):
        MediaGenerationJob.create(
            project_id=None,
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            task_id=uuid4(),
            version_id=uuid4(),
            turn_id=None,
            command_run_id=uuid4(),
            scope_digest="a" * 64,
            kind=MediaGenerationKind.IMAGE,
            model_id="deepseek/deepseek-v4-pro",
            endpoint_kind=ModelEndpointKind.IMAGES,
            output_path="generated/image.png",
            request_spec={"prompt": "A precise media prompt"},
            request_fingerprint="b" * 64,
            idempotency_key="media:image:mismatch",
        )
