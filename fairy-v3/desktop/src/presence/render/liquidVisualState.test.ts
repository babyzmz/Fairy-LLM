import { describe, expect, it } from "vitest";

import {
  DEFAULT_FAIRY_MOTION_SNAPSHOT,
  type FairyMotionSnapshot,
} from "../domain/motionState";
import {
  type PresenceRenderSnapshot,
  visualStateForSnapshot,
} from "./presenceRenderer";
import {
  LiquidVisualTransition,
  liquidVisualStyleForSnapshot,
} from "./liquidVisualState";

function snapshot(
  overrides: Partial<PresenceRenderSnapshot> = {},
): PresenceRenderSnapshot {
  return {
    motion: motionForWorkState(overrides.work_state ?? "idle"),
    interaction: null,
    input_capsule_visible: false,
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
    ...overrides,
  };
}

function motionForWorkState(
  workState: PresenceRenderSnapshot["work_state"],
): FairyMotionSnapshot {
  switch (workState) {
    case "analyzing": return {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "thinking",
      activity: "model",
    };
    case "tool": return {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "thinking",
      activity: "tool",
    };
    case "streaming": return {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "responding",
      activity: "response",
    };
    case "awaiting_confirmation": return {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "awaiting_confirmation",
      activity: "approval",
    };
    case "ready": return {
      ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
      state: "notify",
      activity: "notification",
    };
    case "error": return { ...DEFAULT_FAIRY_MOTION_SNAPSHOT, state: "error" };
    case "idle": return DEFAULT_FAIRY_MOTION_SNAPSHOT;
  }
}

describe("Liquid visual states", () => {
  it("keeps idle and active particle counts inside the fixed draw budget", () => {
    expect(liquidVisualStyleForSnapshot(snapshot()).particle_count).toBe(10);
    for (const work_state of [
      "idle",
      "analyzing",
      "tool",
      "streaming",
      "awaiting_confirmation",
      "ready",
      "error",
    ] as const) {
      expect(
        liquidVisualStyleForSnapshot(snapshot({ work_state })).particle_count,
      ).toBeLessThanOrEqual(18);
    }
  });

  it("uses amber, mint, and coral only for meaningful states", () => {
    const tool = liquidVisualStyleForSnapshot(snapshot({ work_state: "tool" }));
    const ready = liquidVisualStyleForSnapshot(snapshot({ work_state: "ready" }));
    const error = liquidVisualStyleForSnapshot(snapshot({ work_state: "error" }));
    expect(tool.accent[0]).toBeGreaterThan(tool.accent[2]);
    expect(ready.accent[1]).toBeGreaterThan(ready.accent[0]);
    expect(error.accent[0]).toBeGreaterThan(error.accent[1] * 2);
  });

  it("continues an interrupted transition from the currently displayed style", () => {
    const idle = snapshot();
    const tool = snapshot({ work_state: "tool" });
    const error = snapshot({ work_state: "error" });
    const transition = new LiquidVisualTransition(
      visualStateForSnapshot(idle),
      liquidVisualStyleForSnapshot(idle),
      0,
    );
    transition.setTarget(
      visualStateForSnapshot(tool),
      liquidVisualStyleForSnapshot(tool),
      0,
      false,
    );
    const interrupted = transition.sample(140);
    transition.setTarget(
      visualStateForSnapshot(error),
      liquidVisualStyleForSnapshot(error),
      140,
      false,
    );
    const resumed = transition.sample(140);

    expect(resumed.accent).toEqual(interrupted.accent);
    expect(resumed.energy).toBe(interrupted.energy);
    expect(resumed.progress).toBe(0);
  });

  it("uses a short material dissolve for reduced motion", () => {
    const idle = snapshot();
    const speaking = snapshot({
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "speaking",
        activity: "voice",
      },
      speaking: true,
    });
    const transition = new LiquidVisualTransition(
      visualStateForSnapshot(idle),
      liquidVisualStyleForSnapshot(idle),
      0,
    );
    transition.setTarget(
      visualStateForSnapshot(speaking),
      liquidVisualStyleForSnapshot(speaking),
      0,
      true,
    );

    expect(transition.sample(79).progress).toBeLessThan(1);
    expect(transition.sample(80).progress).toBe(1);
  });

  it("stays finite through rapid interruptible state changes", () => {
    const states = [
      snapshot(),
      snapshot({ work_state: "analyzing" }),
      snapshot({ work_state: "tool" }),
      snapshot({ work_state: "streaming" }),
      snapshot({ work_state: "awaiting_confirmation" }),
      snapshot({ work_state: "error" }),
      snapshot({ work_state: "ready" }),
    ];
    const initial = states[0];
    const transition = new LiquidVisualTransition(
      visualStateForSnapshot(initial),
      liquidVisualStyleForSnapshot(initial),
      0,
    );
    let now = 0;
    for (let index = 0; index < 1_000; index += 1) {
      const next = states[index % states.length];
      transition.setTarget(
        visualStateForSnapshot(next),
        liquidVisualStyleForSnapshot(next),
        now,
        false,
      );
      const sample = transition.sample(now + 5);
      expect([
        ...sample.accent,
        sample.energy,
        sample.particle_count,
        sample.pulse_speed,
        sample.progress,
      ].every(Number.isFinite)).toBe(true);
      expect(sample.progress).toBeGreaterThanOrEqual(0);
      expect(sample.progress).toBeLessThanOrEqual(1);
      now += 7;
    }
  });
});
