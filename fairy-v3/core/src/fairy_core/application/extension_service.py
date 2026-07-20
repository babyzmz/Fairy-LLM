from __future__ import annotations

import os
import shutil
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.contracts.extensions import (
    McpPresetInstallInput,
    McpServerAcceptInput,
    McpServerConfigureInput,
    McpServerDeleteInput,
    McpServerDiscoverInput,
    McpServerSetEnabledInput,
    SkillCreateInput,
    SkillImportInspectInput,
    SkillImportInstallInput,
    SkillInstallInput,
    SkillRemoveInput,
    SkillSetEnabledInput,
    SkillUpdateInput,
)
from fairy_core.mcp.application import McpApplication
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.skills.manager import SkillManager
from fairy_core.skills.registry import SkillRegistry


class ExtensionService:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        execution_policy: ExecutionPolicyResolver,
        default_execution_target: str,
        skill_registry: SkillRegistry,
        skill_manager: SkillManager | None,
        mcp_application: McpApplication | None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._execution_policy = execution_policy
        self._default_execution_target = default_execution_target
        self._skill_registry = skill_registry
        self._skill_manager = skill_manager
        self._mcp_application = mcp_application

    @property
    def handlers(self) -> dict[str, Any]:
        return {
            "extensions.catalog.list": self.list_catalog,
            "mcp.servers.accept": self.accept_mcp_server,
            "mcp.servers.configure": self.configure_mcp_server,
            "mcp.servers.delete": self.delete_mcp_server,
            "mcp.servers.discover": self.discover_mcp_server,
            "mcp.servers.list": self.list_mcp_servers,
            "mcp.servers.set_enabled": self.set_mcp_server_enabled,
            "mcp.presets.install": self.install_mcp_preset,
            "skills.create": self.create_skill,
            "skills.import.inspect": self.inspect_skill_import,
            "skills.import.install": self.install_skill_import,
            "skills.install": self.install_skill,
            "skills.list": self.list_skills,
            "skills.remove": self.remove_skill,
            "skills.set_enabled": self.set_skill_enabled,
            "skills.update": self.update_skill,
        }

    def refresh_registry(self) -> None:
        if self._mcp_application is not None:
            self._mcp_application.reload_registry()

    def list_skills(self, _request: BaseModel) -> dict[str, Any]:
        self.refresh_registry()
        with self._unit_of_work_factory() as unit_of_work:
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=self._default_execution_target,
            )
        operations = self._registry.capability_manifest(
            profile=policy.profile,
            sandbox_healthy=policy.sandbox_healthy,
            overrides=dict(policy.capability_overrides),
        )
        return {
            "items": [
                {
                    "name": package.manifest.name,
                    "version": package.manifest.version,
                    "description": package.manifest.description,
                    "tool_name": package.manifest.tool_name,
                    "content_sha256": package.content_sha256,
                    "required_capabilities": package.manifest.required_capabilities,
                    "compatible_mcp_servers": package.manifest.compatible_mcp_servers,
                    "provenance": package.manifest.provenance.model_dump(mode="json"),
                    "enabled": self._skill_registry.enabled(package.manifest.name),
                    "available": operations.get(package.manifest.tool_name, False),
                }
                for package in self._skill_registry.packages()
            ]
        }

    def list_catalog(self, _request: BaseModel) -> dict[str, Any]:
        installed = {package.manifest.name for package in self._skill_registry.packages()}
        if self._mcp_application is not None:
            installed.update(
                record.connection.server_id for record in self._mcp_application.list_servers()
            )
        return {
            "items": [
                {
                    "extension_id": entry.extension_id,
                    "kind": entry.kind,
                    "name": entry.name,
                    "description": entry.description,
                    "publisher": entry.publisher,
                    "version": entry.version,
                    "source": entry.source,
                    "license": entry.license,
                    "experimental": entry.experimental,
                    "installed": entry.extension_id in installed,
                    "source_kind": entry.source_kind,
                    "trust": entry.trust,
                    "tags": entry.tags,
                    "requirements": entry.requirements,
                }
                for entry in self._skills().catalog()
            ]
        }

    def install_skill(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillInstallInput, request)
        self._skills().install(validated.catalog_id)
        return self.list_skills(request)

    def inspect_skill_import(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillImportInspectInput, request)
        if self._default_execution_target == "cloud" and validated.source_kind != "github":
            raise ValueError("Cloud Skill imports require a GitHub source")
        pending = self._skills().inspect_import(validated.source_kind, validated.source)
        manifest = pending.inspection.manifest
        return {
            "inspection_token": pending.token,
            "name": pending.inspection.name,
            "description": pending.inspection.description,
            "version": manifest.version if manifest is not None else None,
            "publisher": manifest.provenance.publisher if manifest is not None else None,
            "license": manifest.provenance.license if manifest is not None else None,
            "source": pending.source,
            "has_manifest": manifest is not None,
            "file_count": pending.inspection.file_count,
            "content_bytes": pending.inspection.content_bytes,
        }

    def install_skill_import(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillImportInstallInput, request)
        self._skills().install_import(
            validated.inspection_token,
            name=validated.name,
            version=validated.version,
            description=validated.description,
            publisher=validated.publisher,
            license_name=validated.license,
            input_schema=validated.input_schema,
            required_capabilities=validated.required_capabilities,
            compatible_mcp_servers=validated.compatible_mcp_servers,
        )
        return self.list_skills(request)

    def create_skill(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillCreateInput, request)
        self._skills().create(
            name=validated.name,
            version=validated.version,
            description=validated.description,
            instructions=validated.instructions,
            publisher=validated.publisher,
            license_name=validated.license,
            input_schema=validated.input_schema,
            required_capabilities=validated.required_capabilities,
            compatible_mcp_servers=validated.compatible_mcp_servers,
        )
        return self.list_skills(request)

    def update_skill(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillUpdateInput, request)
        self._skills().update(
            validated.name,
            expected_content_sha256=validated.expected_content_sha256,
        )
        return self.list_skills(request)

    def set_skill_enabled(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillSetEnabledInput, request)
        self._require_skill_digest(validated.name, validated.expected_content_sha256)
        self._skills().set_enabled(validated.name, enabled=validated.enabled)
        return self.list_skills(request)

    def remove_skill(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(SkillRemoveInput, request)
        self._require_skill_digest(validated.name, validated.expected_content_sha256)
        self._skills().remove(validated.name)
        return {"name": validated.name, "removed": True}

    def list_mcp_servers(self, _request: BaseModel) -> dict[str, Any]:
        application = self._mcp()
        return {
            "items": [
                self._mcp_server_payload(application, record)
                for record in application.list_servers()
            ]
        }

    def configure_mcp_server(self, request: BaseModel) -> dict[str, Any]:
        application = self._mcp()
        record = application.configure(cast(McpServerConfigureInput, request))
        return self._mcp_server_payload(application, record)

    def install_mcp_preset(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(McpPresetInstallInput, request)
        preset = self._skills().mcp_preset(validated.catalog_id)
        if preset.credential_required and validated.credential_ref is None:
            raise ValueError("MCP preset requires a credential reference")
        command = preset.command
        if command == "npx":
            command = shutil.which("npx.cmd" if os.name == "nt" else "npx") or shutil.which("npx")
            if command is None:
                raise RuntimeError("Playwright MCP requires Node.js and npx")
        configured = McpServerConfigureInput(
            server_id=preset.entry.extension_id,
            display_name=preset.entry.name,
            transport=preset.transport,
            command=command,
            arguments=preset.arguments,
            endpoint=preset.endpoint,
            credential_ref=validated.credential_ref,
            environment_refs={},
            expected_revision=validated.expected_revision,
            idempotency_key=validated.idempotency_key,
        )
        application = self._mcp()
        record = application.configure(configured)
        return self._mcp_server_payload(application, record)

    def discover_mcp_server(self, request: BaseModel) -> dict[str, Any]:
        application = self._mcp()
        record = application.discover(cast(McpServerDiscoverInput, request))
        return self._mcp_server_payload(application, record)

    def accept_mcp_server(self, request: BaseModel) -> dict[str, Any]:
        application = self._mcp()
        record = application.accept(cast(McpServerAcceptInput, request))
        return self._mcp_server_payload(application, record)

    def set_mcp_server_enabled(self, request: BaseModel) -> dict[str, Any]:
        application = self._mcp()
        record = application.set_enabled(cast(McpServerSetEnabledInput, request))
        return self._mcp_server_payload(application, record)

    def delete_mcp_server(self, request: BaseModel) -> dict[str, Any]:
        server_id = self._mcp().delete(cast(McpServerDeleteInput, request))
        return {"server_id": server_id, "deleted": True}

    def _require_skill_digest(self, name: str, digest: str) -> None:
        package = self._skill_registry.get(name)
        if package is None:
            raise KeyError(f"Skill is not installed: {name}")
        if package.content_sha256 != digest:
            raise ValueError("Skill content changed since it was displayed")

    def _skills(self) -> SkillManager:
        if self._skill_manager is None:
            raise RuntimeError("Skill installation is unavailable")
        return self._skill_manager

    def _mcp(self) -> McpApplication:
        if self._mcp_application is None:
            raise RuntimeError("MCP extensions are unavailable")
        return self._mcp_application

    @staticmethod
    def _mcp_server_payload(application: McpApplication, record) -> dict[str, Any]:
        connection = record.connection
        return {
            "server_id": connection.server_id,
            "display_name": connection.display_name,
            "transport": connection.transport,
            "command": connection.command,
            "arguments": connection.arguments,
            "endpoint": connection.endpoint,
            "credential_configured": application.credential_configured(record),
            "environment_names": tuple(sorted(connection.environment_refs)),
            "enabled": record.enabled,
            "status": record.status,
            "accepted_schema_digest": record.accepted_schema_digest,
            "pending_schema_digest": record.pending_schema_digest,
            "accepted_tools": tuple(tool.as_dict() for tool in record.accepted_tools),
            "pending_tools": tuple(tool.as_dict() for tool in record.pending_tools),
            "policies": tuple(policy.as_dict() for policy in record.policies),
            "revision": record.revision,
            "last_error_code": record.last_error_code,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }


__all__ = ["ExtensionService"]
