from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.asset_sets import AssetSet, AssetVariant

_SECRET_KEY = re.compile(r"(?:api.?key|authorization|password|secret|token)", re.IGNORECASE)


class AssetSetApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspaces: WorkspaceApplication,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspaces

    def create(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        kind: str,
        title: str,
        variants: tuple[dict[str, str], ...],
        provenance: dict[str, object],
        generation_parameters: dict[str, object],
        idempotency_key: str,
    ) -> AssetSet:
        safe_provenance = _safe_metadata(provenance, "provenance")
        safe_parameters = _safe_metadata(generation_parameters, "generation parameters")
        input_digest = hashlib.sha256(
            json.dumps(
                {
                    "workspace_id": str(workspace_id),
                    "version_id": str(version_id),
                    "kind": kind,
                    "title": title,
                    "variants": variants,
                    "provenance": safe_provenance,
                    "generation_parameters": safe_parameters,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.presentations.find_asset_set_by_idempotency_key(
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
            )
        if existing is not None:
            if existing.input_digest != input_digest:
                raise IdempotencyConflictError(
                    "Asset Set idempotency key was already used for different input"
                )
            return existing
        resolved = []
        for variant in variants:
            descriptor = self._workspaces.probe_file(
                workspace_id=workspace_id,
                version_id=version_id,
                path=variant["path"],
            )
            if descriptor.content_hash != variant["content_hash"]:
                raise ValueError("Asset Set variant hash no longer matches the Workspace Version")
            if not descriptor.media_type.startswith(f"{kind}/"):
                raise ValueError("Asset Set variant media type does not match its kind")
            resolved.append(
                AssetVariant(
                    path=descriptor.path,
                    content_hash=descriptor.content_hash,
                    byte_length=descriptor.byte_length,
                    media_type=descriptor.media_type,
                    role=variant["role"],
                    label=variant["label"],
                )
            )
        asset_set = AssetSet.create(
            workspace_id=workspace_id,
            version_id=version_id,
            kind=kind,
            title=title,
            variants=tuple(resolved),
            provenance=safe_provenance,
            generation_parameters=safe_parameters,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
        )
        try:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.presentations.save_asset_set(asset_set)
                unit_of_work.commit()
        except IntegrityError as conflict:
            # A concurrent retry can win the unique idempotency-key insert.
            with self._unit_of_work_factory() as unit_of_work:
                existing = unit_of_work.presentations.find_asset_set_by_idempotency_key(
                    workspace_id=workspace_id,
                    idempotency_key=idempotency_key,
                )
            if existing is None:
                raise
            if existing.input_digest != input_digest:
                raise IdempotencyConflictError(
                    "Asset Set idempotency key was already used for different input"
                ) from conflict
            return existing
        return asset_set

    def list(self, *, workspace_id: UUID, version_id: UUID) -> tuple[AssetSet, ...]:
        self._workspaces.files(workspace_id=workspace_id, version_id=version_id)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.presentations.list_asset_sets(
                workspace_id=workspace_id,
                version_id=version_id,
            )


def _safe_metadata(value: dict[str, object], label: str) -> dict[str, object]:
    def validate(item: object, *, depth: int) -> None:
        if depth > 6:
            raise ValueError(f"Asset Set {label} is nested too deeply")
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str) or len(key) > 128 or _SECRET_KEY.search(key):
                    raise ValueError(f"Asset Set {label} contains a prohibited key")
                validate(nested, depth=depth + 1)
        elif isinstance(item, list):
            if len(item) > 256:
                raise ValueError(f"Asset Set {label} contains too many values")
            for nested in item:
                validate(nested, depth=depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"Asset Set {label} contains an unsupported value")
        elif isinstance(item, str) and len(item) > 4000:
            raise ValueError(f"Asset Set {label} contains an oversized value")

    validate(value, depth=0)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    if len(encoded) > 64 * 1024:
        raise ValueError(f"Asset Set {label} exceeds its size limit")
    return json.loads(encoded)


__all__ = ["AssetSetApplication"]
