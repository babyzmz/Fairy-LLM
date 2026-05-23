from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.dependencies import build_error_contract, initialize_runtime_service, shutdown_runtime_service
from app.api.routes import (
    assets_router,
    capabilities_router,
    chat_router,
    commands_router,
    companion_router,
    health_router,
    system_router,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_runtime_service()
    try:
        yield
    finally:
        shutdown_runtime_service()


app = FastAPI(
    title="Fairy Runtime API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(capabilities_router)
app.include_router(chat_router)
app.include_router(assets_router)
app.include_router(system_router)
app.include_router(companion_router)
app.include_router(commands_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("api_unhandled_exception")
    return JSONResponse(
        status_code=500,
        content=build_error_contract(code="server_error", message=str(exc)),
    )
