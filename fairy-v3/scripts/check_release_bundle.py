from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"

REQUIRED_RESOURCES = {
    "resources/legal": "legal/licenses",
    "../../THIRD_PARTY_NOTICES.md": "legal/THIRD_PARTY_NOTICES.md",
    "../../docs/release": "docs/release",
    "resources/omni": "runtime/omni-manifests",
}
REQUIRED_BUNDLE_FILES = {
    "legal/THIRD_PARTY_NOTICES.md",
    "legal/licenses/llama.cpp-omni-LICENSE.txt",
    "legal/licenses/windows-capture-dda-LICENSE.txt",
    "legal/licenses/NVIDIA-CUDA-REDISTRIBUTION-NOTICE.txt",
    "docs/release/realtime-companion-beta-support.md",
    "docs/release/realtime-companion-beta-privacy.md",
    "docs/release/realtime-companion-beta-troubleshooting.md",
}
REQUIRED_VOICE_ASSET_DIGESTS = {
    "runtime/voice-assets/PROVENANCE.md": (
        "d8f295125420487315ee20500453eb1747d0e3dafd798778a573d70d99a6d278"
    ),
    "runtime/voice-assets/fairy_clone_core.txt": (
        "d17bab752c2203dd2752d26447487bafe91265bc4f65987f6f1f4c32145d7be8"
    ),
    "runtime/voice-assets/fairy_clone_core.wav": (
        "2308e292107a6b0b0dcced97b7f25c833a9d534ca0d71c097bbfb0b2e625f89c"
    ),
}
REQUIRED_BUNDLE_FILES.update(REQUIRED_VOICE_ASSET_DIGESTS)
REQUIRED_VOICE_LICENSES = {
    "runtime/voice-worker/THIRD_PARTY_LICENSES/CosyVoice-LICENSE.txt",
    "runtime/voice-worker/THIRD_PARTY_LICENSES/Matcha-TTS-LICENSE.txt",
}
REQUIRED_OMNI_RUNTIME_FILES = {
    "runtime/omni/build-profile.txt",
    "runtime/omni/cublas64_13.dll",
    "runtime/omni/cublasLt64_13.dll",
    "runtime/omni/fairy-omni-runtime.exe",
    "runtime/omni/runtime-components.json",
}
ALLOWED_OMNI_STAGE_FILES = REQUIRED_OMNI_RUNTIME_FILES | {"runtime/omni/.gitkeep"}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".dmp",
    ".env",
    ".gguf",
    ".log",
    ".partial",
    ".wav",
}
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "test-results",
}
FORBIDDEN_NAMES = {
    ".env",
    "main.py",
}


def validate_configuration(config_path: Path = CONFIG_PATH) -> None:
    configuration = json.loads(config_path.read_text(encoding="utf-8"))
    bundle = configuration["bundle"]
    if bundle.get("targets") != ["msi"]:
        raise AssertionError("release target must be exactly the Windows MSI")
    resources = bundle.get("resources", {})
    for source, destination in REQUIRED_RESOURCES.items():
        if resources.get(source) != destination:
            raise AssertionError(
                f"required release resource mapping is missing: {source} -> {destination}"
            )
    wix = bundle.get("windows", {}).get("wix", {})
    if wix.get("template") != "wix/main.wxs":
        raise AssertionError("custom external-cabinet WiX template is required")


def normalized_relative_files(bundle_root: Path) -> set[str]:
    resolved_root = bundle_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise AssertionError("bundle root must be a directory")
    files: set[str] = set()
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            raise AssertionError(f"bundle entry escapes its root: {path}")
        relative = resolved.relative_to(resolved_root)
        relative_posix = relative.as_posix()
        lowered_parts = {part.lower() for part in relative.parts}
        lowered_name = relative.name.lower()
        lowered_suffixes = {suffix.lower() for suffix in relative.suffixes}
        if lowered_parts & FORBIDDEN_PARTS:
            raise AssertionError(
                f"bundle contains a forbidden directory: {relative_posix}"
            )
        if lowered_name in FORBIDDEN_NAMES:
            raise AssertionError(
                f"bundle contains a forbidden legacy entry: {relative_posix}"
            )
        if (
            lowered_suffixes & FORBIDDEN_SUFFIXES
            and relative_posix not in REQUIRED_VOICE_ASSET_DIGESTS
        ):
            raise AssertionError(
                f"bundle contains a forbidden file type: {relative_posix}"
            )
        files.add(relative_posix)
    return files


