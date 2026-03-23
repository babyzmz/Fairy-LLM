from __future__ import annotations

from app.services.assets.asset_fetcher import AssetFetcher


class MapPreviewService:
    def __init__(self, *, fetcher: AssetFetcher | None = None) -> None:
        self.fetcher = fetcher or AssetFetcher()

    def build_external_map_url(self, lat: float, lon: float) -> str:
        return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=15/{lat:.6f}/{lon:.6f}"

    def build_remote_preview_url(self, lat: float, lon: float) -> str:
        return (
            "https://static-maps.yandex.ru/1.x/"
            f"?ll={lon:.6f},{lat:.6f}&size=650,360&z=14&l=map"
            f"&pt={lon:.6f},{lat:.6f},pm2rdm"
        )

    def resolve_preview_path(self, source: str = "", *, lat: float | None = None, lon: float | None = None) -> str:
        local_path = self.fetcher.normalize_local_path(source)
        if local_path:
            return local_path
        remote_url = str(source or "").strip()
        cache_key = remote_url
        if not remote_url and lat is not None and lon is not None:
            remote_url = self.build_remote_preview_url(lat, lon)
            cache_key = f"map:{lat:.6f}:{lon:.6f}"
        if not remote_url:
            return ""
        return self.fetcher.fetch_to_local(remote_url, namespace="map", cache_key=cache_key, content_kind="image", suffix_hint="png")
