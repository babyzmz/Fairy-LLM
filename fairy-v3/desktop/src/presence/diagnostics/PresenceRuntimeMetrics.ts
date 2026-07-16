const MAX_RUNTIME_SAMPLES = 4_096;

export const PRESENCE_RUNTIME_METRICS_RESET_EVENT =
  "fairy:presence-runtime-metrics-reset";

export interface BackdropTimingSample {
  sequence: number;
  captured_at_ms: number;
  capture_total_ms: number | null;
  frame_pack_ms: number | null;
  ipc_roundtrip_ms: number;
  js_parse_ms: number;
}

export interface PresenceRuntimeMetricsSnapshot {
  frame_samples: number;
  fps_avg: number | null;
  fps_p1: number | null;
  deadline_miss_count: number;
  backdrop_samples: number;
  backdrop_fps_avg: number | null;
  capture_p95_ms: number | null;
  pack_p95_ms: number | null;
  ipc_p95_ms: number | null;
  parse_p95_ms: number | null;
  upload_cpu_p95_ms: number | null;
  backdrop_age_p95_ms: number | null;
  dropped_frame_count: number;
}

export class PresenceRuntimeMetrics {
  private frameIntervals: number[] = [];
  private backdropIntervals: number[] = [];
  private captureSamples: number[] = [];
  private packSamples: number[] = [];
  private ipcSamples: number[] = [];
  private parseSamples: number[] = [];
  private uploadSamples: number[] = [];
  private backdropAgeSamples: number[] = [];
  private lastFrameAt: number | null = null;
  private lastBackdropSequence: number | null = null;
  private lastBackdropAt: number | null = null;
  private deadlineMisses = 0;
  private droppedFrames = 0;

  reset(): void {
    this.frameIntervals = [];
    this.backdropIntervals = [];
    this.captureSamples = [];
    this.packSamples = [];
    this.ipcSamples = [];
    this.parseSamples = [];
    this.uploadSamples = [];
    this.backdropAgeSamples = [];
    this.lastFrameAt = null;
    this.lastBackdropSequence = null;
    this.lastBackdropAt = null;
    this.deadlineMisses = 0;
    this.droppedFrames = 0;
  }

  recordAnimationFrame(now: number, targetFramesPerSecond: number): void {
    if (!Number.isFinite(now) || !Number.isFinite(targetFramesPerSecond)) return;
    if (targetFramesPerSecond <= 0) return;
    if (this.lastFrameAt !== null) {
      const interval = now - this.lastFrameAt;
      if (interval > 0 && interval < 1_000) {
        pushBounded(this.frameIntervals, interval);
        if (interval > (1_000 / targetFramesPerSecond) * 1.5) {
          this.deadlineMisses += 1;
        }
      }
    }
    this.lastFrameAt = now;
  }

  recordBackdrop(sample: BackdropTimingSample, nowMs = Date.now()): void {
    if (this.lastBackdropAt !== null) {
      const interval = nowMs - this.lastBackdropAt;
      if (interval > 0 && interval < 5_000) {
        pushBounded(this.backdropIntervals, interval);
      }
    }
    this.lastBackdropAt = nowMs;
    pushOptional(this.captureSamples, sample.capture_total_ms);
    pushOptional(this.packSamples, sample.frame_pack_ms);
    pushBounded(this.ipcSamples, sample.ipc_roundtrip_ms);
    pushBounded(this.parseSamples, sample.js_parse_ms);
    pushBounded(
      this.backdropAgeSamples,
      Math.max(0, nowMs - sample.captured_at_ms),
    );
    if (
      this.lastBackdropSequence !== null &&
      sample.sequence > this.lastBackdropSequence + 1
    ) {
      this.droppedFrames += sample.sequence - this.lastBackdropSequence - 1;
    }
    this.lastBackdropSequence = Math.max(
      sample.sequence,
      this.lastBackdropSequence ?? sample.sequence,
    );
  }

  recordTextureUploadCpu(durationMs: number): void {
    pushBounded(this.uploadSamples, durationMs);
  }

  snapshot(): PresenceRuntimeMetricsSnapshot {
    const averageInterval = average(this.frameIntervals);
    const averageBackdropInterval = average(this.backdropIntervals);
    const p99Interval = percentile(this.frameIntervals, 0.99);
    return {
      frame_samples: this.frameIntervals.length,
      fps_avg: averageInterval === null ? null : 1_000 / averageInterval,
      fps_p1: p99Interval === null ? null : 1_000 / p99Interval,
      deadline_miss_count: this.deadlineMisses,
      backdrop_samples: this.backdropIntervals.length,
      backdrop_fps_avg:
        averageBackdropInterval === null ? null : 1_000 / averageBackdropInterval,
      capture_p95_ms: percentile(this.captureSamples, 0.95),
      pack_p95_ms: percentile(this.packSamples, 0.95),
      ipc_p95_ms: percentile(this.ipcSamples, 0.95),
      parse_p95_ms: percentile(this.parseSamples, 0.95),
      upload_cpu_p95_ms: percentile(this.uploadSamples, 0.95),
      backdrop_age_p95_ms: percentile(this.backdropAgeSamples, 0.95),
      dropped_frame_count: this.droppedFrames,
    };
  }
}

export function writeRuntimeMetricsDataset(
  element: HTMLElement,
  snapshot: PresenceRuntimeMetricsSnapshot,
): void {
  element.dataset.frameSamples = String(snapshot.frame_samples);
  element.dataset.fpsAvg = format(snapshot.fps_avg);
  element.dataset.fpsP1 = format(snapshot.fps_p1);
  element.dataset.deadlineMissCount = String(snapshot.deadline_miss_count);
  element.dataset.backdropSamples = String(snapshot.backdrop_samples);
  element.dataset.backdropFpsAvg = format(snapshot.backdrop_fps_avg);
  element.dataset.captureP95Ms = format(snapshot.capture_p95_ms);
  element.dataset.packP95Ms = format(snapshot.pack_p95_ms);
  element.dataset.ipcP95Ms = format(snapshot.ipc_p95_ms);
  element.dataset.parseP95Ms = format(snapshot.parse_p95_ms);
  element.dataset.uploadCpuP95Ms = format(snapshot.upload_cpu_p95_ms);
  element.dataset.backdropAgeP95Ms = format(snapshot.backdrop_age_p95_ms);
  element.dataset.droppedFrameCount = String(snapshot.dropped_frame_count);
}

function pushOptional(target: number[], value: number | null): void {
  if (value !== null) pushBounded(target, value);
}

function pushBounded(target: number[], value: number): void {
  if (!Number.isFinite(value) || value < 0) return;
  if (target.length >= MAX_RUNTIME_SAMPLES) target.shift();
  target.push(value);
}

function average(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function percentile(values: readonly number[], quantile: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * quantile) - 1)] ?? null;
}

function format(value: number | null): string {
  return value === null ? "unavailable" : value.toFixed(3);
}
