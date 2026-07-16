import { describe, expect, it } from "vitest";

import {
  backdropCommandMode,
  PRESENCE_EXPERIMENT_STORAGE_KEY,
  resolvePresenceTargetFpsOverride,
  resolvePresenceExperimentMode,
} from "./experimentMode";

describe("Presence experiment mode", () => {
  it("accepts the fixed development allowlist and rejects production overrides", () => {
    const storage = { getItem: () => "single-renderer" };
    expect(resolvePresenceExperimentMode(storage, true)).toBe("single-renderer");
    expect(resolvePresenceExperimentMode(storage, false)).toBe("normal");
    expect(resolvePresenceExperimentMode({ getItem: () => "arbitrary" }, true)).toBe(
      "normal",
    );
    expect(PRESENCE_EXPERIMENT_STORAGE_KEY).toBe("fairy.presence.experiment");
  });

  it("allows only 60 and 144 as development frame-rate overrides", () => {
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "60" }, true)).toBe(60);
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "144" }, true)).toBe(144);
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "240" }, true)).toBeNull();
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "144" }, false)).toBeNull();
  });

  it("exposes only capture experiments to the native command", () => {
    expect(backdropCommandMode("capture-only")).toBe("capture-only");
    expect(backdropCommandMode("ipc-upload-only")).toBe("ipc-upload-only");
    expect(backdropCommandMode("no-refraction")).toBe("normal");
  });
});
