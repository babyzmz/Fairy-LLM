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
        input_compact_frame: { x: 24, y: 58, width: 616, height: 144 },
        input_expanded_frame: { x: 24, y: -158, width: 616, height: 360 },
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
    size_scale: 1,
    opacity: 0.92,
    particles_enabled: true,
    idle_for_ms: 0,
    target_frame_rate: 60,
    frame_rate_limit: 60,
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
    expect(liquidShapeTargetForPhase("input_reveal")).toEqual({
      droplet: 1,
      bridge: 1,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("interactive")).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("returning")).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
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

  it("samples the latest desktop texture with boundary-continuous glass optics", () => {
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("bezierBridgeDistance");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("smoothMinimum");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("opticalThickness");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("thickEdgeProfile");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("edgeLensing");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("schlickFresnel");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("screenSpaceEnvironment");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uBackdropTexture");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("boundaryContinuity");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("chromaticDispersion");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("caustic");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("keyHighlight");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("counterHighlight");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("narrowContactShadow");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("capsuleDistance");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("capsuleContentMask");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uLensStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uRimStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uShadowStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uSizeScale");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uOpacity");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("sampler2D uBackdropTexture");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uBackdropSize");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("texture2D(uBackdropTexture");
  });
});
