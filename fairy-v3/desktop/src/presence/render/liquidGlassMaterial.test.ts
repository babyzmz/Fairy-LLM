import { describe, expect, it } from "vitest";

import type { PresenceInteractionSnapshot } from "../domain/interaction";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import {
  LIQUID_GLASS_FRAGMENT_SHADER,
  liquidDirectionForSnapshot,
  liquidShapeTargetForPhase,
} from "./liquidGlassMaterial";

function renderSnapshot(
  expansion_direction: "left" | "right",
  cursor = { x: 0, y: 0 },
): PresenceRenderSnapshot {
  return {
    interaction: {
      schema_version: 1,
      sequence: 1,
      sampled_at_ms: 100,
      phase: "stretching",
      phase_started_at_ms: 100,
      reduced_motion: false,
      cursor: {
        point: { x: 120, y: 130 },
        direction: cursor,
        distance_px: 24,
        speed_px_s: 100,
        dwell_ms: 180,
        band: "active",
      },
      placement: {
        anchor: { x: 96, y: 130 },
        render_frame: { x: 0, y: 0, width: 640, height: 260 },
        input_compact_frame: { x: 268, y: 94, width: 372, height: 72 },
        input_expanded_frame: { x: 220, y: -64, width: 420, height: 360 },
        monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
        scale_factor: 1,
        expansion_direction,
      },
    } satisfies PresenceInteractionSnapshot,
    work_state: "idle",
    speaking: false,
    voice_level: 0,
    sleeping: false,
    reduced_motion: false,
  };
}

describe("Liquid Glass material", () => {
  it("exposes only the geometry required by each interaction phase", () => {
    expect(liquidShapeTargetForPhase("aware")).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("droplet")).toEqual({
      droplet: 1,
      bridge: 0,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("stretching")).toEqual({
      droplet: 1,
      bridge: 1,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("interactive")).toEqual({
      droplet: 1,
      bridge: 1,
      capsule: 1,
    });
  });

  it("keeps the material axis aligned with the selected monitor edge", () => {
    const right = liquidDirectionForSnapshot(renderSnapshot("right", { x: -1, y: 1 }));
    const left = liquidDirectionForSnapshot(renderSnapshot("left", { x: 1, y: -1 }));
    expect(right.x).toBeGreaterThan(0.9);
    expect(left.x).toBeLessThan(-0.9);
    expect(Math.hypot(right.x, right.y)).toBeCloseTo(1);
    expect(Math.hypot(left.x, left.y)).toBeCloseTo(1);
  });

  it("keeps desktop capture out of the shader contract", () => {
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("bezierBridgeDistance");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("smoothMinimum");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toMatch(/sampler2D|texture2D|texture\s*\(/);
  });
});
