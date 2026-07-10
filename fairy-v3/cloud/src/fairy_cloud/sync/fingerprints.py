from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_payload_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
