import { ASSET_BASE_URL } from "../config/env";

export class AssetResolver {
  static resolveLocalAsset(pathValue?: string | null): string | undefined {
    const value = String(pathValue || "").trim();
    if (!value || /^https?:\/\//i.test(value)) {
      return undefined;
    }
    return `${ASSET_BASE_URL}?path=${encodeURIComponent(value)}`;
  }

  static resolveWeatherIcon(iconKey?: string | null, pathValue?: string | null): string | undefined {
    const key = String(iconKey || "").trim();
    if (key) {
      return this.resolveLocalAsset(`weather/${key}.png`);
    }
    return this.resolveLocalAsset(pathValue);
  }

  static resolveNewsThumbnail(pathValue?: string | null): string | undefined {
    return this.resolveLocalAsset(pathValue || "news/generic-news.png");
  }

  static resolveMapPreview(pathValue?: string | null): string | undefined {
    return this.resolveLocalAsset(pathValue);
  }
}
