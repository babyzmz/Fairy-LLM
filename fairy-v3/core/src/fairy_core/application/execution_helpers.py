from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID


def execution_plan_requests_preview(manifest: Mapping[str, Any]) -> bool:
    entrypoints = manifest.get("entrypoints")
    if isinstance(entrypoints, list) and any(
        isinstance(value, str) and value.strip() for value in entrypoints
    ):
        return True
    files = manifest.get("files")
    if not isinstance(files, list):
        return False
    preview_markers = {
        "index.html",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "fairy.runtime.json",
    }
    return any(
        isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and str(item["path"]).replace("\\", "/").rsplit("/", 1)[-1] in preview_markers
        for item in files
    )


def tool_result_changeset_id(content: str | None) -> UUID | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return UUID(str(payload.get("changeset_id")))
    except (TypeError, ValueError):
        return None


__all__ = ["execution_plan_requests_preview", "tool_result_changeset_id"]
