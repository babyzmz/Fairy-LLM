from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_BUNDLE_FILES = frozenset(
    {"metadata.json", "metadata.sig", "manifest.json", "manifest.sig", "payload.zip"}
)


class RendererPackVerificationError(ValueError):
    code = "RENDERER_PACK_UNTRUSTED"


@dataclass(frozen=True, slots=True)
class RendererPackManifest:
    id: str
    version: str
    platform: str
    input_media_types: tuple[str, ...]
    output_media_types: tuple[str, ...]
    features: tuple[str, ...]
    limits: Mapping[str, int]
    license: str
    sandbox: str
    reproducible: bool
    entrypoint: str
    payload_sha256: str

    @classmethod
    def parse(cls, payload: bytes) -> RendererPackManifest:
        try:
            data = json.loads(payload)
            manifest = cls(
                id=str(data["id"]),
                version=str(data["version"]),
                platform=str(data["platform"]),
                input_media_types=tuple(map(str, data["input_media_types"])),
                output_media_types=tuple(map(str, data["output_media_types"])),
                features=tuple(map(str, data.get("features", []))),
                limits={str(key): int(value) for key, value in data["limits"].items()},
                license=str(data["license"]),
                sandbox=str(data["sandbox"]),
                reproducible=bool(data["reproducible"]),
                entrypoint=str(data["entrypoint"]),
                payload_sha256=str(data["payload_sha256"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RendererPackVerificationError("invalid Renderer Pack manifest") from error
        if not manifest.id or not manifest.version or manifest.sandbox != "native_restricted":
            raise RendererPackVerificationError("Renderer Pack requires a restricted sandbox")
        _require_digest(manifest.payload_sha256)
        _safe_relative(manifest.entrypoint)
        return manifest


@dataclass(frozen=True, slots=True)
class InstalledRendererPack:
    manifest: RendererPackManifest
    install_path: Path


@dataclass(frozen=True, slots=True)
class RendererPackRecord:
    manifest: RendererPackManifest
    install_path: Path
    health: str
    installed_at: str


AuthenticodeVerifier = Callable[[Path], bool]


class RendererPackInstaller:
    def __init__(
        self,
        root: Path,
        *,
        trusted_keys: Mapping[str, bytes],
        authenticode_verifier: AuthenticodeVerifier | None = None,
    ) -> None:
        self._root = root.resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)
        self._trusted_keys = dict(trusted_keys)
        self._authenticode = authenticode_verifier

    def verify_and_install(
        self, bundle_path: Path, *, user_confirmed: bool
    ) -> InstalledRendererPack:
        if not user_confirmed:
            raise PermissionError("Renderer Pack installation requires explicit user confirmation")
        bundle_path = bundle_path.resolve(strict=True)
        with zipfile.ZipFile(bundle_path) as bundle:
            if (
                len(bundle.infolist()) != len(_BUNDLE_FILES)
                or set(bundle.namelist()) != _BUNDLE_FILES
            ):
                raise RendererPackVerificationError("Renderer Pack bundle layout is invalid")
            metadata_bytes = bundle.read("metadata.json")
            metadata = _parse_metadata(metadata_bytes)
            key = self._key(metadata["key_id"])
            _verify(key, metadata_bytes, bundle.read("metadata.sig"), "metadata")
            manifest_bytes = bundle.read("manifest.json")
            _verify(key, manifest_bytes, bundle.read("manifest.sig"), "manifest")
            if hashlib.sha256(manifest_bytes).hexdigest() != metadata["manifest_sha256"]:
                raise RendererPackVerificationError("Renderer Pack manifest digest is invalid")
            payload = bundle.read("payload.zip")
        if hashlib.sha256(payload).hexdigest() != metadata["payload_sha256"]:
            raise RendererPackVerificationError("Renderer Pack payload digest is invalid")
        manifest = RendererPackManifest.parse(manifest_bytes)
        if manifest.payload_sha256 != metadata["payload_sha256"]:
            raise RendererPackVerificationError("Renderer Pack payload digest disagrees")

        target = self._root / manifest.id / manifest.version
        staging = Path(tempfile.mkdtemp(prefix="renderer-pack-", dir=self._root))
        try:
            payload_path = staging / "payload.zip"
            payload_path.write_bytes(payload)
            extracted = staging / "content"
            extracted.mkdir()
            with zipfile.ZipFile(payload_path) as archive:
                self._extract_payload(archive, extracted)
            entrypoint = extracted / Path(*PurePosixPath(manifest.entrypoint).parts)
            if not entrypoint.is_file():
                raise RendererPackVerificationError("Renderer Pack entrypoint is missing")
            executables = [
                path for path in extracted.rglob("*") if path.suffix.lower() in {".exe", ".dll"}
            ]
            if executables and self._authenticode is None:
                raise RendererPackVerificationError("Authenticode verification is unavailable")
            if self._authenticode is not None and any(
                not self._authenticode(path) for path in executables
            ):
                raise RendererPackVerificationError("Renderer Pack Authenticode check failed")
            (extracted / ".verified-manifest.json").write_bytes(manifest_bytes)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                installed_manifest = target / ".verified-manifest.json"
                if (
                    installed_manifest.is_file()
                    and installed_manifest.read_bytes() == manifest_bytes
                ):
                    return InstalledRendererPack(manifest, target)
                raise FileExistsError(
                    "a different Renderer Pack is already installed at this version"
                )
            os.replace(extracted, target)
            return InstalledRendererPack(manifest, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _key(self, key_id: str) -> Ed25519PublicKey:
        raw = self._trusted_keys.get(key_id)
        if raw is None:
            raise RendererPackVerificationError("Renderer Pack signing key is not trusted")
        return Ed25519PublicKey.from_public_bytes(raw)

    @staticmethod
    def _extract_payload(archive: zipfile.ZipFile, target: Path) -> None:
        if len(archive.infolist()) > 10_000:
            raise RendererPackVerificationError("Renderer Pack contains too many members")
        total_size = 0
        for info in archive.infolist():
            relative = _safe_relative(info.filename)
            if info.flag_bits & 1:
                raise RendererPackVerificationError(
                    "encrypted Renderer Pack members are not allowed"
                )
            if info.is_dir():
                (target / Path(*relative.parts)).mkdir(parents=True, exist_ok=True)
                continue
            if info.file_size > 2 * 1024 * 1024 * 1024:
                raise RendererPackVerificationError("Renderer Pack member exceeds size limit")
            total_size += info.file_size
            if total_size > 4 * 1024 * 1024 * 1024:
                raise RendererPackVerificationError("Renderer Pack exceeds extracted size limit")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise RendererPackVerificationError("Renderer Pack symlinks are not allowed")
            destination = target / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _parse_metadata(payload: bytes) -> dict[str, str]:
    try:
        data: dict[str, Any] = json.loads(payload)
        result = {
            "key_id": str(data["key_id"]),
            "manifest_sha256": str(data["manifest_sha256"]),
            "payload_sha256": str(data["payload_sha256"]),
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RendererPackVerificationError("invalid Renderer Pack metadata") from error
    _require_digest(result["manifest_sha256"])
    _require_digest(result["payload_sha256"])
    return result


def _verify(key: Ed25519PublicKey, payload: bytes, signature: bytes, label: str) -> None:
    try:
        key.verify(signature, payload)
    except InvalidSignature as error:
        raise RendererPackVerificationError(
            f"Renderer Pack {label} signature is invalid"
        ) from error


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RendererPackVerificationError("Renderer Pack contains an unsafe path")
    if ":" in value:
        raise RendererPackVerificationError("Renderer Pack contains an unsafe path")
    return path


def _require_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RendererPackVerificationError("Renderer Pack digest must be lowercase SHA-256")


__all__ = [
    "InstalledRendererPack",
    "RendererPackInstaller",
    "RendererPackManifest",
    "RendererPackRecord",
    "RendererPackVerificationError",
]
