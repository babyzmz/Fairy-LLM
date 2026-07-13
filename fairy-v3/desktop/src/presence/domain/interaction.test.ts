import { describe, expect, it } from "vitest";

import { presenceInteractionSnapshotSchema } from "./interaction";

const snapshot = {
  schema_version: 1,
  sequence: 4,
  sampled_at_ms: 120,
  cursor: {
    point: { x: -240, y: 720 },
    distance_px: 84.5,
    speed_px_s: 340,
    dwell_ms: 48,
    band: "active",
  },
  placement: {
    anchor: { x: -300, y: 700 },
    render_frame: { x: -396, y: 570, width: 640, height: 260 },
    input_compact_frame: { x: -128, y: 664, width: 372, height: 72 },
    input_expanded_frame: { x: -176, y: 376, width: 420, height: 360 },
    monitor_work_area: { x: -1920, y: 0, width: 1920, height: 1040 },
    scale_factor: 1,
    expansion_direction: "right",
  },
} as const;

describe("PresenceInteractionSnapshot", () => {
  it("accepts physical coordinates and the safe coordinator payload", () => {
    expect(presenceInteractionSnapshotSchema.parse(snapshot)).toEqual(snapshot);
  });

  it("rejects unknown fields and invalid scale factors", () => {
    expect(() => presenceInteractionSnapshotSchema.parse({
      ...snapshot,
      placement: { ...snapshot.placement, scale_factor: 0.25 },
    })).toThrow();
    expect(() => presenceInteractionSnapshotSchema.parse({
      ...snapshot,
      project_id: "must-not-cross-the-presence-boundary",
    })).toThrow();
  });
});
