from __future__ import annotations

from pathlib import Path

from app.services.assets.image_cache import ImageCache


class AssetResolver:
    def __init__(self, *, cache: ImageCache | None = None, bundled_root: Path | None = None) -> None:
        self.cache = cache or ImageCache()
        self.bundled_root = (bundled_root or Path("app") / "assets" / "local").resolve()

    def local_roots(self) -> list[Path]:
        return [self.cache.root.resolve(), self.bundled_root.resolve()]

    def resolve_local_asset(self, path_value: str) -> str:
        candidate = str(path_value or "").strip()
        if not candidate:
            return ""
        local = self.cache.ensure_local_path(candidate)
        if local and self._is_allowed(Path(local)):
            return local
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

    def _is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.local_roots():
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False
