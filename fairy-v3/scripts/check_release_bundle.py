from __future__ import annotations

import argparse
import json
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
    "docs/release/realtime-companion-beta-support.md",
    "docs/release/realtime-companion-beta-privacy.md",
    "docs/release/realtime-companion-beta-troubleshooting.md",
}
REQUIRED_VOICE_LICENSES = {
    "runtime/voice-worker/THIRD_PARTY_LICENSES/CosyVoice-LICENSE.txt",
    "runtime/voice-worker/THIRD_PARTY_LICENSES/Matcha-TTS-LICENSE.txt",
}
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
            raise AssertionError(f"bundle contains a forbidden directory: {relative_posix}")
        if lowered_name in FORBIDDEN_NAMES:
            raise AssertionError(f"bundle contains a forbidden legacy entry: {relative_posix}")
        if lowered_suffixes & FORBIDDEN_SUFFIXES:
            raise AssertionError(f"bundle contains a forbidden file type: {relative_posix}")
        files.add(relative_posix)
    return files


def validate_bundle_root(bundle_root: Path) -> None:
    files = normalized_relative_files(bundle_root)
    missing = sorted(REQUIRED_BUNDLE_FILES - files)
    if missing:
        raise AssertionError(f"bundle is missing release resources: {', '.join(missing)}")
    if any(path.startswith("runtime/voice-worker/") for path in files):
        missing_voice = sorted(REQUIRED_VOICE_LICENSES - files)
        if missing_voice:
            raise AssertionError(
                f"Voice runtime is missing upstream licenses: {', '.join(missing_voice)}"
            )


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
