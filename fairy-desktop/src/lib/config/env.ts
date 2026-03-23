export const API_BASE_URL =
  (import.meta.env.VITE_FAIRY_API_BASE_URL as string | undefined)?.trim() ||
  "http://127.0.0.1:8000";

export const ASSET_BASE_URL = `${API_BASE_URL}/assets/local`;
