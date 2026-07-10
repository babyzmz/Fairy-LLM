from __future__ import annotations

from typing import Any, cast

from fairy_core.application.service import CoreService

from fairy_cloud.api import create_cloud_app


class _SchemaService:
    def invoke(self, _method: str, _params: dict[str, Any]) -> Any:
        raise RuntimeError("the OpenAPI schema service cannot execute requests")


def build_openapi_document() -> dict[str, Any]:
    service = cast(CoreService, _SchemaService())
    return create_cloud_app(service).openapi()
