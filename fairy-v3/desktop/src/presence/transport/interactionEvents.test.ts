import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PresenceInteractionSnapshot } from "../domain/interaction";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: mocks.invoke,
  isTauri: () => true,
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: mocks.listen,
}));

import { createPresenceInteractionSource } from "./interactionEvents";

function snapshot(sequence: number): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence,
    sampled_at_ms: sequence * 16,
    phase: "interactive",
    phase_started_at_ms: sequence * 16,
    reduced_motion: false,
    cursor: {
      point: { x: 100, y: 100 },
      direction: { x: 0, y: 0 },
      distance_px: 0,
      speed_px_s: 0,
      dwell_ms: 520,
      band: "active",
    },
    placement: {
      anchor: { x: 100, y: 100 },
      render_frame: { x: 4, y: 12, width: 640, height: 260 },
      input_compact_frame: { x: 4, y: 12, width: 616, height: 260 },
      input_expanded_frame: { x: 4, y: 12, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction: "right",
    },
  };
}

describe("presence interaction events", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.listen.mockReset();
  });

  it("replays the latest native snapshot after subscribing", async () => {
    const unlisten = vi.fn();
    mocks.listen.mockResolvedValue(unlisten);
    mocks.invoke.mockResolvedValue(snapshot(8));
    const listener = vi.fn();

    const stop = await createPresenceInteractionSource().subscribe(listener);

    expect(mocks.listen).toHaveBeenCalledWith(
      "presence-interaction-snapshot",
      expect.any(Function),
    );
    expect(mocks.invoke).toHaveBeenCalledWith("pet_interaction_snapshot_get");
    expect(listener).toHaveBeenCalledWith(snapshot(8));
    stop();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("deduplicates a replay that arrives after a newer live event", async () => {
    let liveListener: ((event: { payload: unknown }) => void) | null = null;
    mocks.listen.mockImplementation(async (_event, listener) => {
      liveListener = listener;
      return vi.fn();
    });
    mocks.invoke.mockImplementation(async () => {
      liveListener?.({ payload: snapshot(9) });
      return snapshot(8);
    });
    const listener = vi.fn();

    await createPresenceInteractionSource().subscribe(listener);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith(snapshot(9));
  });
});
