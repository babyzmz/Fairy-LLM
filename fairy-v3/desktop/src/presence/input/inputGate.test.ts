import { describe, expect, it } from "vitest";

import type { PresenceInteractionSnapshot } from "../domain/interaction";
import { presenceInputGate } from "./inputGate";

function snapshot(
  phase: PresenceInteractionSnapshot["phase"],
  elapsed: number,
  reduced_motion = false,
): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence: 1,
    sampled_at_ms: 300 + elapsed,
    phase,
    phase_started_at_ms: 300,
    reduced_motion,
    cursor: {
      point: { x: 180, y: 130 },
      direction: { x: 1, y: 0 },
      distance_px: 84,
      speed_px_s: 80,
      dwell_ms: 300 + elapsed,
      band: "active",
    },
    placement: {
      anchor: { x: 96, y: 130 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: { x: 24, y: 58, width: 616, height: 144 },
      input_expanded_frame: { x: 24, y: -158, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction: "right",
    },
  };
}

describe("presence input gate", () => {
  it("shows a passive transparent window at 300ms and content at 430ms", () => {
    expect(presenceInputGate(snapshot("input_reveal", 0))).toEqual({
      window_visible: true,
      content_visible: false,
      interactive: false,
    });
    expect(presenceInputGate(snapshot("input_reveal", 129)).content_visible).toBe(false);
    expect(presenceInputGate(snapshot("input_reveal", 130))).toEqual({
      window_visible: true,
      content_visible: true,
      interactive: false,
    });
  });

  it("enables clicks only in the interactive phase and releases them on return", () => {
    expect(presenceInputGate(snapshot("interactive", 0)).interactive).toBe(true);
    expect(presenceInputGate(snapshot("returning", 0))).toEqual({
      window_visible: true,
      content_visible: true,
      interactive: false,
    });
    expect(presenceInputGate(snapshot("returning", 150)).content_visible).toBe(false);
  });

  it("snaps visual content but not focus for reduced motion", () => {
    expect(presenceInputGate(snapshot("input_reveal", 0, true))).toEqual({
      window_visible: true,
      content_visible: true,
      interactive: false,
    });
  });

  it("stays closed after an explicit dismissal until the phase resets", () => {
    expect(presenceInputGate(snapshot("interactive", 0), true)).toEqual({
      window_visible: false,
      content_visible: false,
      interactive: false,
    });
  });
});
