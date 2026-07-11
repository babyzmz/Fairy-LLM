from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fairy_core.mcp.schema import McpSchemaError, sanitize_extension_input_schema

_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SkillProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    publisher: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=200)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: int
    name: str
    version: str
    description: str = Field(min_length=1, max_length=1_024)
    instructions: str
    input_schema: dict[str, Any]
    required_capabilities: tuple[str, ...] = ()
    compatible_mcp_servers: tuple[str, ...] = ()
    provenance: SkillProvenance
    content_sha256: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _SKILL_NAME.fullmatch(value) is None or "--" in value:
            raise ValueError("skill name is invalid")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if _SEMVER.fullmatch(value) is None:
            raise ValueError("skill version must use semantic versioning")
        return value

    @field_validator("content_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator("input_schema")
    @classmethod
    def _validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return sanitize_extension_input_schema(value)
        except McpSchemaError as error:
            raise ValueError(str(error).replace("MCP", "Skill")) from error

    @field_validator("required_capabilities")
    @classmethod
    def _validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values)
        if any(not value or len(value) > 128 for value in normalized):
            raise ValueError("required capability is invalid")
        if len(normalized) != len(set(normalized)):
            raise ValueError("required capabilities must be unique")
        return normalized

    @field_validator("compatible_mcp_servers")
    @classmethod
    def _validate_servers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values)
        if any(_SKILL_NAME.fullmatch(value) is None for value in normalized):
            raise ValueError("compatible MCP server ID is invalid")
        if len(normalized) != len(set(normalized)):
            raise ValueError("compatible MCP server IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_contract(self) -> SkillManifest:
        if self.schema_version != 1:
            raise ValueError("unsupported Fairy Skill manifest schema")
        if self.instructions != "SKILL.md":
            raise ValueError("instructions must reference the package SKILL.md")
        return self

    @property
    def tool_name(self) -> str:
        return f"skill.{self.name}"


@dataclass(frozen=True, slots=True)
class SkillPackage:
    manifest: SkillManifest
    instructions: str
    content_sha256: str
    resources: Mapping[str, bytes]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))


__all__ = ["SkillManifest", "SkillPackage", "SkillProvenance"]
