import { describe, expect, it, vi } from "vitest";

import {
  type AnimationFrameScheduler,
  RendererFrameLoop,
} from "./RendererFrameLoop";

function schedulerHarness() {
  let now = 0;
  let nextId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  const scheduler: AnimationFrameScheduler = {
    now: () => now,
    request(callback) {
      const id = nextId++;
      callbacks.set(id, callback);
      return id;
    },
    cancel(frame) {
      callbacks.delete(frame);
    },
  };
  return {
    scheduler,
    pending: () => callbacks.size,
    flush(at: number) {
      now = at;
      const entries = [...callbacks.values()];
      callbacks.clear();
      for (const callback of entries) callback(at);
    },
  };
}

describe("RendererFrameLoop", () => {
  it("cancels every pending frame on stop and dispose", () => {
    const harness = schedulerHarness();
    const draw = vi.fn();
    const loop = new RendererFrameLoop(draw, () => 16, harness.scheduler);

    loop.start();
    expect(draw).toHaveBeenCalledOnce();
    expect(harness.pending()).toBe(1);
    loop.stop();
    expect(harness.pending()).toBe(0);
    harness.flush(32);
    expect(draw).toHaveBeenCalledOnce();

    loop.start();
    expect(harness.pending()).toBe(1);
    loop.dispose();
    expect(harness.pending()).toBe(0);
    loop.start();
    expect(harness.pending()).toBe(0);
  });

  it("renders one frame for reduced motion and resumes scheduling when enabled", () => {
    const harness = schedulerHarness();
    const draw = vi.fn();
    let interval = Number.POSITIVE_INFINITY;
    const loop = new RendererFrameLoop(draw, () => interval, harness.scheduler);

    loop.start();
    expect(draw).toHaveBeenCalledOnce();
    expect(harness.pending()).toBe(0);
    loop.refresh();
    expect(draw).toHaveBeenCalledTimes(2);

    interval = 16;
    loop.refresh();
    expect(harness.pending()).toBe(1);
    harness.flush(16);
    expect(harness.pending()).toBe(1);
  });

  it("paces 144 FPS without collapsing to a divisor of a 299 Hz display", () => {
    const harness = schedulerHarness();
    const draw = vi.fn();
    const loop = new RendererFrameLoop(draw, () => 1_000 / 144, harness.scheduler);

    loop.start();
    for (let frame = 1; frame <= 299; frame += 1) {
      harness.flush(frame * (1_000 / 299));
    }

    expect(draw.mock.calls.length).toBeGreaterThanOrEqual(144);
    expect(draw.mock.calls.length).toBeLessThanOrEqual(146);
  });
});
