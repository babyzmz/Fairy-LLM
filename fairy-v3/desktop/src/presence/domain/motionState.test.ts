import { describe, expect, it } from "vitest";

import type { PresenceInteractionSnapshot } from "./interaction";
import {
  DEFAULT_FAIRY_MOTION_SNAPSHOT,
  advanceFairyMotionSnapshot,
  fairyMotionSnapshotSchema,
  type FairyMotionFacts,
} from "./motionState";

describe("Fairy motion state", () => {
  it("keeps error and approval above voice while preserving one surface", () => {
    const error = advance({ work_state: "error", speaking: true }, 100);
    expect(error.state).toBe("error");
    expect(error.activity).toBe("none");

    const approval = advance({
      work_state: "awaiting_confirmation",
      speaking: true,
      notice_tone: "critical",
    }, 200);
    expect(approval).toEqual(expect.objectContaining({
      state: "awaiting_confirmation",
      surface: "notice",
      activity: "approval",
      capsule_visible: true,
    }));
  });

  it("keeps approval, errors and active work above an open options menu", () => {
    expect(advance({
      menu_open: true,
      notice_tone: "critical",
      work_state: "awaiting_confirmation",
    }, 100)).toEqual(expect.objectContaining({
      state: "awaiting_confirmation",
      surface: "notice",
    }));
    expect(advance({
      menu_open: true,
      submission_phase: "failed",
    }, 100)).toEqual(expect.objectContaining({
      state: "error",
      surface: "submission",
    }));
    expect(advance({
      menu_open: true,
      submission_phase: "accepted",
      work_state: "tool",
    }, 100)).toEqual(expect.objectContaining({
      state: "thinking",
      surface: "submission",
      activity: "tool",
    }));
  });

  it.each([
    ["options", { menu_open: true }],
    ["reply", { reply: { streaming: false } }],
    ["notice", { notice_tone: "info" as const }],
    ["submission", { submission_phase: "sending" as const }],
  ])("keeps one liquid capsule behind the %s surface", (surface, overrides) => {
    expect(advance(overrides, 100)).toEqual(expect.objectContaining({
      surface,
      capsule_visible: true,
    }));
  });

  it("joins model, tool, response and voice into one deterministic chain", () => {
    expect(advance({ work_state: "analyzing" }, 100).state).toBe("thinking");
    expect(advance({ work_state: "tool" }, 100).activity).toBe("tool");
    expect(advance({ work_state: "streaming" }, 100).state).toBe("responding");
    expect(advance({ work_state: "streaming", speaking: true }, 100).state).toBe(
      "speaking",
    );
  });

  it("uses Rust phases as physical facts without exposing their private state", () => {
    const aware = advance({ interaction: interaction("aware") }, 1_000);
    const forming = advanceFairyMotionSnapshot(
      aware,
      facts({ interaction: interaction("droplet"), input_window_visible: true }),
      1_080,
    );
    expect(forming).toEqual(expect.objectContaining({
      state: "forming",
      surface: "input",
      capsule_visible: true,
      content_visible: false,
      surface_interactive: false,
      state_started_at_ms: 1_080,
      state_duration_ms: 420,
      phase_progress: 0,
    }));
    expect(fairyMotionSnapshotSchema.parse(forming)).toEqual(forming);
  });

  it("keeps transition timing stable and only revises semantic changes", () => {
    const started = advance({ submission_phase: "sending" }, 1_000);
    const progressed = advanceFairyMotionSnapshot(
      started,
      facts({ submission_phase: "sending" }),
      1_160,
    );
    expect(progressed.revision).toBe(started.revision);
    expect(progressed.state_started_at_ms).toBe(1_000);
    expect(progressed.phase_progress).toBe(0.5);

    const thinking = advanceFairyMotionSnapshot(
      progressed,
      facts({ submission_phase: "accepted", work_state: "tool" }),
      1_200,
    );
    expect(thinking.revision).toBe(started.revision + 1);
    expect(thinking.state).toBe("thinking");
    expect(thinking.activity).toBe("tool");
  });
});

function advance(
  overrides: Partial<FairyMotionFacts>,
  now: number,
) {
  return advanceFairyMotionSnapshot(
    DEFAULT_FAIRY_MOTION_SNAPSHOT,
    facts(overrides),
    now,
  );
}

function facts(overrides: Partial<FairyMotionFacts> = {}): FairyMotionFacts {
  return {
    interaction: null,
    input_window_visible: false,
    input_content_visible: false,
    input_interactive: false,
    manual_input_open: false,
    menu_open: false,
    reply: null,
    notice_tone: null,
    submission_phase: null,
    work_state: "idle",
    speaking: false,
    moving: false,
    sleeping: false,
    reduced_motion: false,
    do_not_disturb: false,
    ...overrides,
  };
}

function interaction(
  phase: PresenceInteractionSnapshot["phase"],
): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence: 1,
    sampled_at_ms: 0,
    phase,
    phase_started_at_ms: 0,
    reduced_motion: false,
    cursor: {
      point: { x: 0, y: 0 },
      direction: { x: 1, y: 0 },
      distance_px: 20,
      speed_px_s: 0,
      dwell_ms: 250,
      band: "active",
    },
    placement: {
      anchor: { x: 96, y: 88 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: { x: 24, y: 0, width: 280, height: 260 },
      input_expanded_frame: { x: 24, y: 0, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1_920, height: 1_040 },
      scale_factor: 1,
      expansion_direction: "right",
    },
  };
}
