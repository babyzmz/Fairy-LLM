from __future__ import annotations

import hashlib
import json
import re
import stat
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxPurpose

_MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
_URL = re.compile(r"https?://[^\s\"']+")
_HASHED_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.,-]+\])?==[^\s]+"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})+$"
)


class UnknownProjectManagerError(ValueError):
    error_code = "CAPABILITY_NOT_AVAILABLE"


class DependencyLockError(ValueError):
    error_code = "SECRET_EGRESS_BLOCKED"


class ProjectManagerConflictError(DependencyLockError):
    pass


class DependencyManager(StrEnum):
    NPM = "npm"
    PNPM = "pnpm"
    YARN = "yarn"
    UV = "uv"
    PIP = "pip"
    CARGO = "cargo"


class ReviewKind(StrEnum):
    TYPECHECK = "typecheck"
    LINT = "lint"
    TEST = "test"
    BUILD = "build"


@dataclass(frozen=True, slots=True)
class ExecutionTemplate:
    manager: DependencyManager
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    output_limit_bytes: int
    network_policy: SandboxNetworkPolicy
    purpose: SandboxPurpose

    def __post_init__(self) -> None:
        if not self.argv or any(not value or "\0" in value for value in self.argv):
            raise ValueError("execution template argv is invalid")
        if self.cwd != ".":
            raise ValueError("root execution templates must use cwd=.")
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("execution template timeout is invalid")
        if not 1_024 <= self.output_limit_bytes <= 1_048_576:
            raise ValueError("execution template output limit is invalid")


def dependency_template(project_root: Path) -> ExecutionTemplate:
    root = _project_root(project_root)
    manager = _detect_manager(root)
    _validate_lock(root, manager)
    return ExecutionTemplate(
        manager=manager,
        argv={
            DependencyManager.NPM: (
                "npm",
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ),
            DependencyManager.PNPM: (
                "pnpm",
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
            ),
            DependencyManager.YARN: (
                "yarn",
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
            ),
            DependencyManager.UV: (
                "uv",
                "sync",
                "--frozen",
                "--no-install-project",
            ),
            DependencyManager.PIP: (
                ".venv/bin/python",
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "-r",
                "requirements.lock",
            ),
            DependencyManager.CARGO: ("cargo", "fetch", "--locked"),
        }[manager],
        cwd=".",
        timeout_seconds=900,
        output_limit_bytes=1_048_576,
        network_policy=SandboxNetworkPolicy.PUBLIC,
        purpose=SandboxPurpose.DEPENDENCY,
    )


def dependency_layer_key(
    project_root: Path,
    manager: DependencyManager | None = None,
) -> str:
    root = _project_root(project_root)
    resolved_manager = manager or _detect_manager(root)
    lock_name = {
        DependencyManager.NPM: "package-lock.json",
        DependencyManager.PNPM: "pnpm-lock.yaml",
        DependencyManager.YARN: "yarn.lock",
        DependencyManager.UV: "uv.lock",
        DependencyManager.PIP: "requirements.lock",
        DependencyManager.CARGO: "Cargo.lock",
    }[resolved_manager]
    content = _bytes(root / lock_name)
    return hashlib.sha256(
        b"fairy-dependency-layer-v1\0" + resolved_manager.value.encode("ascii") + b"\0" + content
    ).hexdigest()


