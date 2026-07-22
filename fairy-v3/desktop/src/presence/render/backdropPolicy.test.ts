import { describe, expect, it } from "vitest";

import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import { shouldCaptureBackdrop } from "./backdropPolicy";

function snapshot(optics_mode: "standard" | "enhanced"): PresenceRenderSnapshot {
  return {
    motion: DEFAULT_FAIRY_MOTION_SNAPSHOT,
    interaction: null,
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
    optics_mode,
    idle_for_ms: 0,
    target_frame_rate: 60,
    frame_rate_limit: 60,
  };
}

describe("Presence backdrop policy", () => {
  it("never captures desktop pixels in standard privacy mode", () => {
    expect(shouldCaptureBackdrop(snapshot("standard"))).toBe(false);
  });

  it("allows enhanced capture only while placement is stable", () => {
    const enhanced = snapshot("enhanced");
    expect(shouldCaptureBackdrop(enhanced)).toBe(false);

    enhanced.interaction = {
      schema_version: 1,
      sequence: 1,
      sampled_at_ms: 1,
      phase: "idle",
      phase_started_at_ms: 1,
      reduced_motion: false,
      cursor: {
        point: { x: 0, y: 0 },
        direction: { x: 0, y: 0 },
        distance_px: 300,
        speed_px_s: 0,
        dwell_ms: 0,
        band: "outside",
      },
      placement: {
        anchor: { x: 96, y: 130 },
        render_frame: { x: 0, y: 0, width: 640, height: 260 },
        input_compact_frame: { x: 0, y: 0, width: 616, height: 144 },
        input_expanded_frame: { x: 0, y: 0, width: 616, height: 360 },
        monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
        scale_factor: 1,
        expansion_direction: "right",
      },
    };
    expect(shouldCaptureBackdrop(enhanced)).toBe(true);

    enhanced.interaction.phase = "repositioning";
    expect(shouldCaptureBackdrop(enhanced)).toBe(false);
  });
});
