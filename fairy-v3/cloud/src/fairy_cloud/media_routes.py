from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fairy_core.contracts.media import (
    MediaAudioGenerateInput,
    MediaGenerationJobModel,
    MediaGenerationJobPageModel,
    MediaImageGenerateInput,
    MediaJobListInput,
    MediaVideoCancelInput,
    MediaVideoJobInput,
    MediaVideoStartInput,
)
from fastapi import APIRouter, Header, HTTPException

SyncInvoke = Callable[[str, dict[str, Any]], dict[str, Any]]
AsyncInvoke = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
IdempotencyGuard = Callable[[str, str], None]


def install_media_routes(
    router: APIRouter,
    *,
    invoke: SyncInvoke,
    invoke_async: AsyncInvoke,
    guard: IdempotencyGuard,
) -> None:
    @router.get(
        "/media/jobs",
        operation_id="media.jobs.list",
        response_model=MediaGenerationJobPageModel,
    )
    def list_jobs(task_id: UUID) -> dict[str, Any]:
        request = MediaJobListInput(task_id=task_id)
        return invoke("media.jobs.list", request.model_dump(mode="json"))

    @router.post(
        "/media/images",
        operation_id="media.images.generate",
        response_model=MediaGenerationJobModel,
    )
    async def generate_image(
        request: MediaImageGenerateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        guard(request.idempotency_key, idempotency_key)
        return await invoke_async("media.images.generate", request.model_dump(mode="json"))

    @router.post(
        "/media/audio",
        operation_id="media.audio.generate",
        response_model=MediaGenerationJobModel,
    )
    async def generate_audio(
        request: MediaAudioGenerateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        guard(request.idempotency_key, idempotency_key)
        return await invoke_async("media.audio.generate", request.model_dump(mode="json"))

    @router.post(
        "/media/videos",
        operation_id="media.videos.start",
        response_model=MediaGenerationJobModel,
    )
    async def start_video(
        request: MediaVideoStartInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        guard(request.idempotency_key, idempotency_key)
        return await invoke_async("media.videos.start", request.model_dump(mode="json"))

    @router.get(
        "/media/videos/{job_id}",
        operation_id="media.videos.get",
        response_model=MediaGenerationJobModel,
    )
    async def get_video(job_id: UUID) -> dict[str, Any]:
        request = MediaVideoJobInput(job_id=job_id)
        return await invoke_async("media.videos.get", request.model_dump(mode="json"))

    @router.delete(
        "/media/videos/{job_id}",
        operation_id="media.videos.cancel",
        response_model=MediaGenerationJobModel,
    )
    def cancel_video(
        job_id: UUID,
        request: MediaVideoCancelInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        if request.job_id != job_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "media job id mismatch"},
            )
        guard(request.idempotency_key, idempotency_key)
        return invoke("media.videos.cancel", request.model_dump(mode="json"))


__all__ = ["install_media_routes"]
