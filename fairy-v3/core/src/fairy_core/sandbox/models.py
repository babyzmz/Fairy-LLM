from __future__ import annotations

import hashlib
import json
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from uuid import UUID

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FORBIDDEN_ENVIRONMENT = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "HOME",
        "IFS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PROMPT_COMMAND",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELLOPTS",
        "WSLENV",
    }
)
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_ARGUMENTS = 64
_MAX_ARGUMENT_BYTES = 4_096
_MAX_ENVIRONMENT = 32
_MAX_ENVIRONMENT_VALUE_BYTES = 8_192
_MAX_OUTPUT_BYTES = 1_048_576


class SandboxNetworkPolicy(StrEnum):
    NONE = "none"
    PUBLIC = "public"


class SandboxPurpose(StrEnum):
    RAW = "raw"
    INSPECT = "inspect"
    DEPENDENCY = "dependency"
    REVIEW = "review"


class SandboxResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    job_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    workspace_generation: int
    lease_fence: int
    argv: tuple[str, ...]
    cwd: str
    environment: Mapping[str, str]
    timeout_seconds: int
    output_limit_bytes: int
    network_policy: SandboxNetworkPolicy
    purpose: SandboxPurpose
    dependency_key: str | None
    dependency_manager: str | None
    workspace_archive: bytes
    archive_sha256: str

    @classmethod
    def create(
        cls,
        *,
        job_id: UUID,
        project_id: UUID | None,
        conversation_id: UUID,
        task_id: UUID,
        version_id: UUID | None,
        scope_digest: str,
        workspace_generation: int,
        lease_fence: int,
        argv: tuple[str, ...],
        cwd: str,
        environment: Mapping[str, str],
        timeout_seconds: int,
        output_limit_bytes: int,
        network_policy: SandboxNetworkPolicy,
        workspace_archive: bytes,
        purpose: SandboxPurpose = SandboxPurpose.RAW,
        dependency_key: str | None = None,
        dependency_manager: str | None = None,
    ) -> SandboxRequest:
        if version_id is None:
            raise ValueError("Sandbox requests require an immutable Workspace Version")
        if _DIGEST.fullmatch(scope_digest) is None:
            raise ValueError("scope_digest must be lowercase SHA-256")
        if isinstance(workspace_generation, bool) or workspace_generation < 1:
            raise ValueError("workspace generation must be positive")
        if isinstance(lease_fence, bool) or lease_fence < 1:
            raise ValueError("lease fence must be positive")
        normalized_argv = _validate_argv(argv)
        normalized_cwd = _validate_cwd(cwd)
        normalized_environment = _validate_environment(environment)
        if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")
        if (
            isinstance(output_limit_bytes, bool)
            or not 1_024 <= output_limit_bytes <= _MAX_OUTPUT_BYTES
        ):
            raise ValueError("output_limit_bytes must be between 1024 and 1048576")
        if not isinstance(network_policy, SandboxNetworkPolicy):
            raise ValueError("network_policy must be a SandboxNetworkPolicy")
        if not isinstance(purpose, SandboxPurpose):
            raise ValueError("purpose must be a SandboxPurpose")
        if network_policy is SandboxNetworkPolicy.PUBLIC and purpose not in {
            SandboxPurpose.RAW,
            SandboxPurpose.DEPENDENCY,
        }:
            raise ValueError("public network is incompatible with the Sandbox purpose")
        dependency_aware = purpose in {SandboxPurpose.DEPENDENCY, SandboxPurpose.REVIEW}
        valid_manager = dependency_manager in {"npm", "pnpm", "yarn", "uv", "pip", "cargo"}
        if dependency_aware:
            if (
                version_id is None
                or not isinstance(dependency_key, str)
                or _DIGEST.fullmatch(dependency_key) is None
                or not valid_manager
            ):
                raise ValueError(
                    "dependency layer requires a Workspace Version, key, and supported manager"
                )
        elif dependency_key is not None or dependency_manager is not None:
            raise ValueError("this Sandbox purpose cannot bind a dependency layer")
        if purpose is SandboxPurpose.INSPECT:
            normalized_argv = _validate_inspection_argv(normalized_argv)
            if normalized_environment:
                raise ValueError("inspection requests cannot define environment variables")
            if timeout_seconds > 30:
                raise ValueError("inspection timeout cannot exceed 30 seconds")
            if output_limit_bytes > 262_144:
                raise ValueError("inspection output cannot exceed 262144 bytes")
        archive = bytes(workspace_archive)
        if not archive or len(archive) > _MAX_ARCHIVE_BYTES:
            raise ValueError("workspace archive is empty or exceeds 128 MiB")
        return cls(
            job_id=job_id,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            scope_digest=scope_digest,
            workspace_generation=workspace_generation,
            lease_fence=lease_fence,
            argv=normalized_argv,
            cwd=normalized_cwd,
            environment=MappingProxyType(normalized_environment),
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            network_policy=network_policy,
            purpose=purpose,
            dependency_key=dependency_key,
            dependency_manager=dependency_manager,
            workspace_archive=archive,
            archive_sha256=hashlib.sha256(archive).hexdigest(),
        )

    def header(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "job_id": str(self.job_id),
            "project_id": str(self.project_id) if self.project_id else None,
            "conversation_id": str(self.conversation_id),
            "task_id": str(self.task_id),
            "version_id": str(self.version_id) if self.version_id else None,
            "scope_digest": self.scope_digest,
            "workspace_generation": self.workspace_generation,
            "lease_fence": self.lease_fence,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "environment": dict(self.environment),
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "network_policy": self.network_policy.value,
            "purpose": self.purpose.value,
            "dependency_key": self.dependency_key,
            "dependency_manager": self.dependency_manager,
            "archive_byte_length": len(self.workspace_archive),
            "archive_sha256": self.archive_sha256,
        }


