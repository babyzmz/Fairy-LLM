from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import FairyRuntimeService, get_runtime_service
from app.api.models import CapabilitiesResponse


router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities(runtime_service: FairyRuntimeService = Depends(get_runtime_service)) -> CapabilitiesResponse:
    return CapabilitiesResponse.model_validate(runtime_service.capabilities())

