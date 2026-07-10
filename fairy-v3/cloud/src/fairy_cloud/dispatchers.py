from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_dispatcher

from fairy_cloud.auth import RequestIdentity


class TenantDispatcherRegistry:
    """Process-local Core registry with opaque, isolated tenant data roots."""

    def __init__(
        self,
        *,
        root: Path,
        builder: Callable[[Path], JsonRpcDispatcher] = build_local_dispatcher,
    ) -> None:
        self._root = root
        self._builder = builder
        self._dispatchers: dict[str, JsonRpcDispatcher] = {}
        self._lock = threading.RLock()

    def system_dispatcher(self) -> JsonRpcDispatcher:
        return self._dispatcher_for_key("system", self._root / "system")

    def for_identity(self, identity: RequestIdentity) -> JsonRpcDispatcher:
        tenant_key = hashlib.sha256(identity.user_id.encode("utf-8")).hexdigest()
        return self._dispatcher_for_key(tenant_key, self._root / "tenants" / tenant_key)

    def _dispatcher_for_key(self, key: str, path: Path) -> JsonRpcDispatcher:
        with self._lock:
            dispatcher = self._dispatchers.get(key)
            if dispatcher is None:
                dispatcher = self._builder(path)
                self._dispatchers[key] = dispatcher
            return dispatcher
