from __future__ import annotations

from app.services.assets.asset_fetcher import AssetFetcher
from app.services.assets.asset_resolver import AssetResolver
from app.services.assets.image_cache import ImageCache
from app.services.assets.map_preview_service import MapPreviewService
from app.services.assets.thumbnail_service import ThumbnailService

_image_cache: ImageCache | None = None
_asset_fetcher: AssetFetcher | None = None
_asset_resolver: AssetResolver | None = None
_map_preview_service: MapPreviewService | None = None
_thumbnail_service: ThumbnailService | None = None


def get_image_cache() -> ImageCache:
    global _image_cache
    if _image_cache is None:
        _image_cache = ImageCache()
    return _image_cache


def get_asset_fetcher() -> AssetFetcher:
    global _asset_fetcher
    if _asset_fetcher is None:
        _asset_fetcher = AssetFetcher(cache=get_image_cache())
    return _asset_fetcher


def get_asset_resolver() -> AssetResolver:
    global _asset_resolver
    if _asset_resolver is None:
        _asset_resolver = AssetResolver(cache=get_image_cache())
    return _asset_resolver


def get_map_preview_service() -> MapPreviewService:
    global _map_preview_service
    if _map_preview_service is None:
        _map_preview_service = MapPreviewService(fetcher=get_asset_fetcher())
    return _map_preview_service


def get_thumbnail_service() -> ThumbnailService:
    global _thumbnail_service
    if _thumbnail_service is None:
        _thumbnail_service = ThumbnailService(fetcher=get_asset_fetcher())
    return _thumbnail_service


__all__ = [
    "AssetFetcher",
    "AssetResolver",
    "ImageCache",
    "MapPreviewService",
    "ThumbnailService",
    "get_asset_fetcher",
    "get_asset_resolver",
    "get_image_cache",
    "get_map_preview_service",
    "get_thumbnail_service",
]
