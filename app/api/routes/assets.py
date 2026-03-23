from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.dependencies import resolve_asset_path


router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/local")
def get_local_asset(path: str = Query(min_length=1)) -> FileResponse:
    resolved = resolve_asset_path(path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(resolved)

