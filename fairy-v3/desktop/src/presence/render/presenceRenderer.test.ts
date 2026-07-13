import { describe, expect, it } from "vitest";

import type { PresenceRenderSnapshot } from "./presenceRenderer";
import {
  rendererFrameInterval,
  visualStateForSnapshot,
} from "./presenceRenderer";

function snapshot(
  overrides: Partial<PresenceRenderSnapshot> = {},
): PresenceRenderSnapshot {
  return {
    interaction: null,
    work_state: "idle",
    speaking: false,
    voice_level: 0,
    sleeping: false,
    reduced_motion: false,
    size_scale: 1,
    opacity: 0.92,
    particles_enabled: true,
    idle_for_ms: 0,
    frame_rate_limit: 60,
    ...overrides,
  };
}

describe("presence renderer scheduling", () => {
  it("maps public presence state without importing project state", () => {
    expect(visualStateForSnapshot(snapshot())).toBe("idle");
    expect(visualStateForSnapshot(snapshot({ work_state: "tool" }))).toBe("tool");
    expect(visualStateForSnapshot(snapshot({ speaking: true, work_state: "error" }))).toBe(
      "speaking",
    );
  });

  it("uses 60 FPS while fresh, 30 FPS after idle, and 15 FPS when constrained", () => {
    expect(rendererFrameInterval(snapshot())).toBeCloseTo(1_000 / 60);
    expect(rendererFrameInterval(snapshot({ idle_for_ms: 15_000 }))).toBeCloseTo(
      1_000 / 30,
    );
    expect(rendererFrameInterval(snapshot({ frame_rate_limit: 15 }))).toBeCloseTo(
      1_000 / 15,
    );
    expect(rendererFrameInterval(snapshot({ reduced_motion: true }))).toBe(
      Number.POSITIVE_INFINITY,
    );
  });
});
