from __future__ import annotations

import mimetypes
from hashlib import sha1
from pathlib import Path


class ImageCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path("data") / "asset_cache").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def namespace_dir(self, namespace: str) -> Path:
        safe_namespace = (namespace or "misc").strip().lower().replace("\\", "_").replace("/", "_")
        target = self.root / safe_namespace
        target.mkdir(parents=True, exist_ok=True)
        return target

    def build_cache_path(
        self,
        *,
        namespace: str,
        key: str,
        source: str = "",
        content_type: str = "",
        suffix_hint: str = "",
    ) -> Path:
        digest = sha1(f"{namespace}:{key}:{source}".encode("utf-8")).hexdigest()[:24]
        suffix = self._resolve_suffix(source=source, content_type=content_type, suffix_hint=suffix_hint)
        return self.namespace_dir(namespace) / f"{digest}{suffix}"

    def ensure_local_path(self, source: str) -> str:
        candidate = str(source or "").strip()
        if not candidate or candidate.startswith(("http://", "https://")):
            return ""
        path = Path(candidate)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return str(path) if path.exists() else ""

    def _resolve_suffix(self, *, source: str, content_type: str, suffix_hint: str) -> str:
        hint = (suffix_hint or "").strip()
        if hint and not hint.startswith("."):
            hint = f".{hint}"
        if hint:
            return hint.lower()

        mime_suffix = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
        if mime_suffix:
            return mime_suffix.lower()

        source_suffix = Path((source or "").split("?", 1)[0]).suffix.strip()
        if source_suffix:
            return source_suffix.lower()
        return ".img"
