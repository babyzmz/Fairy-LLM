const MAX_SAMPLES = 300;

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

  recordCpuFrame(durationMs: number): void {
    this.push(this.cpuSamples, durationMs);
  }

  recordGpuFrame(durationMs: number): void {
    this.push(this.gpuSamples, durationMs);
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

  private push(target: number[], value: number) {
    if (!Number.isFinite(value) || value < 0) return;
    target.push(value);
    if (target.length > MAX_SAMPLES) target.splice(0, target.length - MAX_SAMPLES);
  }
}

interface GpuTimerExtension {
  readonly TIME_ELAPSED_EXT: number;
  readonly GPU_DISJOINT_EXT: number;
}

export class WebGlGpuTimer {
  private readonly extension: GpuTimerExtension | null;
  private active: WebGLQuery | null = null;
  private pending: WebGLQuery[] = [];

  constructor(private readonly context: WebGL2RenderingContext) {
    this.extension = context.getExtension(
      "EXT_disjoint_timer_query_webgl2",
    ) as GpuTimerExtension | null;
  }

  begin(): void {
    this.collect();
    if (this.extension === null || this.active !== null || this.pending.length >= 8) return;
    const query = this.context.createQuery();
    if (query === null) return;
    this.context.beginQuery(this.extension.TIME_ELAPSED_EXT, query);
    this.active = query;
  }

  end(): void {
    if (this.extension === null || this.active === null) return;
    this.context.endQuery(this.extension.TIME_ELAPSED_EXT);
    this.pending.push(this.active);
    this.active = null;
  }

  collect(): number[] {
    if (this.extension === null) return [];
    if (this.context.getParameter(this.extension.GPU_DISJOINT_EXT) === true) {
      this.clearPending();
      return [];
    }
    const durations: number[] = [];
    while (this.pending.length > 0) {
      const query = this.pending[0];
      if (!this.context.getQueryParameter(query, this.context.QUERY_RESULT_AVAILABLE)) break;
      const nanoseconds = Number(
        this.context.getQueryParameter(query, this.context.QUERY_RESULT),
      );
      this.context.deleteQuery(query);
      this.pending.shift();
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
    } catch {
      this.active = null;
      this.pending = [];
    }
  }

  private clearPending() {
    for (const query of this.pending) this.context.deleteQuery(query);
    this.pending = [];
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
