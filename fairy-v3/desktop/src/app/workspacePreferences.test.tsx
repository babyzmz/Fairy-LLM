import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DESKTOP_PREFERENCES_EVENT } from "../settings/client";
import { usePersistedBoolean } from "./workspacePreferences";

const key = "fairy.workspace.developer";

describe("workspace preferences", () => {
  afterEach(() => {
    localStorage.removeItem(key);
  });

  it("synchronizes preferences changed inside the same WebView", () => {
    const { result } = renderHook(() => usePersistedBoolean(key, false));

    act(() => {
      localStorage.setItem(key, "true");
      window.dispatchEvent(new CustomEvent(DESKTOP_PREFERENCES_EVENT));
    });

    expect(result.current[0]).toBe(true);
  });
});
