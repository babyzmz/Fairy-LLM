import { describe, expect, it } from "vitest";

import {
  RendererPerformanceSampler,
  writePerformanceDataset,
} from "./RendererPerformanceSampler";

describe("RendererPerformanceSampler", () => {
  it("keeps bounded p95 samples and reports heap growth", () => {
    const sampler = new RendererPerformanceSampler();
    for (let value = 1; value <= 400; value += 1) sampler.recordCpuFrame(value);
    sampler.recordGpuFrame(2);
    sampler.recordGpuFrame(6);
    sampler.recordHeap(1_000);
    sampler.recordHeap(1_240);
    const result = sampler.snapshot();
    expect(result.sample_count).toBe(300);
    expect(result.cpu_frame_p95_ms).toBe(385);
    expect(result.gpu_frame_p95_ms).toBe(6);
    expect(result.heap_growth_bytes).toBe(240);
  });

  it("publishes metrics only as non-visible data attributes", () => {
    const target = document.createElement("canvas");
    writePerformanceDataset(target, {
      sample_count: 60,
      cpu_frame_p95_ms: 3.5,
      gpu_frame_p95_ms: null,
      heap_growth_bytes: 128,
    });
    expect(target.dataset).toMatchObject({
      timingSamples: "60",
      cpuFrameP95Ms: "3.500",
      gpuFrameP95Ms: "unavailable",
      heapGrowthBytes: "128.000",
    });
    expect(target.textContent).toBe("");
  });
});
