import { describe, expect, it } from "vitest";

import type { PresenceRenderSnapshot } from "./presenceRenderer";
import { liquidVisualStyleForSnapshot } from "./liquidVisualState";

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
    ...overrides,
  };
}

describe("Liquid visual states", () => {
  it("keeps idle and active particle counts inside the fixed draw budget", () => {
    expect(liquidVisualStyleForSnapshot(snapshot()).particle_count).toBe(10);
    for (const work_state of [
      "idle",
      "analyzing",
      "tool",
      "streaming",
      "awaiting_confirmation",
      "ready",
      "error",
    ] as const) {
      expect(
        liquidVisualStyleForSnapshot(snapshot({ work_state })).particle_count,
      ).toBeLessThanOrEqual(18);
    }
  });

  it("uses amber, mint, and coral only for meaningful states", () => {
    const tool = liquidVisualStyleForSnapshot(snapshot({ work_state: "tool" }));
    const ready = liquidVisualStyleForSnapshot(snapshot({ work_state: "ready" }));
    const error = liquidVisualStyleForSnapshot(snapshot({ work_state: "error" }));
    expect(tool.accent[0]).toBeGreaterThan(tool.accent[2]);
    expect(ready.accent[1]).toBeGreaterThan(ready.accent[0]);
    expect(error.accent[0]).toBeGreaterThan(error.accent[1] * 2);
  });
});
