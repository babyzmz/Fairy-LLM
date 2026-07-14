import { describe, expect, it, vi } from "vitest";

import {
  CPU_TIMING_SAMPLE_LIMIT,
  GPU_QUERY_MAX_POLLS,
  GPU_TIMER_SAMPLE_INTERVAL_FRAMES,
  GPU_TIMER_SAMPLE_LIMIT,
  RendererPerformanceSampler,
  shouldSampleGpuFrame,
  WebGlGpuTimer,
  writePerformanceDataset,
} from "./RendererPerformanceSampler";

describe("RendererPerformanceSampler", () => {
  it("samples GPU timing once per bounded frame interval", () => {
    expect(GPU_TIMER_SAMPLE_INTERVAL_FRAMES).toBe(60);
    expect(GPU_TIMER_SAMPLE_LIMIT).toBe(60);
    expect(shouldSampleGpuFrame(0, 0)).toBe(true);
    expect(shouldSampleGpuFrame(1, 1)).toBe(false);
    expect(shouldSampleGpuFrame(59, 1)).toBe(false);
    expect(shouldSampleGpuFrame(60, 59)).toBe(true);
    expect(shouldSampleGpuFrame(120, 60)).toBe(false);
  });

  it("leaves completed GPU queries for the caller to collect", () => {
    const queryAvailable = 0x8867;
    const queryResult = 0x8866;
    const context = {
      QUERY_RESULT_AVAILABLE: queryAvailable,
      QUERY_RESULT: queryResult,
      getExtension: vi.fn(() => ({ TIME_ELAPSED_EXT: 1, GPU_DISJOINT_EXT: 2 })),
      createQuery: vi.fn(() => ({}) as WebGLQuery),
      beginQuery: vi.fn(),
      endQuery: vi.fn(),
      getParameter: vi.fn(() => false),
      getQueryParameter: vi.fn((_query: WebGLQuery, parameter: number) =>
        parameter === queryAvailable ? true : 2_000_000,
      ),
      deleteQuery: vi.fn(),
    } as unknown as WebGL2RenderingContext;
    const timer = new WebGlGpuTimer(context);

    timer.begin();
    timer.end();
    expect(timer.hasPendingResults()).toBe(true);
    timer.begin();

    expect(timer.collect()).toEqual([2]);
    expect(timer.hasPendingResults()).toBe(false);
    expect(context.deleteQuery).not.toHaveBeenCalled();
    timer.end();
    expect(timer.hasPendingResults()).toBe(true);
    expect(timer.collect()).toEqual([2]);
    expect(timer.hasPendingResults()).toBe(false);
    timer.begin();
    expect(context.createQuery).toHaveBeenCalledTimes(2);
    timer.dispose();
    expect(context.deleteQuery).toHaveBeenCalledTimes(2);
  });

  it("keeps bounded p95 samples and reports heap growth", () => {
    const sampler = new RendererPerformanceSampler();
    for (let value = 1; value <= 400; value += 1) sampler.recordCpuFrame(value);
    sampler.recordGpuFrame(2);
    sampler.recordGpuFrame(6);
    sampler.recordHeap(1_000);
    sampler.recordHeap(1_240);
    const result = sampler.snapshot();
    expect(result.sample_count).toBe(CPU_TIMING_SAMPLE_LIMIT);
    expect(result.cpu_frame_p95_ms).toBe(285);
    expect(result.gpu_frame_p95_ms).toBe(6);
    expect(result.heap_growth_bytes).toBe(240);
  });

  it("stops retaining CPU timing samples after the bounded warm-up", () => {
    const sampler = new RendererPerformanceSampler();
    for (let value = 0; value < CPU_TIMING_SAMPLE_LIMIT; value += 1) {
      expect(sampler.recordCpuFrame(value)).toBe(true);
    }
    expect(sampler.cpuSamplingComplete).toBe(true);
    expect(sampler.recordCpuFrame(9_999)).toBe(false);
    expect(sampler.snapshot().cpu_frame_p95_ms).toBe(284);
  });

  it("deletes GPU queries that never become available", () => {
    const context = {
      QUERY_RESULT_AVAILABLE: 0x8867,
      QUERY_RESULT: 0x8866,
      getExtension: vi.fn(() => ({ TIME_ELAPSED_EXT: 1, GPU_DISJOINT_EXT: 2 })),
      createQuery: vi.fn(() => ({}) as WebGLQuery),
      beginQuery: vi.fn(),
      endQuery: vi.fn(),
      getParameter: vi.fn(() => false),
      getQueryParameter: vi.fn(() => false),
      deleteQuery: vi.fn(),
    } as unknown as WebGL2RenderingContext;
    const timer = new WebGlGpuTimer(context);

    expect(timer.begin()).toBe(true);
    timer.end();
    for (let poll = 0; poll < GPU_QUERY_MAX_POLLS; poll += 1) timer.collect();

    expect(timer.hasPendingResults()).toBe(false);
    expect(context.deleteQuery).toHaveBeenCalledTimes(1);
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
