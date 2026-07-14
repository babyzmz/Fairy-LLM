from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def presentation_cache_key(
    *,
    source_hash: str,
    dependency_hashes: Sequence[str],
    renderer_pack_id: str,
    renderer_pack_version: str,
    parameters: Mapping[str, Any],
    platform: str,
    color_configuration: str,
) -> str:
    payload = {
        "color_configuration": color_configuration,
        "dependency_hashes": sorted(dependency_hashes),
        "parameters": parameters,
        "platform": platform,
        "renderer_pack_id": renderer_pack_id,
        "renderer_pack_version": renderer_pack_version,
        "source_hash": source_hash,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["presentation_cache_key"]