@dataclass(frozen=True, slots=True)
class SandboxResult:
    job_id: UUID
    executor: str
    executor_version: str
    scope_digest: str
    workspace_generation: int
    lease_fence: int
    status: SandboxResultStatus
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_sha256: str
    stderr_sha256: str
    output_truncated: bool
    started_at: datetime
    finished_at: datetime

    @classmethod
    def create(
        cls,
        *,
        request: SandboxRequest,
        executor: str,
        executor_version: str,
        status: SandboxResultStatus,
        exit_code: int | None,
        stdout: bytes,
        stderr: bytes,
        output_truncated: bool,
        started_at: datetime,
        finished_at: datetime,
    ) -> SandboxResult:
        normalized_executor = executor.strip()
        normalized_version = executor_version.strip()
        if not normalized_executor or not normalized_version:
            raise ValueError("executor identity and version are required")
        if started_at.tzinfo is None or finished_at.tzinfo is None:
            raise ValueError("Sandbox result timestamps must include a timezone")
        started = started_at.astimezone(UTC)
        finished = finished_at.astimezone(UTC)
        if finished < started:
            raise ValueError("finished_at cannot be before started_at")
        out = bytes(stdout)
        err = bytes(stderr)
        if len(out) + len(err) > request.output_limit_bytes:
            raise ValueError("Sandbox result exceeds the output limit")
        if not isinstance(status, SandboxResultStatus):
            raise ValueError("status must be a SandboxResultStatus")
        if isinstance(exit_code, bool) or (
            exit_code is not None and not isinstance(exit_code, int)
        ):
            raise ValueError("Sandbox result exit code is invalid")
        if status is SandboxResultStatus.COMPLETED and exit_code != 0:
            raise ValueError("completed Sandbox result requires exit code zero")
        if status in {SandboxResultStatus.TIMED_OUT, SandboxResultStatus.CANCELLED} and (
            exit_code is not None
        ):
            raise ValueError("timed out or cancelled Sandbox result cannot have an exit code")
        if not isinstance(output_truncated, bool):
            raise ValueError("output_truncated must be a boolean")
        return cls(
            job_id=request.job_id,
            executor=normalized_executor,
            executor_version=normalized_version,
            scope_digest=request.scope_digest,
            workspace_generation=request.workspace_generation,
            lease_fence=request.lease_fence,
            status=status,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            stdout_sha256=hashlib.sha256(out).hexdigest(),
            stderr_sha256=hashlib.sha256(err).hexdigest(),
            output_truncated=output_truncated,
            started_at=started,
            finished_at=finished,
        )


