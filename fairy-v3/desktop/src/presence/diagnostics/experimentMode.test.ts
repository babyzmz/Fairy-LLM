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
    expect(resolvePresenceExperimentMode(storage, true, true)).toBe("single-renderer");
    expect(resolvePresenceExperimentMode(storage, true, false)).toBe("normal");
    expect(resolvePresenceExperimentMode(storage, false, true)).toBe("normal");
    expect(resolvePresenceExperimentMode({ getItem: () => "arbitrary" }, true, true)).toBe(
      "normal",
    );
    expect(PRESENCE_EXPERIMENT_STORAGE_KEY).toBe("fairy.presence.experiment");
  });

  it("allows only 60, 144, and 300 as development frame-rate overrides", () => {
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "60" }, true, true)).toBe(60);
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "144" }, true, true)).toBe(144);
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "300" }, true, true)).toBe(300);
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "144" }, true, false)).toBeNull();
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "240" }, true, true)).toBeNull();
    expect(resolvePresenceTargetFpsOverride({ getItem: () => "144" }, false, true)).toBeNull();
  });

  it("exposes only capture experiments to the native command", () => {
    expect(backdropCommandMode("capture-only")).toBe("capture-only");
    expect(backdropCommandMode("ipc-upload-only")).toBe("ipc-upload-only");
    expect(backdropCommandMode("no-refraction")).toBe("normal");
  });
});