def review_template(project_root: Path, kind: ReviewKind) -> ExecutionTemplate:
    root = _project_root(project_root)
    manager = _detect_manager(root)
    commands: dict[DependencyManager, dict[ReviewKind, tuple[str, ...]]] = {
        DependencyManager.NPM: {
            ReviewKind.TYPECHECK: ("npm", "run", "typecheck", "--if-present"),
            ReviewKind.LINT: ("npm", "run", "lint", "--if-present"),
            ReviewKind.TEST: ("npm", "test", "--if-present"),
            ReviewKind.BUILD: ("npm", "run", "build", "--if-present"),
        },
        DependencyManager.PNPM: {
            ReviewKind.TYPECHECK: ("pnpm", "run", "typecheck", "--if-present"),
            ReviewKind.LINT: ("pnpm", "run", "lint", "--if-present"),
            ReviewKind.TEST: ("pnpm", "test", "--if-present"),
            ReviewKind.BUILD: ("pnpm", "run", "build", "--if-present"),
        },
        DependencyManager.YARN: {
            ReviewKind.TYPECHECK: ("yarn", "run", "typecheck"),
            ReviewKind.LINT: ("yarn", "run", "lint"),
            ReviewKind.TEST: ("yarn", "test"),
            ReviewKind.BUILD: ("yarn", "run", "build"),
        },
        DependencyManager.UV: _python_review_commands(),
        DependencyManager.PIP: _python_review_commands(),
        DependencyManager.CARGO: {
            ReviewKind.TYPECHECK: ("cargo", "check", "--locked"),
            ReviewKind.LINT: ("cargo", "clippy", "--locked", "--", "-D", "warnings"),
            ReviewKind.TEST: ("cargo", "test", "--locked"),
            ReviewKind.BUILD: ("cargo", "build", "--locked"),
        },
    }
    return ExecutionTemplate(
        manager=manager,
        argv=commands[manager][kind],
        cwd=".",
        timeout_seconds=900,
        output_limit_bytes=1_048_576,
        network_policy=SandboxNetworkPolicy.NONE,
        purpose=SandboxPurpose.REVIEW,
    )


def _python_review_commands() -> dict[ReviewKind, tuple[str, ...]]:
    return {
        ReviewKind.TYPECHECK: ("python3", "-m", "compileall", "-q", "."),
        ReviewKind.LINT: ("python3", "-m", "ruff", "check", "."),
        ReviewKind.TEST: ("python3", "-m", "pytest", "-q"),
        ReviewKind.BUILD: ("python3", "-m", "build", "--no-isolation"),
    }


def _detect_manager(root: Path) -> DependencyManager:
    candidates: list[DependencyManager] = []
    if _exists(root / "package-lock.json"):
        candidates.append(DependencyManager.NPM)
    if _exists(root / "pnpm-lock.yaml"):
        candidates.append(DependencyManager.PNPM)
    if _exists(root / "yarn.lock"):
        candidates.append(DependencyManager.YARN)
    if _exists(root / "uv.lock"):
        candidates.append(DependencyManager.UV)
    if _exists(root / "requirements.lock"):
        candidates.append(DependencyManager.PIP)
    if _exists(root / "Cargo.lock"):
        candidates.append(DependencyManager.CARGO)
    if len(candidates) > 1:
        raise ProjectManagerConflictError(
            "multiple dependency lockfiles require an explicit Core-owned workspace split"
        )
    if candidates:
        manager = candidates[0]
        required_manifest = {
            DependencyManager.NPM: "package.json",
            DependencyManager.PNPM: "package.json",
            DependencyManager.YARN: "package.json",
            DependencyManager.UV: "pyproject.toml",
            DependencyManager.PIP: None,
            DependencyManager.CARGO: "Cargo.toml",
        }[manager]
        if required_manifest is not None and not _exists(root / required_manifest):
            raise DependencyLockError(f"{required_manifest} is required for {manager.value}")
        return manager
    if any(
        _exists(root / name)
        for name in ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml")
    ):
        raise DependencyLockError("a supported immutable dependency lockfile is required")
    raise UnknownProjectManagerError("no supported project manager was detected")


