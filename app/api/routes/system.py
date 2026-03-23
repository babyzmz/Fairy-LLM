from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import FairyRuntimeService, get_runtime_service
from app.api.models import SystemActionRequest, SystemActionResponse, SystemEventModel, SystemStateResponse


router = APIRouter(prefix="/system", tags=["system"])


@router.get("/state", response_model=SystemStateResponse)
def get_system_state(runtime_service: FairyRuntimeService = Depends(get_runtime_service)) -> SystemStateResponse:
    return SystemStateResponse.model_validate(runtime_service.system_state())


@router.get("/events", response_model=list[SystemEventModel])
def get_system_events(
    limit: int = Query(default=25, ge=1, le=200),
    runtime_service: FairyRuntimeService = Depends(get_runtime_service),
) -> list[SystemEventModel]:
    return runtime_service.system_events(limit=limit)


@router.post("/actions", response_model=SystemActionResponse)
def perform_system_action(
    payload: SystemActionRequest,
    runtime_service: FairyRuntimeService = Depends(get_runtime_service),
) -> SystemActionResponse:
    return SystemActionResponse.model_validate(runtime_service.perform_system_action(payload.action, payload.payload))
