import { describe, expect, it } from "vitest";

import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import {
  liquidAnchorForSnapshot,
  liquidCapsuleGeometryForSnapshot,
} from "./liquidGeometry";

describe("Liquid renderer coordinate spaces", () => {
  it("does not multiply physical placement coordinates by DPR twice", () => {
    expect(
      liquidAnchorForSnapshot(snapshot(1.25, 120, 163), 640, 260, 1.25),
    ).toEqual({ x: 120, y: 162 });
    expect(
      liquidAnchorForSnapshot(snapshot(2, 192, 260), 640, 260, 2),
    ).toEqual({ x: 192, y: 260 });
  });

  it("places a bounded dynamic capsule below the core on either edge", () => {
    const right = snapshot(1, 96, 88);
    right.input_capsule_width = 280;
    expect(liquidCapsuleGeometryForSnapshot(right, 640, 260, 1)).toEqual({
      center_x: 164,
      center_y: 40,
      half_width: 132,
    });
    right.interaction!.placement.expansion_direction = "left";
    right.input_capsule_width = 360;
    expect(liquidCapsuleGeometryForSnapshot(right, 640, 260, 1)).toEqual({
      center_x: 436,
      center_y: 40,
      half_width: 172,
    });
  });
});

function snapshot(
  scale: number,
  localAnchorX: number,
  localAnchorY: number,
): PresenceRenderSnapshot {
  return {
    motion: {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "input",
      surface: "input",
      capsule_visible: true,
    },
    interaction: {
      schema_version: 1,
      sequence: 1,
      sampled_at_ms: 0,
      phase: "interactive",
      phase_started_at_ms: 0,
      reduced_motion: false,
      cursor: {
        point: { x: 0, y: 0 },
        direction: { x: 0, y: 0 },
        distance_px: 0,
        speed_px_s: 0,
        dwell_ms: 0,
        band: "active",
      },
      placement: {
        anchor: { x: 1_000 + localAnchorX, y: 500 + localAnchorY },
        render_frame: {
          x: 1_000,
          y: 500,
          width: Math.round(640 * scale),
          height: Math.round(260 * scale),
        },
        input_compact_frame: { x: 0, y: 0, width: 1, height: 1 },
        input_expanded_frame: { x: 0, y: 0, width: 1, height: 1 },
        monitor_work_area: { x: 0, y: 0, width: 3_840, height: 2_160 },
        scale_factor: scale,
        expansion_direction: "right",
      },
    },
    input_capsule_visible: true,
    input_capsule_width: 280,
    input_capsule_height: 64,
    input_surface_visible: true,
    work_state: "idle",
    speaking: false,
    voice_level: 0,
    sleeping: false,
    reduced_motion: false,
    size_scale: 1,
    opacity: 0.92,
    particles_enabled: true,
    optics_mode: "standard",
    idle_for_ms: 0,
    target_frame_rate: 60,
    frame_rate_limit: 60,
  };
}