def _validate_lock(root: Path, manager: DependencyManager) -> None:
    if manager is DependencyManager.NPM:
        values = _json_object(root / "package-lock.json")
        packages = values.get("packages", {})
        if not isinstance(packages, dict):
            raise DependencyLockError("package-lock packages must be an object")
        for package in packages.values():
            if not isinstance(package, dict):
                raise DependencyLockError("package-lock package entry is invalid")
            resolved = package.get("resolved")
            if resolved is not None and not _allowed_url(
                resolved,
                hosts={"registry.npmjs.org"},
            ):
                raise DependencyLockError("package-lock contains a non-registry source")
        return
    if manager in {DependencyManager.PNPM, DependencyManager.YARN}:
        name = "pnpm-lock.yaml" if manager is DependencyManager.PNPM else "yarn.lock"
        content = _text(root / name)
        if any(marker in content.casefold() for marker in ("git+", "github:", "file:", "link:")):
            raise DependencyLockError(f"{name} contains a non-registry source")
        for value in _URL.findall(content):
            if not _allowed_url(
                value.rstrip("),"),
                hosts={"registry.npmjs.org", "registry.yarnpkg.com"},
            ):
                raise DependencyLockError(f"{name} contains a non-registry source")
        return
    if manager is DependencyManager.UV:
        values = _toml_object(root / "uv.lock")
        packages = values.get("package", [])
        if not isinstance(packages, list):
            raise DependencyLockError("uv.lock package list is invalid")
        for package in packages:
            source = package.get("source") if isinstance(package, dict) else None
            if source is None:
                continue
            if not isinstance(source, dict) or source != {"registry": "https://pypi.org/simple"}:
                raise DependencyLockError("uv.lock contains a non-PyPI source")
        return
    if manager is DependencyManager.PIP:
        _validate_requirements_lock(_text(root / "requirements.lock"))
        return
    values = _toml_object(root / "Cargo.lock")
    packages = values.get("package", [])
    if not isinstance(packages, list):
        raise DependencyLockError("Cargo.lock package list is invalid")
    allowed_sources = {
        "registry+https://github.com/rust-lang/crates.io-index",
        "registry+sparse+https://index.crates.io/",
    }
    for package in packages:
        source = package.get("source") if isinstance(package, dict) else None
        if source is not None and source not in allowed_sources:
            raise DependencyLockError("Cargo.lock contains a non-crates.io source")


def _validate_requirements_lock(content: str) -> None:
    logical = content.replace("\\\r\n", " ").replace("\\\n", " ")
    requirements = [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    invalid = any(_HASHED_REQUIREMENT.fullmatch(line) is None for line in requirements)
    if not requirements or invalid:
        raise DependencyLockError("requirements.lock must contain hash-pinned packages")


def _allowed_url(value: object, *, hosts: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname in hosts
        and parsed.username is None
        and parsed.password is None
    )


def _project_root(value: Path) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except OSError as error:
        raise ValueError("project root is unavailable") from error
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    return root


def _exists(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DependencyLockError(f"project control file is not a regular file: {path.name}")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if getattr(metadata, "st_file_attributes", 0) & reparse_flag:
        raise DependencyLockError(f"project control file is a reparse point: {path.name}")
    if metadata.st_size > _MAX_CONTROL_FILE_BYTES:
        raise DependencyLockError(f"project control file is too large: {path.name}")
    return True


def _text(path: Path) -> str:
    if not _exists(path):
        raise DependencyLockError(f"project control file is missing: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DependencyLockError(f"project control file is invalid UTF-8: {path.name}") from error


def _bytes(path: Path) -> bytes:
    if not _exists(path):
        raise DependencyLockError(f"project control file is missing: {path.name}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise DependencyLockError(f"project control file is unavailable: {path.name}") from error


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_text(path))
    except json.JSONDecodeError as error:
        raise DependencyLockError(f"project control file is invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise DependencyLockError(f"project control file must be an object: {path.name}")
    return value


def _toml_object(path: Path) -> dict[str, object]:
    try:
        value = tomllib.loads(_text(path))
    except tomllib.TOMLDecodeError as error:
        raise DependencyLockError(f"project control file is invalid TOML: {path.name}") from error
    return value


__all__ = [
    "DependencyLockError",
    "DependencyManager",
    "ExecutionTemplate",
    "ProjectManagerConflictError",
    "ReviewKind",
    "UnknownProjectManagerError",
    "dependency_layer_key",
    "dependency_template",
    "review_template",
]
