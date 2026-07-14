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
    expect(visualStateForSnapshot(snapshot({ interaction: interactiveSnapshot() }))).toBe(
      "hover",
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

function interactiveSnapshot(): NonNullable<PresenceRenderSnapshot["interaction"]> {
  return {
    schema_version: 1,
    sequence: 1,
    sampled_at_ms: 520,
    phase: "interactive",
    phase_started_at_ms: 520,
    reduced_motion: false,
    cursor: {
      point: { x: 136, y: 130 },
      direction: { x: 1, y: 0 },
      distance_px: 40,
      speed_px_s: 0,
      dwell_ms: 520,
      band: "active",
    },
    placement: {
      anchor: { x: 96, y: 130 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: { x: 24, y: 58, width: 616, height: 144 },
      input_expanded_frame: { x: 24, y: -158, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1_920, height: 1_040 },
      scale_factor: 1,
      expansion_direction: "right",
    },
  };
}
