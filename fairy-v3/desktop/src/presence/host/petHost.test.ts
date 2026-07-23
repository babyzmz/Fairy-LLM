import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invoke(...args),
  isTauri: () => true,
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => undefined),
}));

import { createDefaultPetHost } from "./petHost";

describe("PetHost", () => {
  beforeEach(() => invoke.mockReset());

  it("opens the restricted Companion window without routing through the workspace", async () => {
    invoke.mockResolvedValue(undefined);
    const host = createDefaultPetHost();

    await host.openCompanion();

    expect(invoke).toHaveBeenCalledWith("open_companion_window");
    expect(invoke).not.toHaveBeenCalledWith("open_main_window");
  });
});
