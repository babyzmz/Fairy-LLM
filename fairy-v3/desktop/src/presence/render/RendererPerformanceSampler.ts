export const CPU_TIMING_SAMPLE_LIMIT = 300;
export const GPU_TIMER_SAMPLE_INTERVAL_FRAMES = 60;
export const GPU_TIMER_SAMPLE_LIMIT = 60;
export const GPU_QUERY_MAX_POLLS = 120;

export function shouldSampleGpuFrame(
  renderedFrames: number,
  startedSamples: number,
): boolean {
  return startedSamples < GPU_TIMER_SAMPLE_LIMIT &&
    renderedFrames % GPU_TIMER_SAMPLE_INTERVAL_FRAMES === 0;
}

export interface RendererPerformanceSnapshot {
  sample_count: number;
  cpu_frame_p95_ms: number | null;
  gpu_frame_p95_ms: number | null;
  heap_growth_bytes: number | null;
}

export class RendererPerformanceSampler {
  private cpuSamples: number[] = [];
  private gpuSamples: number[] = [];
  private initialHeapBytes: number | null = null;
  private currentHeapBytes: number | null = null;

  recordCpuFrame(durationMs: number): boolean {
    if (this.cpuSamples.length >= CPU_TIMING_SAMPLE_LIMIT) return false;
    return this.push(this.cpuSamples, durationMs);
  }

  recordGpuFrame(durationMs: number): boolean {
    return this.push(this.gpuSamples, durationMs);
  }

  recordHeap(usedBytes: number | null): void {
    if (usedBytes === null || !Number.isFinite(usedBytes) || usedBytes < 0) return;
    this.initialHeapBytes ??= usedBytes;
    this.currentHeapBytes = usedBytes;
  }

  snapshot(): RendererPerformanceSnapshot {
    return {
      sample_count: this.cpuSamples.length,
      cpu_frame_p95_ms: percentile95(this.cpuSamples),
      gpu_frame_p95_ms: percentile95(this.gpuSamples),
      heap_growth_bytes:
        this.initialHeapBytes === null || this.currentHeapBytes === null
          ? null
          : this.currentHeapBytes - this.initialHeapBytes,
    };
  }

  get cpuSamplingComplete(): boolean {
    return this.cpuSamples.length >= CPU_TIMING_SAMPLE_LIMIT;
  }

  private push(target: number[], value: number): boolean {
    if (!Number.isFinite(value) || value < 0) return false;
    target.push(value);
    return true;
  }
}

interface GpuTimerExtension {
  readonly TIME_ELAPSED_EXT: number;
  readonly GPU_DISJOINT_EXT: number;
}

export class WebGlGpuTimer {
  private readonly extension: GpuTimerExtension | null;
  private active: WebGLQuery | null = null;
  private pending: Array<{ query: WebGLQuery; polls: number }> = [];
  private reusable: WebGLQuery[] = [];

  constructor(private readonly context: WebGL2RenderingContext) {
    this.extension = context.getExtension(
      "EXT_disjoint_timer_query_webgl2",
    ) as GpuTimerExtension | null;
  }

  get supported(): boolean {
    return this.extension !== null;
  }

  begin(): boolean {
    if (this.extension === null || this.active !== null || this.pending.length >= 8) return false;
    const query = this.reusable.pop() ?? this.context.createQuery();
    if (query === null) return false;
    try {
      this.context.beginQuery(this.extension.TIME_ELAPSED_EXT, query);
    } catch (error) {
      this.context.deleteQuery(query);
      throw error;
    }
    this.active = query;
    return true;
  }

  end(): void {
    if (this.extension === null || this.active === null) return;
    this.context.endQuery(this.extension.TIME_ELAPSED_EXT);
    this.pending.push({ query: this.active, polls: 0 });
    this.active = null;
  }

  hasPendingResults(): boolean {
    return this.pending.length > 0;
  }

  collect(): number[] {
    if (this.extension === null) return [];
    if (this.context.getParameter(this.extension.GPU_DISJOINT_EXT) === true) {
      this.clearPending();
      return [];
    }
    const durations: number[] = [];
    while (this.pending.length > 0) {
      const pending = this.pending[0];
      if (!this.context.getQueryParameter(pending.query, this.context.QUERY_RESULT_AVAILABLE)) {
        pending.polls += 1;
        if (pending.polls < GPU_QUERY_MAX_POLLS) break;
        this.pending.shift();
        this.context.deleteQuery(pending.query);
        continue;
      }
      const nanoseconds = Number(
        this.context.getQueryParameter(pending.query, this.context.QUERY_RESULT),
      );
      this.pending.shift();
      this.reusable.push(pending.query);
      if (Number.isFinite(nanoseconds) && nanoseconds >= 0) {
        durations.push(nanoseconds / 1_000_000);
      }
    }
    return durations;
  }

  dispose(): void {
    try {
      if (this.active !== null) this.context.deleteQuery(this.active);
      this.active = null;
      this.clearPending();
      this.clearReusable();
    } catch {
      this.active = null;
      this.pending = [];
      this.reusable = [];
    }
  }

  private clearPending() {
    for (const pending of this.pending) this.context.deleteQuery(pending.query);
    this.pending = [];
  }

  private clearReusable() {
    for (const query of this.reusable) this.context.deleteQuery(query);
    this.reusable = [];
  }
}

export function readPerformanceHeapBytes(): number | null {
  const candidate = performance as Performance & {
    memory?: { usedJSHeapSize?: number };
  };
  const used = candidate.memory?.usedJSHeapSize;
  return typeof used === "number" ? used : null;
}

export function writePerformanceDataset(
  element: HTMLElement,
  snapshot: RendererPerformanceSnapshot,
): void {
  element.dataset.timingSamples = String(snapshot.sample_count);
  element.dataset.cpuFrameP95Ms = formatMetric(snapshot.cpu_frame_p95_ms);
  element.dataset.gpuFrameP95Ms = formatMetric(snapshot.gpu_frame_p95_ms);
  element.dataset.heapGrowthBytes = formatMetric(snapshot.heap_growth_bytes);
}

function percentile95(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.ceil(sorted.length * 0.95) - 1] ?? null;
}

function formatMetric(value: number | null): string {
  return value === null ? "unavailable" : value.toFixed(3);
}
