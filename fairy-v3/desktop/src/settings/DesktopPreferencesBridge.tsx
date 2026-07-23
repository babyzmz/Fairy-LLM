import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect } from "react";

import type { CoreClient } from "../core/client";
import {
  applyDesktopPreferences,
  DESKTOP_PREFERENCES_EVENT,
  type DesktopPreferences,
} from "./client";
import { runAutomaticTrashMaintenance } from "./trashMaintenance";

interface DesktopPreferencesBridgeProps {
  trash?: Pick<CoreClient["trash"], "purgeAll">;
  onChange?(preferences: DesktopPreferences): void;
}

export function DesktopPreferencesBridge({ trash, onChange }: DesktopPreferencesBridgeProps) {
  useEffect(() => {
    let disposed = false;
    let removeListener: (() => void) | undefined;
    const handleLocalChange = (event: Event) => {
      if (disposed) return;
      const preferences = (event as CustomEvent<DesktopPreferences>).detail;
      if (preferences !== undefined) onChange?.(preferences);
    };
    window.addEventListener(DESKTOP_PREFERENCES_EVENT, handleLocalChange);

    void invoke<DesktopPreferences>("desktop_preferences_get")
      .then((preferences) => {
        if (!disposed) {
          applyDesktopPreferences(preferences);
          onChange?.(preferences);
          void runAutomaticTrashMaintenance(preferences, trash);
        }
      })
      .catch(() => undefined);
    void listen<DesktopPreferences>("desktop-preferences-changed", (event) => {
      if (!disposed) {
        applyDesktopPreferences(event.payload);
        onChange?.(event.payload);
      }
    }).then((unlisten) => {
      if (disposed) unlisten();
      else removeListener = unlisten;
    }).catch(() => undefined);

    return () => {
      disposed = true;
      removeListener?.();
      window.removeEventListener(DESKTOP_PREFERENCES_EVENT, handleLocalChange);
    };
  }, [onChange, trash]);

  return null;
}
