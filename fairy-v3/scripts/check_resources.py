from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = ROOT / "resources"
MANIFEST_PATH = RESOURCE_ROOT / "manifest.json"
REQUIRED_FIELDS = {
    "id",
    "source_path",
    "destination_path",
    "sha256",
    "byte_length",
    "provenance_assertion",
    "license_assertion",
    "intended_use",
    "validation_status",
}


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise SystemExit("resource manifest schema_version must be 1")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise SystemExit("resource manifest assets must be a list")

    ids: set[str] = set()
    destinations: set[str] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or set(asset) != REQUIRED_FIELDS:
            raise SystemExit(f"resource asset {index} has an invalid field set")
        asset_id = required_text(asset, "id", index)
        source_path = PurePosixPath(required_text(asset, "source_path", index))
        destination_path = PurePosixPath(
            required_text(asset, "destination_path", index)
        )
        if source_path.is_absolute() or ".." in source_path.parts:
            raise SystemExit(f"resource asset {asset_id} has an unsafe source_path")
        if destination_path.is_absolute() or ".." in destination_path.parts:
            raise SystemExit(
                f"resource asset {asset_id} has an unsafe destination_path"
            )
        if asset_id in ids or destination_path.as_posix() in destinations:
            raise SystemExit(f"resource asset {asset_id} is duplicated")
        ids.add(asset_id)
        destinations.add(destination_path.as_posix())

        expected_hash = required_text(asset, "sha256", index)
        if len(expected_hash) != 64 or expected_hash != expected_hash.lower():
            raise SystemExit(f"resource asset {asset_id} has an invalid sha256")
        if asset.get("validation_status") != "approved":
            raise SystemExit(f"resource asset {asset_id} is not approved")
        for field in ("provenance_assertion", "license_assertion", "intended_use"):
            required_text(asset, field, index)

        destination = (RESOURCE_ROOT / Path(*destination_path.parts)).resolve()
        if (
            not destination.is_relative_to(RESOURCE_ROOT.resolve())
            or not destination.is_file()
        ):
            raise SystemExit(f"resource asset {asset_id} destination is missing")
        content = destination.read_bytes()
        if len(content) != asset.get("byte_length"):
            raise SystemExit(f"resource asset {asset_id} byte length does not match")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise SystemExit(f"resource asset {asset_id} digest does not match")

    actual_files = {
        path.relative_to(RESOURCE_ROOT).as_posix()
        for path in RESOURCE_ROOT.rglob("*")
        if path.is_file()
        and path.name not in {"README.md", "manifest.json"}
        and "__pycache__" not in path.parts
    }
    if actual_files != destinations:
        missing = sorted(destinations - actual_files)
        unlisted = sorted(actual_files - destinations)
        raise SystemExit(
            f"resource inventory mismatch; missing={missing!r}, unlisted={unlisted!r}"
        )
    print(f"validated {len(assets)} manifested Fairy resource(s)")


def required_text(asset: dict[str, object], field: str, index: int) -> str:
    value = asset.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"resource asset {index} field {field} must be non-empty text")
    return value


if __name__ == "__main__":
    main()
