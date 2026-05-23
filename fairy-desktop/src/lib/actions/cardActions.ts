import type { CardAction } from "../types/api";

function sanitizeUrl(value: string | undefined | null): string {
  const url = String(value || "").trim();
  return /^https?:\/\//i.test(url) ? url : "";
}

export function openExternalUrl(url: string | undefined | null): void {
  const safeUrl = sanitizeUrl(url);
  if (!safeUrl) {
    return;
  }
  window.open(safeUrl, "_blank", "noopener,noreferrer");
}

export function executeCardAction(action: CardAction | null | undefined): void {
  if (!action) {
    return;
  }
  openExternalUrl(action.url);
}

export function firstCardAction(actions: CardAction[] | undefined, preferredTypes?: string[]): CardAction | null {
  if (!Array.isArray(actions) || actions.length === 0) {
    return null;
  }
  if (Array.isArray(preferredTypes) && preferredTypes.length > 0) {
    for (const preferredType of preferredTypes) {
      const match = actions.find((action) => String(action.type || "").trim() === preferredType);
      if (match) {
        return match;
      }
    }
  }
  return actions[0] ?? null;
}
