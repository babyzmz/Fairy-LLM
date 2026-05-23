from __future__ import annotations

from pathlib import Path

from app.services.assets.image_cache import ImageCache


class AssetResolver:
    def __init__(self, *, cache: ImageCache | None = None, bundled_root: Path | None = None) -> None:
        self.cache = cache or ImageCache()
        self.bundled_root = (bundled_root or Path("app") / "assets" / "local").resolve()
        self.voice_root = (Path("app") / "ai" / "voice").resolve()
        self.voice_lines_root = (Path("data") / "voice_lines").resolve()

    def local_roots(self) -> list[Path]:
        return [
            self.cache.root.resolve(),
            self.bundled_root.resolve(),
            self.voice_root.resolve(),
            self.voice_lines_root.resolve(),
        ]

    def resolve_local_asset(self, path_value: str) -> str:
        candidate = str(path_value or "").strip()
        if not candidate:
            return ""
        local = self.cache.ensure_local_path(candidate)
        if local and self._is_allowed(Path(local)):
            return local
        prefixed = self._resolve_prefixed_asset(candidate)
        if prefixed:
            return prefixed
        logical = (self.bundled_root / candidate.replace("\\", "/")).resolve()
        if logical.exists() and self._is_allowed(logical):
            return str(logical)
        return ""

    def resolve_weather_icon(self, icon_key: str) -> str:
        normalized = self._normalize_key(icon_key) or "unknown"
        for key in (normalized, "unknown"):
            resolved = self.resolve_local_asset(f"weather/{key}.png")
            if resolved:
                return resolved
        return ""

    def resolve_news_thumbnail(self, path_value: str = "") -> str:
        resolved = self.resolve_local_asset(path_value)
        if resolved:
            return resolved
        return self.resolve_local_asset("news/generic-news.png")

    def _normalize_key(self, value: str) -> str:
        text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
        return "".join(ch for ch in text if ch.isalnum() or ch == "-")

    def _resolve_prefixed_asset(self, candidate: str) -> str:
        normalized = candidate.replace("\\", "/")
        if "/" not in normalized:
            return ""
        prefix, relative = normalized.split("/", 1)
        if not relative:
            return ""
        root = {
            "voice": self.voice_root,
            "voice_lines": self.voice_lines_root,
        }.get(prefix)
        if root is None:
            return ""
        logical = (root / relative).resolve()
        if logical.exists() and self._is_allowed(logical):
            return str(logical)
        return ""

    def _is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.local_roots():
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False
