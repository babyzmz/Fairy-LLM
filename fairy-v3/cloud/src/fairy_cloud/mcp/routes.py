from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fairy_core.contracts.extensions import (
    ExtensionCatalogPageModel,
    McpServerAcceptInput,
    McpServerConfigureInput,
    McpServerDeleteInput,
    McpServerDeleteResult,
    McpServerDiscoverInput,
    McpServerModel,
    McpServerPageModel,
    McpServerSetEnabledInput,
    SkillInstallInput,
    SkillPageModel,
    SkillRemoveInput,
    SkillRemoveResult,
    SkillSetEnabledInput,
    SkillUpdateInput,
)
from fastapi import APIRouter, Header, HTTPException

CoreInvoker = Callable[[str, dict[str, Any]], Any]


def install_extension_routes(router: APIRouter, invoke: CoreInvoker) -> None:
    @router.get(
        "/extensions/catalog",
        operation_id="extensions.catalog.list",
        response_model=ExtensionCatalogPageModel,
    )
    def list_extension_catalog() -> dict[str, Any]:
        return invoke("extensions.catalog.list", {})

    @router.get(
        "/skills",
        operation_id="skills.list",
        response_model=SkillPageModel,
    )
    def list_skills() -> dict[str, Any]:
        return invoke("skills.list", {})

    @router.post(
        "/skills/{catalog_id}",
        operation_id="skills.install",
        response_model=SkillPageModel,
    )
    def install_skill(
        catalog_id: str,
        request: SkillInstallInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=512)
        ],
    ) -> dict[str, Any]:
        _validate_request(catalog_id, request.catalog_id, request.idempotency_key, idempotency_key)
        return invoke("skills.install", request.model_dump(mode="json"))

    @router.put(
        "/skills/{name}",
        operation_id="skills.update",
        response_model=SkillPageModel,
    )
    def update_skill(
        name: str,
        request: SkillUpdateInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=512)
        ],
    ) -> dict[str, Any]:
        _validate_request(name, request.name, request.idempotency_key, idempotency_key)
        return invoke("skills.update", request.model_dump(mode="json"))

    @router.post(
        "/skills/{name}/enabled",
        operation_id="skills.set_enabled",
        response_model=SkillPageModel,
    )
    def set_skill_enabled(
        name: str,
        request: SkillSetEnabledInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=512)
        ],
    ) -> dict[str, Any]:
        _validate_request(name, request.name, request.idempotency_key, idempotency_key)
        return invoke("skills.set_enabled", request.model_dump(mode="json"))

    @router.delete(
        "/skills/{name}",
        operation_id="skills.remove",
        response_model=SkillRemoveResult,
    )
    def remove_skill(
        name: str,
        request: SkillRemoveInput,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=512)
        ],
    ) -> dict[str, Any]:
        _validate_request(name, request.name, request.idempotency_key, idempotency_key)
        return invoke("skills.remove", request.model_dump(mode="json"))

    @router.get(
        "/mcp/servers",
        operation_id="mcp.servers.list",
        response_model=McpServerPageModel,
    )
    def list_mcp_servers() -> dict[str, Any]:
        return invoke("mcp.servers.list", {})

    @router.put(
        "/mcp/servers/{server_id}",
        operation_id="mcp.servers.configure",
        response_model=McpServerModel,
    )
    def configure_mcp_server(
        server_id: str,
        request: McpServerConfigureInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        _validate_request(server_id, request.server_id, request.idempotency_key, idempotency_key)
        return invoke("mcp.servers.configure", request.model_dump(mode="json"))

    @router.post(
        "/mcp/servers/{server_id}/discover",
        operation_id="mcp.servers.discover",
        response_model=McpServerModel,
    )
    def discover_mcp_server(
        server_id: str,
        request: McpServerDiscoverInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        _validate_request(server_id, request.server_id, request.idempotency_key, idempotency_key)
        return invoke("mcp.servers.discover", request.model_dump(mode="json"))

    @router.post(
        "/mcp/servers/{server_id}/accept",
        operation_id="mcp.servers.accept",
        response_model=McpServerModel,
    )
    def accept_mcp_server(
        server_id: str,
        request: McpServerAcceptInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        _validate_request(server_id, request.server_id, request.idempotency_key, idempotency_key)
        return invoke("mcp.servers.accept", request.model_dump(mode="json"))

    @router.post(
        "/mcp/servers/{server_id}/enabled",
        operation_id="mcp.servers.set_enabled",
        response_model=McpServerModel,
    )
    def set_mcp_server_enabled(
        server_id: str,
        request: McpServerSetEnabledInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        _validate_request(server_id, request.server_id, request.idempotency_key, idempotency_key)
        return invoke("mcp.servers.set_enabled", request.model_dump(mode="json"))

    @router.delete(
        "/mcp/servers/{server_id}",
        operation_id="mcp.servers.delete",
        response_model=McpServerDeleteResult,
    )
    def delete_mcp_server(
        server_id: str,
        request: McpServerDeleteInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        _validate_request(server_id, request.server_id, request.idempotency_key, idempotency_key)
        return invoke("mcp.servers.delete", request.model_dump(mode="json"))


def _validate_request(
    path_server_id: str,
    body_server_id: str,
    body_idempotency_key: str,
    header_idempotency_key: str,
) -> None:
    if path_server_id != body_server_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCOPE_MISMATCH",
                "message": "MCP server path does not match Core params",
            },
        )
    if body_idempotency_key != header_idempotency_key:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCOPE_MISMATCH",
                "message": "Idempotency-Key does not match Core params",
            },
        )


__all__ = ["install_extension_routes"]
