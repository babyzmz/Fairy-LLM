from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from check_release_bundle import (
    REQUIRED_BUNDLE_FILES,
    validate_bundle_root,
    validate_configuration,
)


def populate_required_resources(root: Path) -> None:
    for relative in REQUIRED_BUNDLE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed release resource", encoding="utf-8")


def populate_omni_runtime(root: Path) -> None:
    omni_root = root / "runtime" / "omni"
    omni_root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "build-profile.txt": b"production-cuda\n",
        "cublas64_13.dll": b"fixture cublas",
        "cublasLt64_13.dll": b"fixture cublasLt",
        "fairy-omni-runtime.exe": b"MZ fixture omni",
    }
    components = []
    for name, data in payloads.items():
        (omni_root / name).write_bytes(data)
        is_cuda = name.startswith("cublas")
        components.append(
            {
                "name": name,
                "package": "libcublas" if is_cuda else "fairy-omni-runtime",
                "version": "13.1.1.3" if is_cuda else "fairy-omni-runtime-v1",
                "license": (
                    "LicenseRef-NVIDIA-End-User-License-Agreement"
                    if is_cuda
                    else "Fairy-and-third-party-notices"
                ),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "schema_version": 1,
        "build_profile": "production-cuda",
        "runtime_compatibility": "fairy-omni-runtime-v1",
        "upstream_revision": "7" * 40,
        "patch_set_digest": "8" * 64,
        "components": sorted(components, key=lambda component: component["name"]),
    }
    (omni_root / "runtime-components.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


class ReleaseBundlePolicyTests(unittest.TestCase):
    def test_repository_configuration_packages_required_release_resources(self) -> None:
        validate_configuration()

    def test_controlled_bundle_accepts_only_required_release_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            populate_required_resources(root)
            (root / "fairy.exe").write_bytes(b"MZ")
            validate_bundle_root(root)

    def test_forbidden_release_content_fails_closed(self) -> None:
        forbidden = (
            "models/MiniCPM-o-4_5-Q4_K_M.gguf",
            "data/core.db",
            "logs/realtime-worker.log",
            "media/session.wav",
            "legacy/main.py",
            "test-results/trace.zip",
            "configuration/.env",
        )
        for relative in forbidden:
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                populate_required_resources(root)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"forbidden")
                with self.assertRaisesRegex(AssertionError, "forbidden"):
                    validate_bundle_root(root)

    def test_missing_legal_resource_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in REQUIRED_BUNDLE_FILES - {"legal/THIRD_PARTY_NOTICES.md"}:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("reviewed release resource", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "missing release resources"):
                validate_bundle_root(root)

    def test_voice_runtime_requires_upstream_license_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            populate_required_resources(root)
            voice = root / "runtime/voice-worker/fairy-voice-worker.exe"
            voice.parent.mkdir(parents=True, exist_ok=True)
            voice.write_bytes(b"MZ")
            with self.assertRaisesRegex(AssertionError, "Voice runtime is missing"):
                validate_bundle_root(root)

    def test_complete_omni_runtime_is_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            populate_required_resources(root)
            populate_omni_runtime(root)
            validate_bundle_root(root)

    def test_missing_omni_runtime_component_fails_closed(self) -> None:
        for name in (
            "cublas64_13.dll",
            "cublasLt64_13.dll",
            "runtime-components.json",
        ):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                populate_required_resources(root)
                populate_omni_runtime(root)
                (root / "runtime" / "omni" / name).unlink()
                with self.assertRaisesRegex(AssertionError, "missing governed"):
                    validate_bundle_root(root)

    def test_tampered_omni_runtime_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            populate_required_resources(root)
            populate_omni_runtime(root)
            (root / "runtime" / "omni" / "cublas64_13.dll").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                AssertionError, "component (size|digest) mismatch"
            ):
                validate_bundle_root(root)

    def test_unknown_omni_runtime_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            populate_required_resources(root)
            populate_omni_runtime(root)
            (root / "runtime" / "omni" / "cudart64_13.dll").write_bytes(b"extra")
            with self.assertRaisesRegex(AssertionError, "unknown components"):
                validate_bundle_root(root)


if __name__ == "__main__":
    unittest.main()
