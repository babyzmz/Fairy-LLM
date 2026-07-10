from __future__ import annotations

TENANT_ID_LENGTH = 128


def normalize_tenant_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("tenant_id must not be empty")
    if len(normalized) > TENANT_ID_LENGTH:
        raise ValueError(f"tenant_id must not exceed {TENANT_ID_LENGTH} characters")
    return normalized
