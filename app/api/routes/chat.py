from __future__ import annotations

import asyncio
import logging
import json
import queue
import threading
from time import perf_counter
from time import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import FairyRuntimeService, build_error_contract, get_runtime_service
from app.api.models import ChatInvokeRequest, ChatInvokeResponse
from app.api.routes.companion import notify_assistant_text


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _summarize_message(message: str, *, limit: int = 80) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_sse_event(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/invoke", response_model=ChatInvokeResponse)
def invoke_chat(
    payload: ChatInvokeRequest,
    runtime_service: FairyRuntimeService = Depends(get_runtime_service),
) -> ChatInvokeResponse:
    started = perf_counter()
    session_id = payload.session_id or "default"
    request_id = ""
    try:
        result = runtime_service.invoke(
            message=payload.message,
            session_id=session_id,
            attachments=payload.attachments,
        )
        request_id = str(result.get("request_id") or "")
        notify_assistant_text(str(result.get("text") or ""))
    except Exception as exc:
        logger.exception("api_chat_invoke_failed session_id=%s", session_id)
        result = build_error_contract(
            request_id=request_id,
            session_id=session_id,
            code="runtime_error",
            message=str(exc),
        )
    duration_ms = int((perf_counter() - started) * 1000)
    logger.info(
        "api_chat_invoke request_id=%s session_id=%s message=%s duration_ms=%s cards=%s errors=%s",
        request_id or str(result.get("request_id") or ""),
        session_id,
        _summarize_message(payload.message),
        duration_ms,
        len(result.get("cards") or []),
        len(result.get("errors") or []),
    )
    return ChatInvokeResponse.model_validate(result)


@router.post("/stream")
async def stream_chat(
    payload: ChatInvokeRequest,
    request: Request,
    runtime_service: FairyRuntimeService = Depends(get_runtime_service),
) -> StreamingResponse:
    session_id = payload.session_id or "default"
    started = perf_counter()

    async def event_stream():
        request_id = ""
        cards_emitted = 0
        error_count = 0
        was_cancelled = False
        cancel_event = threading.Event()
        event_queue: queue.Queue[dict[str, object]] = queue.Queue()
        done_sentinel = {"kind": "done"}
        sequence = 0

        def normalize_stream_event(event: dict[str, object]) -> dict[str, object]:
            nonlocal sequence, request_id
            sequence += 1
            normalized = dict(event)
            request_id = str(normalized.get("request_id") or request_id)
            normalized["request_id"] = request_id
            normalized["session_id"] = str(normalized.get("session_id") or session_id)
            normalized["sequence"] = sequence
            normalized["timestamp_ms"] = int(time() * 1000)
            return normalized

        def worker() -> None:
            try:
                for event in runtime_service.stream_invoke(
                    message=payload.message,
                    session_id=session_id,
                    attachments=payload.attachments,
                    cancel_event=cancel_event,
                ):
                    event_queue.put(event)
            except Exception as exc:
                logger.exception("api_chat_stream_worker_failed session_id=%s", session_id)
                event_queue.put(
                    {
                        "event": "error",
                        "request_id": request_id,
                        "session_id": session_id,
                        "code": "runtime_stream_error",
                        "message": str(exc),
                    }
                )
            finally:
                event_queue.put(done_sentinel)

        threading.Thread(
            target=worker,
            name=f"api-stream-{session_id}",
            daemon=True,
        ).start()

        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    was_cancelled = True
                    break
                try:
                    event = event_queue.get(timeout=0.1)
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                if event is done_sentinel:
                    break
                normalized = normalize_stream_event(event)
                if str(normalized.get("event") or "") == "card":
                    cards_emitted += 1
                if str(normalized.get("event") or "") == "error":
                    error_count += 1
                if str(normalized.get("event") or "") == "message_end":
                    notify_assistant_text(str(normalized.get("text") or ""))
                event_name = str(normalized.get("event") or "message_end")
                yield _format_sse_event(event_name, normalized)
        except Exception as exc:
            cancel_event.set()
            logger.exception("api_chat_stream_failed session_id=%s", session_id)
            fallback = build_error_contract(
                request_id=request_id,
                session_id=session_id,
                code="runtime_stream_error",
                message=str(exc),
            )
            error_event = {
                "event": "error",
                "request_id": request_id,
                "session_id": session_id,
                "code": "runtime_stream_error",
                "message": str(exc),
            }
            yield _format_sse_event("error", normalize_stream_event(error_event))
            yield _format_sse_event(
                "message_end",
                normalize_stream_event(
                    {
                    "event": "message_end",
                    "request_id": request_id,
                    "session_id": session_id,
                    "text": fallback["text"],
                    "cards": fallback["cards"],
                    "meta": fallback["meta"],
                    "errors": fallback["errors"],
                    }
                ),
            )
            error_count += 1
        finally:
            cancel_event.set()
            duration_ms = int((perf_counter() - started) * 1000)
            logger.info(
                "api_chat_stream request_id=%s session_id=%s message=%s duration_ms=%s cards=%s errors=%s cancelled=%s",
                request_id,
                session_id,
                _summarize_message(payload.message),
                duration_ms,
                cards_emitted,
                error_count,
                was_cancelled,
            )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream; charset=utf-8",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
