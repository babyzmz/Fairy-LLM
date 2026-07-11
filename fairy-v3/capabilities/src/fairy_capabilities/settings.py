from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fairy_core.providers import (
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
    SecretValue,
)

_SECRET_ENVIRONMENT_NAME = re.compile(r"^FAIRY_PROVIDER_SECRET_[A-Z0-9_]+$")
_PROFILE_KEYS = frozenset(
    {
        "id",
        "display_name",
        "kind",
        "base_url",
        "model_id",
        "capabilities",
        "credential_ref",
        "fallback_profile_id",
        "timeout_seconds",
        "enabled",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    profiles: tuple[ProviderProfile, ...]
    secret_environment_names: Mapping[str, str]

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> ProviderSettings:
        profile_values = _json_value(
            environment.get("FAIRY_PROVIDER_PROFILES_JSON", "[]"),
            "FAIRY_PROVIDER_PROFILES_JSON",
        )
        if not isinstance(profile_values, list):
            raise ValueError("FAIRY_PROVIDER_PROFILES_JSON must be an array")
        profiles = tuple(_profile(value) for value in profile_values)

        reference_values = _json_value(
            environment.get("FAIRY_PROVIDER_SECRET_REFS_JSON", "{}"),
            "FAIRY_PROVIDER_SECRET_REFS_JSON",
        )
        if not isinstance(reference_values, dict):
            raise ValueError("FAIRY_PROVIDER_SECRET_REFS_JSON must be an object")
        secret_environment_names: dict[str, str] = {}
        for raw_reference, raw_environment_name in reference_values.items():
            if not isinstance(raw_reference, str) or not raw_reference.strip():
                raise ValueError("provider secret reference names must be non-empty text")
            if not isinstance(raw_environment_name, str) or not _SECRET_ENVIRONMENT_NAME.fullmatch(
                raw_environment_name
            ):
                raise ValueError(
                    "provider secret environment names must start with FAIRY_PROVIDER_SECRET_"
                )
            secret_environment_names[raw_reference.strip()] = raw_environment_name
        return cls(
            profiles=profiles,
            secret_environment_names=MappingProxyType(secret_environment_names),
        )


class EnvironmentProviderSecretResolver:
    def __init__(
        self,
        secret_environment_names: Mapping[str, str],
        environment: Mapping[str, str],
    ) -> None:
        self._secret_environment_names = dict(secret_environment_names)
        self._environment = environment

    def resolve(self, reference: str) -> SecretValue:
        environment_name = self._secret_environment_names.get(reference)
        if environment_name is None:
            raise ValueError("provider secret reference is not configured")
        value = self._environment.get(environment_name, "")
        if not value.strip():
            raise ValueError("provider secret value is not configured")
        return SecretValue.from_text(value)

    def try_resolve(self, reference: str | None) -> SecretValue | None:
        if reference is None:
            return None
        try:
            return self.resolve(reference)
        except ValueError:
            return None


def _profile(value: Any) -> ProviderProfile:
    if not isinstance(value, dict):
        raise ValueError("provider profile entries must be objects")
    unknown = set(value) - _PROFILE_KEYS
    if unknown:
        raise ValueError(
            "provider profile contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    raw_capabilities = value.get("capabilities")
    if not isinstance(raw_capabilities, list) or not all(
        isinstance(capability, str) for capability in raw_capabilities
    ):
        raise ValueError("provider profile capabilities must be a string array")
    try:
        return ProviderProfile.create(
            profile_id=_string(value, "id"),
            display_name=_string(value, "display_name"),
            kind=ProviderKind(_string(value, "kind")),
            base_url=_string(value, "base_url"),
            model_id=_string(value, "model_id"),
            capabilities=frozenset(
                ProviderCapability(capability) for capability in raw_capabilities
            ),
            credential_ref=_optional_string(value.get("credential_ref")),
            fallback_profile_id=_optional_string(value.get("fallback_profile_id")),
            timeout_seconds=_number(value, "timeout_seconds"),
            enabled=_boolean(value, "enabled"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid provider profile: {error}") from error


def _json_value(raw: str, field_name: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field_name} must contain valid JSON") from error


def _string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise ValueError(f"provider profile {key} must be text")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional provider profile value must be text")
    return value


def _number(value: Mapping[str, Any], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ValueError(f"provider profile {key} must be a number")
    return float(result)


def _boolean(value: Mapping[str, Any], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise ValueError(f"provider profile {key} must be a boolean")
    return result
