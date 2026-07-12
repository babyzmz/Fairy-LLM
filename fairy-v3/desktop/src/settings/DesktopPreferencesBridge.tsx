import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect } from "react";

import { applyDesktopPreferences, type DesktopPreferences } from "./client";

export function DesktopPreferencesBridge() {
  useEffect(() => {
    let disposed = false;
    let removeListener: (() => void) | undefined;

    void invoke<DesktopPreferences>("desktop_preferences_get")
      .then((preferences) => {
        if (!disposed) applyDesktopPreferences(preferences);
      })
      .catch(() => undefined);
    void listen<DesktopPreferences>("desktop-preferences-changed", (event) => {
      if (!disposed) applyDesktopPreferences(event.payload);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else removeListener = unlisten;
    }).catch(() => undefined);

    return () => {
      disposed = true;
      removeListener?.();
    };
  }, []);

  return null;
}
