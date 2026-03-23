import { API_BASE_URL, ASSET_BASE_URL } from "../config/env";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, status = 500, details: unknown = null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.details = details;
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    const message =
      typeof payload === "object" && payload !== null && "errors" in payload
        ? String(((payload as { errors?: Array<{ message?: string }> }).errors?.[0]?.message) || "Request failed.")
        : `Request failed with status ${response.status}.`;
    throw new ApiRequestError(message, response.status, payload);
  }
  return payload as T;
}

export function resolveAssetSrc(pathValue?: string | null): string | undefined {
  const value = String(pathValue || "").trim();
  if (!value) {
    return undefined;
  }
  if (/^https?:\/\//i.test(value)) {
    return undefined;
  }
  // Dev and packaged builds both load image assets through the local backend.
  // Business remote URLs are rejected here on purpose.
  return `${ASSET_BASE_URL}?path=${encodeURIComponent(value)}`;
}

export const toAssetUrl = resolveAssetSrc;