def encode_request_frame(request: SandboxRequest) -> bytes:
    header = json.dumps(
        request.header(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return struct.pack(">I", len(header)) + header + request.workspace_archive


def decode_request_frame(frame: bytes) -> tuple[dict[str, object], bytes]:
    if len(frame) < 4:
        raise ValueError("Sandbox request frame is truncated")
    header_length = struct.unpack(">I", frame[:4])[0]
    if header_length < 2 or header_length > 1_000_000 or len(frame) < 4 + header_length:
        raise ValueError("Sandbox request header length is invalid")
    try:
        header = json.loads(frame[4 : 4 + header_length])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Sandbox request header is invalid JSON") from error
    if not isinstance(header, dict):
        raise ValueError("Sandbox request header must be an object")
    archive = frame[4 + header_length :]
    if header.get("archive_byte_length") != len(archive):
        raise ValueError("Sandbox archive byte length does not match")
    if header.get("archive_sha256") != hashlib.sha256(archive).hexdigest():
        raise ValueError("Sandbox archive hash does not match")
    return header, archive


def _validate_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv or len(argv) > _MAX_ARGUMENTS:
        raise ValueError("argv must contain between 1 and 64 items")
    normalized: list[str] = []
    for value in argv:
        if not isinstance(value, str):
            raise ValueError("argv items must be strings")
        if "\0" in value:
            raise ValueError("argv items cannot contain NUL")
        if not value or len(value.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
            raise ValueError("argv item is empty or too long")
        normalized.append(value)
    return tuple(normalized)


_INSPECTION_PROGRAMS = frozenset({"head", "ls", "rg", "stat", "tail", "wc"})
_SHELL_SYNTAX = re.compile(r"[\r\n|;&`<>]|\$\(")
_UNSAFE_RG_OPTIONS = ("--pre", "--pre-glob", "--hostname-bin")


def _validate_inspection_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    normalized = _validate_argv(argv)
    if normalized[0] not in _INSPECTION_PROGRAMS:
        raise ValueError("inspection program is not permitted")
    for index, value in enumerate(normalized):
        if _SHELL_SYNTAX.search(value):
            raise ValueError("inspection arguments cannot contain shell syntax")
        if index > 0 and ("\\" in value or value.startswith("/")):
            raise ValueError("inspection paths must be relative POSIX paths")
        if index > 0 and ".." in PurePosixPath(value).parts:
            raise ValueError("inspection paths must remain inside the Workspace")
        if normalized[0] == "rg" and value.startswith(_UNSAFE_RG_OPTIONS):
            raise ValueError("inspection ripgrep option is not permitted")
    return normalized


def _validate_cwd(cwd: str) -> str:
    normalized = cwd.replace("\\", "/").strip() or "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "\0" in normalized or ":" in normalized:
        raise ValueError("cwd must remain inside the Sandbox Workspace")
    return path.as_posix()


def _validate_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if len(environment) > _MAX_ENVIRONMENT:
        raise ValueError("environment has too many entries")
    normalized: dict[str, str] = {}
    for name, value in environment.items():
        if (
            not isinstance(name, str)
            or _ENVIRONMENT_NAME.fullmatch(name) is None
            or name in _FORBIDDEN_ENVIRONMENT
            or name.startswith(("DYLD_", "LD_"))
        ):
            raise ValueError(f"environment name is forbidden: {name}")
        if (
            not isinstance(value, str)
            or "\0" in value
            or len(value.encode("utf-8")) > _MAX_ENVIRONMENT_VALUE_BYTES
        ):
            raise ValueError(f"environment value is invalid: {name}")
        normalized[name] = value
    return dict(sorted(normalized.items()))


__all__ = [
    "SandboxNetworkPolicy",
    "SandboxPurpose",
    "SandboxRequest",
    "SandboxResult",
    "SandboxResultStatus",
    "_validate_inspection_argv",
    "decode_request_frame",
    "encode_request_frame",
]
