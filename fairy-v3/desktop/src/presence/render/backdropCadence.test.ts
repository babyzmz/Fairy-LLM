import { describe, expect, it } from "vitest";

import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import { BACKDROP_CAPTURE_RATES, backdropFrameRate } from "./backdropCadence";

function snapshot(
  phase: NonNullable<PresenceRenderSnapshot["interaction"]>["phase"],
  band: NonNullable<PresenceRenderSnapshot["interaction"]>["cursor"]["band"],
  overrides: Partial<PresenceRenderSnapshot> = {},
): PresenceRenderSnapshot {
  return {
    motion: DEFAULT_FAIRY_MOTION_SNAPSHOT,
    interaction: {
      schema_version: 1,
      sequence: 1,
      sampled_at_ms: 1,
      phase,
      phase_started_at_ms: 1,
      reduced_motion: false,
      cursor: {
        point: { x: 0, y: 0 },
        direction: { x: 0, y: 0 },
        distance_px: 0,
        speed_px_s: 0,
        dwell_ms: 0,
        band,
      },
      placement: {
        anchor: { x: 0, y: 0 },
        render_frame: { x: 0, y: 0, width: 640, height: 260 },
        input_compact_frame: { x: 0, y: 0, width: 372, height: 72 },
        input_expanded_frame: { x: 0, y: 0, width: 372, height: 220 },
        monitor_work_area: { x: 0, y: 0, width: 1920, height: 1080 },
        scale_factor: 1,
        expansion_direction: "right",
      },
    },
    input_capsule_visible: false,
    input_capsule_width: 280,
    input_capsule_height: 64,
    input_surface_visible: false,
    work_state: "idle",
    speaking: false,
    voice_level: 0,
    sleeping: false,
    reduced_motion: false,
    size_scale: 1,
    opacity: 0.92,
    particles_enabled: true,
    optics_mode: "enhanced",
    idle_for_ms: 0,
    target_frame_rate: 144,
    frame_rate_limit: 144,
    ...overrides,
  };
}

describe("Presence backdrop cadence", () => {
  it("decouples capture cadence from 60 and 144 fps animation", () => {
    expect(backdropFrameRate(snapshot("idle", "outside"))).toBe(
      BACKDROP_CAPTURE_RATES.idle,
    );
    expect(backdropFrameRate(snapshot("aware", "aware"))).toBe(
      BACKDROP_CAPTURE_RATES.aware,
    );
    expect(backdropFrameRate(snapshot("interactive", "active"))).toBe(
      BACKDROP_CAPTURE_RATES.active,
    );
  });

  it("uses active capture for work and voice without exceeding the runtime cap", () => {
    expect(
      backdropFrameRate(snapshot("idle", "outside", { speaking: true })),
    ).toBe(BACKDROP_CAPTURE_RATES.active);
    expect(
      backdropFrameRate(snapshot("idle", "outside", { work_state: "tool" })),
    ).toBe(BACKDROP_CAPTURE_RATES.active);
    expect(
      backdropFrameRate(snapshot("interactive", "active", { frame_rate_limit: 15 })),
    ).toBe(15);
  });
});
