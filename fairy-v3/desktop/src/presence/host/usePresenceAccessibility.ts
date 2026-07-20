import { useEffect, useState } from "react";

export interface PresenceAccessibilityPreferences {
  reduced_motion: boolean;
  reduced_transparency: boolean;
  increased_contrast: boolean;
}

const QUERIES = Object.freeze({
  reduced_motion: "(prefers-reduced-motion: reduce)",
  reduced_transparency: "(prefers-reduced-transparency: reduce)",
  increased_contrast: "(prefers-contrast: more)",
  forced_colors: "(forced-colors: active)",
});

export function resolvePresenceAccessibilityPreferences(
  matches: Readonly<Record<keyof typeof QUERIES, boolean>>,
): PresenceAccessibilityPreferences {
  return Object.freeze({
    reduced_motion: matches.reduced_motion,
    reduced_transparency: matches.reduced_transparency,
    increased_contrast: matches.increased_contrast || matches.forced_colors,
  });
}

export function usePresenceAccessibilityPreferences(): PresenceAccessibilityPreferences {
  const [preferences, setPreferences] = useState(readPreferences);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const entries = Object.entries(QUERIES).map(([name, query]) => [
      name as keyof typeof QUERIES,
      window.matchMedia(query),
    ] as const);
    const update = () => {
      setPreferences(resolvePresenceAccessibilityPreferences({
        reduced_motion: entries[0][1].matches,
        reduced_transparency: entries[1][1].matches,
        increased_contrast: entries[2][1].matches,
        forced_colors: entries[3][1].matches,
      }));
    };
    for (const [, media] of entries) addMediaListener(media, update);
    update();
    return () => {
      for (const [, media] of entries) removeMediaListener(media, update);
    };
  }, []);

  return preferences;
}

function addMediaListener(media: MediaQueryList, listener: () => void) {
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", listener);
  } else if (typeof media.addListener === "function") {
    media.addListener(listener);
  }
}

function removeMediaListener(media: MediaQueryList, listener: () => void) {
  if (typeof media.removeEventListener === "function") {
    media.removeEventListener("change", listener);
  } else if (typeof media.removeListener === "function") {
    media.removeListener(listener);
  }
}

function readPreferences(): PresenceAccessibilityPreferences {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return resolvePresenceAccessibilityPreferences({
      reduced_motion: false,
      reduced_transparency: false,
      increased_contrast: false,
      forced_colors: false,
    });
  }
  return resolvePresenceAccessibilityPreferences({
    reduced_motion: window.matchMedia(QUERIES.reduced_motion).matches,
    reduced_transparency: window.matchMedia(QUERIES.reduced_transparency).matches,
    increased_contrast: window.matchMedia(QUERIES.increased_contrast).matches,
    forced_colors: window.matchMedia(QUERIES.forced_colors).matches,
  });
}
