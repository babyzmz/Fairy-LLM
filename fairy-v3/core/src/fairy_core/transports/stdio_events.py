from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread

from fairy_core.transports.jsonrpc import JsonRpcDispatcher


class EventCursorResyncRequired(ValueError):
    pass


@dataclass
class _Watch:
    subscription_id: str
    ledger_id: str
    cursor: int


class StdioEventWatches:
    """Bounded connection subscriptions; persistent Ledger is the replay authority."""

    def __init__(self, dispatcher: JsonRpcDispatcher, emit: Callable[[dict], None]) -> None:
        if dispatcher.ledger_signal is None:
            raise ValueError("Ledger notifications are unavailable")
        self._dispatcher = dispatcher
        self._signal = dispatcher.ledger_signal
        self._emit = emit
        self._watches: dict[str, _Watch] = {}
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._pump, name="core-ledger-events", daemon=True)
        self._thread.start()

    def watch(self, params: dict) -> dict:
        key = self._key(params)
        cursor = params.get("cursor")
        if set(params) != {"subscription_id", "cursor"} or type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid event watch cursor")
        state = self._read("events.state", {})
        if cursor > state["latest_cursor"] or (cursor and cursor < state["oldest_cursor"] - 1):
            raise EventCursorResyncRequired("Event cursor requires resync with the current ledger")
        with self._lock:
            if self._stop.is_set():
                raise ValueError("Event connection is closing")
            if key in self._watches:
                raise ValueError("Event subscription already exists")
            if len(self._watches) >= 8:
                raise ValueError("Event subscription capacity exceeded")
            self._watches[key] = _Watch(key, state["ledger_id"], cursor)
        self._signal.notify()
        return {"subscription_id": key, **state}

    def unwatch(self, params: dict) -> dict:
        key = self._key(params)
        if set(params) != {"subscription_id"}:
            raise ValueError("Invalid event unwatch parameters")
        with self._lock:
            self._watches.pop(key, None)
        return {"subscription_id": key, "stopped": True}

    @staticmethod
    def _key(params) -> str:
        if not isinstance(params, dict):
            raise ValueError("Invalid event parameters")
        key = params.get("subscription_id")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", key):
            raise ValueError("Invalid event subscription ID")
        return key

    def _read(self, method: str, params: dict) -> dict:
        response = self._dispatcher.dispatch(
            {"jsonrpc": "2.0", "id": 0, "method": method, "params": params}
        )
        if "error" in response:
            raise RuntimeError("Ledger replay is unavailable")
        return response["result"]

    def _pump(self) -> None:
        while not self._stop.is_set():
            seen = self._signal.version
            with self._lock:
                watches = tuple(self._watches.values())
            backlog = False
            for watch in watches:
                if self._stop.is_set():
                    return
                try:
                    batch = self._read("events.list", {"cursor": watch.cursor, "limit": 64})
                    if not batch["items"]:
                        continue
                    with self._lock:
                        if self._watches.get(watch.subscription_id) is not watch:
                            continue
                    self._emit(
                        {
                            "jsonrpc": "2.0",
                            "method": "events.changed",
                            "params": {
                                "subscription_id": watch.subscription_id,
                                "source": "local:stdio",
                                "ledger_id": watch.ledger_id,
                                "cursor": batch["next_cursor"],
                                "items": batch["items"],
                                "resync_required": False,
                            },
                        }
                    )
                    watch.cursor = batch["next_cursor"]
                    backlog = backlog or len(batch["items"]) == 64
                except OSError:
                    self._stop.set()
                    return
                except Exception:
                    # No raw exception or prompt content crosses this transport boundary.
                    with self._lock:
                        self._watches.pop(watch.subscription_id, None)
                    try:
                        self._emit(
                            {
                                "jsonrpc": "2.0",
                                "method": "events.changed",
                                "params": {
                                    "subscription_id": watch.subscription_id,
                                    "source": "local:stdio",
                                    "ledger_id": watch.ledger_id,
                                    "cursor": watch.cursor,
                                    "items": [],
                                    "resync_required": True,
                                },
                            }
                        )
                    except OSError:
                        self._stop.set()
                        return
            if not backlog:
                self._signal.wait(seen, timeout=5.0)

    def close(self) -> None:
        self._stop.set()
        self._signal.notify()
        self._thread.join()
        with self._lock:
            self._watches.clear()
