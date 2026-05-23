from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.commands import CommandKind, get_command_registry


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/commands", tags=["commands"])


class ExecuteRequest(BaseModel):
    name: str
    args: dict | None = None


@router.get("/list")
def list_commands() -> JSONResponse:
    registry = get_command_registry()
    return JSONResponse({"commands": registry.list_specs()})


@router.post("/execute")
def execute_command(request: ExecuteRequest) -> JSONResponse:
    registry = get_command_registry()
    spec = registry.find(request.name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"command not found: {request.name}")
    if spec.kind != CommandKind.SERVER_ACTION:
        raise HTTPException(status_code=400, detail=f"{spec.name} is a {spec.kind.value} command; resolve on client")
    if not spec.server_handler_name:
        raise HTTPException(status_code=500, detail=f"{spec.name} missing server handler binding")
    try:
        result = registry.execute_server(spec.server_handler_name, request.args or {})
    except Exception as exc:
        logger.exception("command_execute_failed name=%s", request.name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"name": spec.name, "result": result})
