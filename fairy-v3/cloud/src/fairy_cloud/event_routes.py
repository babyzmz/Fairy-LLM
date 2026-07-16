from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fairy_core.contracts.methods import EventPageModel
from fairy_core.contracts.models import EventStreamStateModel
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fairy_cloud.auth import RequestIdentity
from fairy_cloud.sync.models import SyncedEvent
from fairy_cloud.sync.ports import SyncStore

AsyncInvoke = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
IdentityResolver = Callable[[Request], RequestIdentity]


def install_event_routes(
    router: APIRouter,
    *,
    invoke_async: AsyncInvoke,
    sync_store: SyncStore | None,
    identity_for: IdentityResolver,
    event_poll_seconds: float,
) -> None:
    @router.get(
        "/events/state",
        operation_id="events.state",
        response_model=EventStreamStateModel,
    )
    async def event_state(request: Request) -> EventStreamStateModel:
        if sync_store is None:
            payload = await invoke_async("events.state", {})
        else:
            identity = identity_for(request)
            payload = await sync_store.event_stream_state(user_id=identity.user_id)
        return EventStreamStateModel.model_validate(payload)

    @router.get(
        "/events/history",
        operation_id="events.list",
        response_model=EventPageModel,
    )
    async def event_history(
        request: Request,
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=2_000)] = 500,
    ) -> EventPageModel:
        if sync_store is None:
            payload = await invoke_async("events.list", {"cursor": cursor, "limit": limit})
        else:
            identity = identity_for(request)
            events = await sync_store.events_after(
                user_id=identity.user_id,
                cursor=cursor,
                limit=limit,
            )
            items = [_synced_event_json(event) for event in events]
            payload = {
                "items": items,
                "next_cursor": int(items[-1]["cursor"]) if items else cursor,
            }
        return EventPageModel.model_validate(payload)

    @router.get(
        "/events",
        response_class=EventSourceResponse,
        operation_id="events.subscribe",
    )
    async def events(
        request: Request,
        cursor: Annotated[int, Query(ge=0)] = 0,
        follow: Annotated[bool, Query()] = True,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        current = _resume_cursor(cursor, last_event_id)
        while True:
            if sync_store is None:
                batch = await invoke_async("events.subscribe", {"cursor": current})
                items = batch["items"]
            else:
                identity = identity_for(request)
                synced_events = await sync_store.events_after(
                    user_id=identity.user_id,
                    cursor=current,
                )
                items = [_synced_event_json(event) for event in synced_events]
            for item in items:
                current = int(item["cursor"])
                yield ServerSentEvent(
                    data=item,
                    event=str(item["event_type"]),
                    id=str(current),
                    retry=1_000,
                )
            if not follow or await request.is_disconnected():
                break
            if not items:
                yield ServerSentEvent(comment="keepalive")
            await asyncio.sleep(event_poll_seconds)


def _resume_cursor(cursor: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return cursor
    try:
        return max(cursor, int(last_event_id))
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Last-Event-ID must be an integer",
        ) from error


def _synced_event_json(event: SyncedEvent) -> dict[str, Any]:
    item = dict(event.payload)
    item.update(
        {
            "id": event.event_id,
            "cursor": event.cursor,
            "run_id": event.run_id,
            "project_id": event.project_id,
            "conversation_id": event.conversation_id,
            "task_id": event.task_id,
            "version_id": event.version_id,
            "task_sequence": event.task_sequence,
            "event_type": event.event_type,
            "visibility": event.visibility,
            "message": event.message,
            "schema_version": event.schema_version,
        }
    )
    item.setdefault("created_at", event.created_at.isoformat())
    return item


__all__ = ["install_event_routes"]
