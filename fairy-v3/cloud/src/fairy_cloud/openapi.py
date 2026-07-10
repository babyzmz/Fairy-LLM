from __future__ import annotations

from typing import Any, cast

from fairy_core.transports.jsonrpc import JsonRpcDispatcher

from fairy_cloud.api import create_cloud_app


class _SchemaDispatcher:
    def dispatch(self, _request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the OpenAPI schema dispatcher cannot execute requests")


def build_openapi_document() -> dict[str, Any]:
    dispatcher = cast(JsonRpcDispatcher, _SchemaDispatcher())
    return create_cloud_app(dispatcher).openapi()
