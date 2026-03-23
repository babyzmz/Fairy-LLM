from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from app.services.assets.image_cache import ImageCache


logger = logging.getLogger(__name__)


class AssetFetcher:
    def __init__(
        self,
        *,
        cache: ImageCache | None = None,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.cache = cache or ImageCache()
        self.session = session or requests.Session()
        self.timeout = float(timeout)
        self.session.headers.setdefault("User-Agent", "Fairy/1.0 (desktop assistant)")

    def fetch_to_local(
        self,
        source: str,
        *,
        namespace: str,
        cache_key: str = "",
        content_kind: str = "image",
        suffix_hint: str = "",
        timeout: float | None = None,
    ) -> str:
        candidate = str(source or "").strip()
        if not candidate:
            return ""

        local_path = self.cache.ensure_local_path(candidate)
        if local_path:
            return local_path

        if not candidate.startswith(("http://", "https://")):
            return ""

        target = self.cache.build_cache_path(
            namespace=namespace,
            key=cache_key or candidate,
            source=candidate,
            suffix_hint=suffix_hint,
        )
        if target.exists() and target.stat().st_size > 0:
            return str(target)

        try:
            response = self.session.get(candidate, allow_redirects=True, timeout=timeout or self.timeout)
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type", "") or "").lower()
            if content_kind == "image" and "image" not in content_type:
                logger.warning("asset_fetch_rejected source=%s content_type=%s", candidate, content_type)
                return ""
            if not response.content:
                return ""
            target = self.cache.build_cache_path(
                namespace=namespace,
                key=cache_key or candidate,
                source=candidate,
                content_type=content_type,
                suffix_hint=suffix_hint,
            )
            target.write_bytes(response.content)
            return str(target)
        except Exception as exc:  # noqa: BLE001
            logger.warning("asset_fetch_failed source=%s error=%s", candidate, exc)
            return ""

    def normalize_local_path(self, source: str) -> str:
        return self.cache.ensure_local_path(source)

    def write_bytes(
        self,
        content: bytes,
        *,
        namespace: str,
        cache_key: str,
        source: str = "",
        content_type: str = "",
        suffix_hint: str = "",
    ) -> str:
        if not content:
            return ""
        target = self.cache.build_cache_path(
            namespace=namespace,
            key=cache_key,
            source=source,
            content_type=content_type,
            suffix_hint=suffix_hint,
        )
        target.write_bytes(content)
        return str(target)

    def cache_file_info(self, path: str) -> dict[str, Any]:
        resolved = self.normalize_local_path(path)
        if not resolved:
            return {}
        file_path = Path(resolved)
        return {
            "path": resolved,
            "size": file_path.stat().st_size,
            "name": file_path.name,
        }