def validate_bundle_root(bundle_root: Path) -> None:
    files = normalized_relative_files(bundle_root)
    missing = sorted(REQUIRED_BUNDLE_FILES - files)
    if missing:
        raise AssertionError(
            f"bundle is missing release resources: {', '.join(missing)}"
        )
    if any(path.startswith("runtime/voice-worker/") for path in files):
        missing_voice = sorted(REQUIRED_VOICE_LICENSES - files)
        if missing_voice:
            raise AssertionError(
                f"Voice runtime is missing upstream licenses: {', '.join(missing_voice)}"
            )
    for relative, expected_digest in REQUIRED_VOICE_ASSET_DIGESTS.items():
        with (bundle_root / relative).open("rb") as stream:
            actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_digest != expected_digest:
            raise AssertionError(f"Voice asset digest mismatch: {relative}")
    omni_files = {path for path in files if path.startswith("runtime/omni/")}
    if omni_files:
        missing_omni = sorted(REQUIRED_OMNI_RUNTIME_FILES - omni_files)
        if missing_omni:
            raise AssertionError(
                f"Omni runtime is missing governed components: {', '.join(missing_omni)}"
            )
        unknown_omni = sorted(omni_files - ALLOWED_OMNI_STAGE_FILES)
        if unknown_omni:
            raise AssertionError(
                f"Omni runtime contains unknown components: {', '.join(unknown_omni)}"
            )
        validate_omni_runtime(bundle_root)


def validate_omni_runtime(bundle_root: Path) -> None:
    omni_root = bundle_root / "runtime" / "omni"
    manifest_path = omni_root / "runtime-components.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {
        "schema_version",
        "build_profile",
        "runtime_compatibility",
        "upstream_revision",
        "patch_set_digest",
        "components",
    }:
        raise AssertionError("Omni runtime component manifest shape is not exact")
    if (
        manifest["schema_version"] != 1
        or manifest["build_profile"] != "production-cuda"
        or manifest["runtime_compatibility"] != "fairy-omni-runtime-v1"
        or not isinstance(manifest["upstream_revision"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", manifest["upstream_revision"])
        or not isinstance(manifest["patch_set_digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["patch_set_digest"])
        or not isinstance(manifest["components"], list)
    ):
        raise AssertionError("Omni runtime component manifest identity is invalid")

    expected_names = {
        "build-profile.txt",
        "cublas64_13.dll",
        "cublasLt64_13.dll",
        "fairy-omni-runtime.exe",
    }
    components = manifest["components"]
    names = [component.get("name") for component in components]
    if len(names) != len(set(names)) or set(names) != expected_names:
        raise AssertionError("Omni runtime component set is not exact")
    for component in components:
        if set(component) != {
            "name",
            "package",
            "version",
            "license",
            "bytes",
            "sha256",
        }:
            raise AssertionError("Omni runtime component metadata shape is not exact")
        name = component["name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)
            or name in {".", ".."}
            or not isinstance(component["package"], str)
            or not component["package"]
            or not isinstance(component["version"], str)
            or not component["version"]
            or not isinstance(component["license"], str)
            or not component["license"]
            or not isinstance(component["bytes"], int)
            or component["bytes"] <= 0
            or not isinstance(component["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", component["sha256"])
        ):
            raise AssertionError("Omni runtime component metadata is invalid")
        path = omni_root / name
        if path.stat().st_size != component["bytes"]:
            raise AssertionError(f"Omni runtime component size mismatch: {name}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != component["sha256"]:
            raise AssertionError(f"Omni runtime component digest mismatch: {name}")
    if (omni_root / "build-profile.txt").read_text(encoding="utf-8").strip() != (
        "production-cuda"
    ):
        raise AssertionError("Omni runtime staged profile is invalid")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--bundle-root", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_configuration(args.config)
    if args.bundle_root is not None:
        validate_bundle_root(args.bundle_root)
    print("Fairy release bundle policy passed.")


if __name__ == "__main__":
    main()
