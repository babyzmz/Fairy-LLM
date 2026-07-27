from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from check_release_bundle import REQUIRED_BUNDLE_FILES, validate_bundle_root, validate_configuration


class ReleaseBundlePolicyTests(unittest.TestCase):
    def test_repository_configuration_packages_required_release_resources(self) -> None:
        validate_configuration()

    def test_controlled_bundle_accepts_only_required_release_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in REQUIRED_BUNDLE_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("reviewed release resource", encoding="utf-8")
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
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for required in REQUIRED_BUNDLE_FILES:
                    path = root / required
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("reviewed release resource", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
