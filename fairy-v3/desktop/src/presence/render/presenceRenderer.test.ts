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
    sleeping: false,
    reduced_motion: false,
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

  it("uses 60 FPS for active work, 30 FPS when idle, and one frame for reduced motion", () => {
    expect(rendererFrameInterval(snapshot())).toBeCloseTo(1_000 / 30);
    expect(rendererFrameInterval(snapshot({ work_state: "streaming" }))).toBeCloseTo(
      1_000 / 60,
    );
    expect(rendererFrameInterval(snapshot({ reduced_motion: true }))).toBe(
      Number.POSITIVE_INFINITY,
    );
  });
});
