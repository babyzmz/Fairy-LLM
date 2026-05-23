from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from dataclasses import asdict
from time import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.companion import (
    QuipEvent,
    get_bubble_state,
    get_companion_observer,
    get_passive_screen_watcher,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companion", tags=["companion"])


class MuteRequest(BaseModel):
    muted: bool


def _format_sse(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _quip_event_payload(event: QuipEvent) -> dict[str, object]:
    return {
        "text": event.text,
        "category": event.category.value,
        "source": event.source,
        "emitted_at": event.emitted_at,
    }


@router.get("/quip-stream")
async def quip_stream(request: Request) -> StreamingResponse:
    observer = get_companion_observer()
    bubble = get_bubble_state()
    snapshot = bubble.snapshot_dict()

    event_queue: queue.Queue[dict[str, object] | None] = queue.Queue()

    def on_quip(event: QuipEvent) -> None:
        event_queue.put({"kind": "quip", "payload": _quip_event_payload(event)})

    unsubscribe = observer.subscribe(on_quip)

    async def event_stream():
        try:
            yield _format_sse("hello", {"ts": time(), "snapshot": snapshot})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = event_queue.get(timeout=0.5)
                except queue.Empty:
                    yield _format_sse("ping", {"ts": time()})
                    continue
                if item is None:
                    return
                if item.get("kind") == "quip":
                    yield _format_sse("quip", item["payload"])  # type: ignore[arg-type]
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream; charset=utf-8",
    }
    return StreamingResponse(event_stream(), headers=headers, media_type="text/event-stream")


@router.get("/state")
def get_state() -> JSONResponse:
    bubble = get_bubble_state()
    watcher = get_passive_screen_watcher()
    payload = {
        "bubble": bubble.snapshot_dict(),
        "watcher": asdict(watcher.snapshot()),
        "muted": get_companion_observer().muted,
    }
    return JSONResponse(payload)


@router.post("/pet")
def pet() -> JSONResponse:
    started_at = get_bubble_state().trigger_pet()
    return JSONResponse({"pet_started_at": started_at, "now": time()})


@router.post("/mute")
def set_mute(request: MuteRequest) -> JSONResponse:
    muted = bool(request.muted)
    get_bubble_state().set_muted(muted)
    return JSONResponse({"muted": muted})


@router.post("/watcher/start")
def start_watcher() -> JSONResponse:
    get_passive_screen_watcher().start()
    return JSONResponse({"running": True})


@router.post("/watcher/stop")
def stop_watcher() -> JSONResponse:
    get_passive_screen_watcher().stop()
    return JSONResponse({"running": False})


_assistant_lock = threading.Lock()


def notify_assistant_text(text: str) -> None:
    if not text:
        return
    with _assistant_lock:
        try:
            get_companion_observer().observe_assistant_text(text, source="assistant")
        except Exception:
            logger.exception("companion_notify_assistant_failed")
