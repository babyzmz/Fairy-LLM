import { describe, expect, it, vi } from "vitest";

import type { InvokeFunction } from "../core/tauriTransport";
import type { MainViewRequest } from "./mainViewBridge";
import { TauriMainViewHost } from "./mainViewBridge";

describe("TauriMainViewHost", () => {
  it("persists internal navigation with the public request shape", async () => {
    const request: MainViewRequest = {
      schema_version: 1,
      sequence: 4,
      view: "settings",
      settings_category: "models",
    };
    const invoke = vi.fn(async <T>() => request as T) as InvokeFunction;
    const host = new TauriMainViewHost(invoke, async () => () => undefined);

    await expect(host.navigate("settings", "models")).resolves.toEqual(request);
    expect(invoke).toHaveBeenCalledWith("main_view_navigate", {
      input: {
        view: "settings",
        settings_category: "models",
      },
    });
  });

  it("forwards late native requests without changing their sequence", async () => {
    let nativeListener: ((request: MainViewRequest) => void) | undefined;
    const listener = vi.fn();
    const host = new TauriMainViewHost(
      async () => {
        throw new Error("not used");
      },
      async (next) => {
        nativeListener = next;
        return () => undefined;
      },
    );
    await host.subscribe(listener);

    const request: MainViewRequest = {
      schema_version: 1,
      sequence: 9,
      view: "settings",
      settings_category: "pet",
    };
    nativeListener?.(request);

    expect(listener).toHaveBeenCalledWith(request);
  });
});
