from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.runtime_contracts import (
    PreviewResolveRequest,
    PreviewStartRequest,
    PreviewStopRequest,
)
from fairy_core.contracts.models import (
    PreviewIdInput,
    PreviewResolveInput,
    PreviewStartInput,
    PreviewStopInput,
    RuntimeHealthInput,
    RuntimeIdInput,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


def runtime_service_handlers(
    runtime_provider: Callable[[], RuntimeApplication],
    unit_of_work_factory: CoreUnitOfWorkFactory,
) -> dict[str, Any]:
    def get_runtime(request: BaseModel) -> Any:
        runtime_id = cast(RuntimeIdInput, request).runtime_id
        with unit_of_work_factory() as unit_of_work:
            runtime = unit_of_work.state.get_runtime(runtime_id)
        if runtime is None:
            raise KeyError(f"Runtime not found: {runtime_id}")
        return runtime

    def runtime_health(request: BaseModel) -> Any:
        return runtime_provider().runtime_health(cast(RuntimeHealthInput, request).task_id)

    def start_preview(request: BaseModel) -> Any:
        value = cast(PreviewStartInput, request)
        return runtime_provider().start_preview(
            PreviewStartRequest(**value.model_dump(mode="python"))
        )

    def get_preview(request: BaseModel) -> Any:
        return runtime_provider().get_preview(cast(PreviewIdInput, request).preview_id)

    def resolve_preview(request: BaseModel) -> Any:
        value = cast(PreviewResolveInput, request)
        return runtime_provider().resolve_preview(
            PreviewResolveRequest(**value.model_dump(mode="python"))
        )

    def stop_preview(request: BaseModel) -> Any:
        value = cast(PreviewStopInput, request)
        return runtime_provider().stop_preview(
            PreviewStopRequest(**value.model_dump(mode="python"))
        )

    return {
        "previews.get": get_preview,
        "previews.resolve": resolve_preview,
        "previews.start": start_preview,
        "previews.stop": stop_preview,
        "runtimes.get": get_runtime,
        "runtimes.health": runtime_health,
    }


__all__ = ["runtime_service_handlers"]
