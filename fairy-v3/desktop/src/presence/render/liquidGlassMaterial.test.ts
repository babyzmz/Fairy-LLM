import { describe, expect, it } from "vitest";

import type { PresenceInteractionSnapshot } from "../domain/interaction";
import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import {
  LIQUID_GLASS_FRAGMENT_SHADER,
  liquidDirectionForSnapshot,
  liquidShapeTargetForPhase,
  liquidShapeTargetForSnapshot,
} from "./liquidGlassMaterial";

function renderSnapshot(
  expansion_direction: "left" | "right",
  cursor = { x: 0, y: 0 },
): PresenceRenderSnapshot {
  return {
    motion: {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "forming",
      surface: "input",
      capsule_visible: true,
    },
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
    input_capsule_visible: true,
    input_capsule_width: 280,
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
      droplet: 0,
      bridge: 1,
      capsule: 1,
    });
    expect(liquidShapeTargetForPhase("interactive")).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
    });
    expect(liquidShapeTargetForPhase("returning")).toEqual({
      droplet: 0,
      bridge: 1,
      capsule: 0,
    });
  });

  it("keeps the material axis aligned with the selected monitor edge", () => {
    const right = liquidDirectionForSnapshot(renderSnapshot("right", { x: -1, y: 1 }));
    const left = liquidDirectionForSnapshot(renderSnapshot("left", { x: 1, y: -1 }));
    expect(right.x).toBeGreaterThan(0.4);
    expect(right.y).toBeGreaterThan(0.8);
    expect(left.x).toBeLessThan(-0.4);
    expect(left.y).toBeGreaterThan(0.8);
    expect(Math.hypot(right.x, right.y)).toBeCloseTo(1);
    expect(Math.hypot(left.x, left.y)).toBeCloseTo(1);
  });

  it("hides the stable capsule when the input DOM is not presented", () => {
    const snapshot = renderSnapshot("right");
    snapshot.interaction!.phase = "interactive";
    snapshot.input_capsule_visible = false;
    expect(liquidShapeTargetForSnapshot(snapshot)).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
    });
  });

  it("keeps manually opened input outside the render shape", () => {
    const snapshot = renderSnapshot("right");
    snapshot.interaction!.phase = "idle";
    snapshot.input_capsule_visible = true;
    expect(liquidShapeTargetForSnapshot(snapshot)).toEqual({
      droplet: 0,
      bridge: 0,
      capsule: 0,
    });
  });

  it("keeps the bridge when input reveal has no render capsule", () => {
    const snapshot = renderSnapshot("right");
    snapshot.interaction!.phase = "input_reveal";
    snapshot.input_capsule_visible = false;
    expect(liquidShapeTargetForSnapshot(snapshot)).toEqual({
      droplet: 0,
      bridge: 1,
      capsule: 0,
    });
  });

  it("samples the latest desktop texture with boundary-continuous glass optics", () => {
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("bezierBridgeDistance");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("smoothMinimum");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("if (dropletMorph >= 0.001)");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("if (bridgeMorph >= 0.001)");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("sceneSdf");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("thicknessField");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("float clearInterior");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("edgeProfile *= clearInterior");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("surfaceNormal");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("curvatureApprox");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("bottomLip");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("edgeLensResponse");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("pow(edgeProfile, 1.55)");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("schlickFresnel");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("screenSpaceEnvironment");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uBackdropTexture");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("interiorContinuity");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("chromaticDispersion");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("chromaMask");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("primaryTransmission");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("replacementMaterial");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("maximumGlassAlpha = 0.50");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("BackdropAdaptation");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("caustic");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("keyHighlight");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("counterHighlight");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("narrowContactShadow");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("identityPoint");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("identityBreath");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("identityAtmosphereOuter");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("identityAtmosphereInner");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("coreRing");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("outerCoreRing");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("capsuleDistance");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("capsuleContentMask");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uLensStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uRimStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uShadowStrength");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uReducedTransparency");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uIncreasedContrast");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uSizeScale");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uOpacity");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("sampler2D uBackdropTexture");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("uBackdropSize");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("texture2D(uBackdropTexture");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain(
      "clamp(glassColor, 0.0, 1.0) * materialAlpha",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain(
      "vec2 identityOffset = point - uGaze * 3.5 * uDpr",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain(
      "vec3 foregroundPremultiplied = identityPremultiplied",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("float atmosphereLayerAlpha");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain("float pointLayerAlpha");
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain(
      "identityColor * identityAlpha",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).toContain(
      "materialPremultiplied * (1.0 - identityAlpha)",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain(
      "coreOffset = point + internalRefraction",
    );
    expect(LIQUID_GLASS_FRAGMENT_SHADER).not.toContain("sampledGlassAlpha");
  });
});
