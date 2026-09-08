from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock
from typing import TextIO

from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio_events import EventCursorResyncRequired, StdioEventWatches

# Names are audited, not inferred from '.get' or '.cancel' suffixes. In particular,
# assistant cancellation still invokes domain cleanup and is not a fast-lane action.
CONTROL_METHODS = frozenset({"health", "assistant.turns.pause"})
READ_METHODS = frozenset(
    {
        "assistant.turns.get",
        "assistant.turns.workflow.get",
        "assistant.turns.interpretation.get",
        "messages.list",
        "events.list",
        "events.subscribe",
    }
)
LANE_WORKERS = {"control": 1, "read": 2, "serial": 1}
LANE_CAPACITY = 32  # includes running and queued work, independently per lane


class StdioRequestDispatcher:
    """Connection-owned bounded dispatch, enabled only by local capability negotiation."""

    def __init__(self, dispatcher: JsonRpcDispatcher, destination: TextIO) -> None:
        self._dispatcher = dispatcher
        self._destination = destination
        self._write_lock = Lock()
        self._broken = Event()
        self._ids_lock = Lock()
        self._ids: set[tuple[type, str | int]] = set()
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._events: StdioEventWatches | None = None
        self._slots = {lane: BoundedSemaphore(LANE_CAPACITY) for lane in LANE_WORKERS}

    def write(self, response: dict) -> None:
        payload = json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._write_lock:
            try:
                self._destination.write(payload)
                self._destination.flush()
            except OSError:
                self._broken.set()
                raise

    def dispatch(self, request: dict) -> None:
        if self._broken.is_set():
            raise BrokenPipeError("Core response stream is closed")
        method = str(request.get("method") or "")
        if method == "transport.negotiate":
            params = request.get("params", {})
            if (
                not isinstance(params, dict)
                or set(params) - {"event_notifications"}
                or type(params.get("event_notifications", False)) is not bool
            ):
                self._reject(request.get("id"), "RPC_INVALID_NEGOTIATION")
                return
            if (
                params.get("event_notifications")
                and self._events is None
                and getattr(self._dispatcher, "ledger_signal", None) is not None
            ):
                self._events = StdioEventWatches(self._dispatcher, self.write)
            if not self._pools:
                self._pools = {
                    lane: ThreadPoolExecutor(
                        max_workers=workers, thread_name_prefix=f"core-rpc-{lane}"
                    )
                    for lane, workers in LANE_WORKERS.items()
                }
            self.write(
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {
                        "protocol": "stdio-dispatch-v1",
                        "concurrent_requests": True,
                        "event_notifications": self._events is not None,
                        "lane_capacity": LANE_CAPACITY,
                    },
                }
            )
            return
        if method in {"events.watch", "events.unwatch"}:
            if self._events is None:
                self._reject(request.get("id"), "RPC_EVENTS_NOT_NEGOTIATED")
                return
            try:
                handler = self._events.watch if method == "events.watch" else self._events.unwatch
                result = handler(request.get("params", {}))
            except EventCursorResyncRequired:
                self._reject(request.get("id"), "RPC_EVENT_RESYNC_REQUIRED")
            except ValueError:
                self._reject(request.get("id"), "RPC_EVENT_SUBSCRIPTION_INVALID")
            except RuntimeError:
                self._reject(request.get("id"), "RPC_EVENT_STREAM_UNAVAILABLE")
            else:
                self.write({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
            return
        if not self._pools:
            self.write(self._dispatcher.dispatch(request))
            return
        key = request.get("id")
        if type(key) not in {str, int}:
            self._reject(key, "RPC_REQUEST_ID_REQUIRED")
            return
        identity = (type(key), key)
        with self._ids_lock:
            duplicate = identity in self._ids
            if not duplicate:
                self._ids.add(identity)
        if duplicate:
            self._reject(key, "RPC_DUPLICATE_REQUEST_ID")
            return
        lane = (
            "control"
            if method in CONTROL_METHODS
            else "read"
            if method in READ_METHODS
            else "serial"
        )
        if not self._slots[lane].acquire(blocking=False):
            with self._ids_lock:
                self._ids.remove(identity)
            self._reject(key, "RPC_CAPACITY_EXCEEDED")
            return
        self._pools[lane].submit(self._execute, request, identity, lane)

    def _execute(self, request: dict, identity: tuple[type, str | int], lane: str) -> None:
        try:
            if not self._broken.is_set():
                self.write(self._dispatcher.dispatch(request))
        finally:
            with self._ids_lock:
                self._ids.remove(identity)
            self._slots[lane].release()

    def _reject(self, key, code: str) -> None:
        self.write(
            {
                "jsonrpc": "2.0",
                "id": key,
                "error": {
                    "code": -32051,
                    "message": "Request was not dispatched",
                    "data": {"error_code": code},
                },
            }
        )

    def close(self) -> None:
        for pool in self._pools.values():
            pool.shutdown(wait=True)
        if self._events is not None:
            self._events.close()
