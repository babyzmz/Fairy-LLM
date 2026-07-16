import { describe, expect, it } from "vitest";

import {
  PresenceRuntimeMetrics,
  writeRuntimeMetricsDataset,
} from "./PresenceRuntimeMetrics";

describe("Presence runtime metrics", () => {
  it("reports frame pacing and bounded backdrop stage percentiles", () => {
    const metrics = new PresenceRuntimeMetrics();
    metrics.recordAnimationFrame(0, 60);
    metrics.recordAnimationFrame(16, 60);
    metrics.recordAnimationFrame(32, 60);
    metrics.recordAnimationFrame(72, 60);
    metrics.recordBackdrop({
      sequence: 4,
      captured_at_ms: 900,
      capture_total_ms: 4,
      frame_pack_ms: 1,
      ipc_roundtrip_ms: 6,
      js_parse_ms: 0.5,
    }, 1_000);
    metrics.recordBackdrop({
      sequence: 7,
      captured_at_ms: 950,
      capture_total_ms: 8,
      frame_pack_ms: 2,
      ipc_roundtrip_ms: 10,
      js_parse_ms: 1,
    }, 1_100);
    metrics.recordTextureUploadCpu(3);

    const snapshot = metrics.snapshot();
    expect(snapshot.frame_samples).toBe(3);
    expect(snapshot.fps_avg).toBeCloseTo(1_000 / 24);
    expect(snapshot.fps_p1).toBe(25);
    expect(snapshot.deadline_miss_count).toBe(1);
    expect(snapshot.backdrop_samples).toBe(1);
    expect(snapshot.backdrop_fps_avg).toBe(10);
    expect(snapshot.capture_p95_ms).toBe(8);
    expect(snapshot.ipc_p95_ms).toBe(10);
    expect(snapshot.dropped_frame_count).toBe(2);

    const target = document.createElement("canvas");
    writeRuntimeMetricsDataset(target, snapshot);
    expect(target.dataset).toMatchObject({
      frameSamples: "3",
      fpsP1: "25.000",
      backdropFpsAvg: "10.000",
      captureP95Ms: "8.000",
      droppedFrameCount: "2",
    });
  });

  it("starts a clean measurement window after reset", () => {
    const metrics = new PresenceRuntimeMetrics();
    metrics.recordAnimationFrame(0, 60);
    metrics.recordAnimationFrame(100, 60);
    metrics.recordBackdrop({
      sequence: 1,
      captured_at_ms: 0,
      capture_total_ms: 50,
      frame_pack_ms: 10,
      ipc_roundtrip_ms: 80,
      js_parse_ms: 5,
    }, 100);

    metrics.reset();
    metrics.recordAnimationFrame(200, 60);
    metrics.recordAnimationFrame(216, 60);
    metrics.recordBackdrop({
      sequence: 20,
      captured_at_ms: 210,
      capture_total_ms: 4,
      frame_pack_ms: 1,
      ipc_roundtrip_ms: 6,
      js_parse_ms: 0.5,
    }, 216);

    expect(metrics.snapshot()).toMatchObject({
      frame_samples: 1,
      deadline_miss_count: 0,
      backdrop_samples: 0,
      backdrop_fps_avg: null,
      capture_p95_ms: 4,
      ipc_p95_ms: 6,
      dropped_frame_count: 0,
    });
  });
});
