from __future__ import annotations

from app.services.assets.asset_fetcher import AssetFetcher


class ThumbnailService:
    def __init__(self, *, fetcher: AssetFetcher | None = None) -> None:
        self.fetcher = fetcher or AssetFetcher()

    def resolve_image(self, source: str, *, namespace: str = "thumbnails") -> str:
        local_path = self.fetcher.normalize_local_path(source)
        if local_path:
            return local_path
        return self.fetcher.fetch_to_local(source, namespace=namespace, cache_key=source, content_kind="image")
