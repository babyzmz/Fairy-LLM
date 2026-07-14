import { describe, expect, it } from "vitest";

import type { PresenceRenderSnapshot } from "./presenceRenderer";
import {
  LIQUID_OPTICS_LIMITS,
  liquidOpticsForSnapshot,
} from "./liquidOptics";

function snapshot(
  interaction: PresenceRenderSnapshot["interaction"],
): PresenceRenderSnapshot {
  return {
    interaction,
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
  };
}

function interaction(
  band: "outside" | "aware" | "active" = "outside",
): NonNullable<PresenceRenderSnapshot["interaction"]> {
  return {
    schema_version: 1,
    sequence: 1,
    sampled_at_ms: 100,
    phase: band === "active" ? "interactive" : "idle",
    phase_started_at_ms: 100,
    reduced_motion: false,
    cursor: {
      point: { x: -1_540, y: 710 },
      direction: { x: 0.8, y: -0.2 },
      distance_px: band === "active" ? 40 : 400,
      speed_px_s: 0,
      dwell_ms: band === "active" ? 520 : 0,
      band,
    },
    placement: {
      anchor: { x: -1_520, y: 700 },
      render_frame: { x: -1_880, y: 570, width: 960, height: 390 },
      input_compact_frame: { x: -1_508, y: 664, width: 558, height: 108 },
      input_expanded_frame: { x: -1_550, y: 460, width: 630, height: 540 },
      monitor_work_area: { x: -2_560, y: 0, width: 2_560, height: 1_440 },
      scale_factor: 1.5,
      expansion_direction: "left",
    },
  };
}

describe("liquid optics uniforms", () => {
  it("preserves physical screen placement including negative monitor origins", () => {
    const optics = liquidOpticsForSnapshot(snapshot(interaction()), 640, 260, 1.5);

    expect(optics.render_origin).toEqual([-1_880, 570]);
    expect(optics.monitor_origin).toEqual([-2_560, 0]);
    expect(optics.monitor_size).toEqual([2_560, 1_440]);
    expect(optics.device_scale).toBe(1.5);
  });

  it("uses deterministic canvas bounds before the native placement arrives", () => {
    const optics = liquidOpticsForSnapshot(snapshot(null), 640, 260, 1.25);

    expect(optics.render_origin).toEqual([0, 0]);
    expect(optics.monitor_origin).toEqual([0, 0]);
    expect(optics.monitor_size).toEqual([800, 325]);
  });

  it("keeps dispersion bounded in logical pixels and strengthens active lensing", () => {
    const idle = liquidOpticsForSnapshot(snapshot(interaction()), 640, 260, 2);
    const active = liquidOpticsForSnapshot(snapshot(interaction("active")), 640, 260, 2);

    expect(idle.dispersion_px / idle.device_scale).toBeGreaterThanOrEqual(
      LIQUID_OPTICS_LIMITS.minimum_dispersion_logical_px,
    );
    expect(active.dispersion_px).toBeGreaterThan(idle.dispersion_px);
    expect(active.dispersion_px / active.device_scale).toBeLessThanOrEqual(
      LIQUID_OPTICS_LIMITS.maximum_dispersion_logical_px,
    );
    expect(active.refraction_px).toBeGreaterThan(idle.refraction_px);
    expect(active.caustic_strength).toBeGreaterThan(idle.caustic_strength);
  });

  it("clamps invalid runtime dimensions and DPR to finite values", () => {
    const optics = liquidOpticsForSnapshot(snapshot(null), 0, Number.NaN, 20);

    expect(optics.monitor_size).toEqual([4, 4]);
    expect(optics.device_scale).toBe(4);
    expect(Object.values(optics).flat().every(Number.isFinite)).toBe(true);
  });
});
