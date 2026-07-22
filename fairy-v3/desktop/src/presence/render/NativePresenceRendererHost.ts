import { invoke, isTauri } from "@tauri-apps/api/core";
import { z } from "zod";

import type {
  PresenceRendererHealth,
  PresenceRenderSnapshot,
} from "./presenceRenderer";
import { liquidShapeTargetForSnapshot } from "./liquidGlassMaterial";
import type { PresenceRendererMode } from "./rendererSupport";

const nativeGpuStatusSchema = z.object({
  backend: z.enum(["unavailable", "windows_host_backdrop_d3d11_composition"]),
  optics_source: z.enum([
    "none",
    "host_backdrop",
  ]).default("host_backdrop"),
  lifecycle: z.enum(["idle", "starting", "running", "stopping", "failed"]),
  zero_copy_capture: z.boolean(),
  pixel_ipc: z.boolean(),
  hdr_capture: z.boolean(),
  target_frame_rate: z.number().int().positive(),
  effective_frame_rate: z.number().int().nonnegative(),
  display_refresh_rate_hz: z.number().int().nonnegative(),
  capture_frame_rate_limit: z.number().int().nonnegative(),
  frames_presented: z.number().int().nonnegative(),
  capture_fps_avg: z.number().nonnegative(),
  frame_interval_p1_fps: z.number().nonnegative(),
  source_frames_received: z.number().int().nonnegative(),
  source_capture_fps_avg: z.number().nonnegative(),
  source_frame_interval_p1_fps: z.number().nonnegative(),
  callback_to_present_p95_ms: z.number().nonnegative(),
  present_p95_ms: z.number().nonnegative(),
  surface_width: z.number().int().nonnegative(),
  surface_height: z.number().int().nonnegative(),
  target_x: z.number().int(),
  target_y: z.number().int(),
  monitor_x: z.number().int(),
  monitor_y: z.number().int(),
  monitor_width: z.number().int().nonnegative(),
  monitor_height: z.number().int().nonnegative(),
  monitor_handle: z.string().nullable(),
  monitor_device_name: z.string().nullable(),
  monitor_friendly_name: z.string().nullable(),
  adapter_name: z.string().nullable(),
  adapter_index: z.number().int().nonnegative().nullable(),
  output_device_name: z.string().nullable(),
  output_index: z.number().int().nonnegative().nullable(),
  hdr_color_space: z.string().nullable(),
  capture_item_width: z.number().int().nonnegative(),
  capture_item_height: z.number().int().nonnegative(),
  capture_window_handle: z.string().regex(/^0x[0-9A-F]{16}$/u).nullable(),
  capture_source_stage: z.string().min(1).max(64),
  capture_source_hresult: z.string().regex(/^0x[0-9A-F]{8}$/u).nullable(),
  started_at_ms: z.number().int().nonnegative().nullable(),
  last_presented_at_ms: z.number().int().nonnegative().nullable(),
  presentation_revision: z.number().int().nonnegative(),
  fallback_reason: z.string().max(128).nullable().default(null),
  error_code: z.string().nullable(),
}).strict();

export type NativeGpuStatus = z.infer<typeof nativeGpuStatusSchema>;
export type NativeGpuVisualState =
  | "idle"
  | "aware"
  | "forming"
  | "input"
  | "options"
  | "submitting"
  | "thinking"
  | "tool"
  | "responding"
  | "speaking"
  | "notify"
  | "approval"
  | "error"
  | "returning"
  | "suspended"
  | "repositioning"
  | "sleeping";
export type NativeGpuFrameRateLimit = 15 | 30 | 60 | 144 | 300;
export type NativeRendererState =
  | "idle"
  | "starting"
  | "running"
  | "suspended"
  | "fallback"
  | "stopping"
  | "disposed";

export interface NativeGpuPresentationRequest {
  capsule_visible: boolean;
  input_surface_visible: boolean;
  input_surface_height: number;
  expansion_direction: "left" | "right";
  visual_state: NativeGpuVisualState;
  opacity: number;
  voice_level: number;
  reduced_motion: boolean;
  reduced_transparency: boolean;
  increased_contrast: boolean;
  particles_enabled: boolean;
  frame_rate_limit: NativeGpuFrameRateLimit;
  shape_droplet: number;
  shape_bridge: number;
  shape_capsule: number;
  returning: boolean;
  core_x: number;
  core_y: number;
  capsule_x: number;
  capsule_y: number;
  capsule_half_width: number;
}

export type NativeGpuInvoke = (
  command: string,
  args?: Record<string, unknown>,
) => Promise<unknown>;

export interface NativePresenceRendererHostOptions {
  invokeCommand?: NativeGpuInvoke | null;
  onHealth?(health: PresenceRendererHealth): void;
  onStateChange?(state: NativeRendererState): void;
  watchdogIntervalMs?: number;
  recoveryDelaysMs?: readonly number[];
}

export class NativePresenceRendererHost {
  private readonly invokeCommand: NativeGpuInvoke | null;
  private readonly watchdogIntervalMs: number;
  private readonly recoveryDelaysMs: readonly number[];
  private state: NativeRendererState = "idle";
  private latestSnapshot: PresenceRenderSnapshot | null = null;
  private lastPresentationKey = "";
  private operation: Promise<void> = Promise.resolve();
  private updateScheduled = false;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private recoveryTimer: ReturnType<typeof setTimeout> | null = null;
  private startedTargetFrameRate: 60 | 144 | 300 | null = null;
  private startedSurfaceKey: string | null = null;
  private desiredRunning = false;
  private consecutiveFailures = 0;
  private sourceRebindScheduled = false;
  private suspensionRequested = false;
  private disposed = false;
  private requestedMode: PresenceRendererMode = "auto";
  private lastStatus: NativeGpuStatus | null = null;

  constructor(private readonly options: NativePresenceRendererHostOptions = {}) {
    this.invokeCommand = options.invokeCommand === undefined
      ? (isTauri() ? invoke : null)
      : options.invokeCommand;
    this.watchdogIntervalMs = Math.max(50, options.watchdogIntervalMs ?? 100);
    this.recoveryDelaysMs = (options.recoveryDelaysMs ?? [250, 1_000])
      .map((delay) => Math.max(0, delay));
  }

  start(snapshot: PresenceRenderSnapshot): Promise<boolean> {
    this.suspensionRequested = false;
    this.desiredRunning = true;
    this.clearRecovery();
    this.latestSnapshot = snapshot;
    return this.enqueue(() => this.startNow(snapshot));
  }

  setSnapshot(snapshot: PresenceRenderSnapshot): void {
    this.latestSnapshot = snapshot;
    if (this.suspensionRequested || this.state === "suspended") return;
    const nextSurfaceKey = nativeSurfaceKey(snapshot);
    if (
      this.state === "running" &&
      this.startedSurfaceKey !== null &&
      nextSurfaceKey !== null &&
      nextSurfaceKey !== this.startedSurfaceKey
    ) {
      this.scheduleSourceRebind();
      return;
    }
    if (this.state === "running" && this.startedSurfaceKey === null) {
      this.startedSurfaceKey = nextSurfaceKey;
    }
    if (this.state !== "running" || this.updateScheduled || this.disposed) return;
    this.updateScheduled = true;
    queueMicrotask(() => {
      this.updateScheduled = false;
      if (this.state !== "running" || this.disposed) return;
      void this.enqueue(() => this.updateNow());
    });
  }

  stop(): Promise<void> {
    this.suspensionRequested = false;
    this.desiredRunning = false;
    this.clearRecovery();
    return this.enqueue(() => this.stopNow(true));
  }

  suspend(): Promise<void> {
    if (this.disposed) return this.operation;
    this.suspensionRequested = true;
    this.clearRecovery();
    return this.enqueue(async () => {
      if (this.state === "suspended") return;
      await this.stopNow(false);
      if (!this.disposed) {
        this.setState("suspended");
        this.report("suspended", null);
      }
    });
  }

  resume(snapshot: PresenceRenderSnapshot): Promise<boolean> {
    if (this.disposed) return Promise.resolve(false);
    this.suspensionRequested = false;
    this.desiredRunning = true;
    this.latestSnapshot = snapshot;
    this.clearRecovery();
    return this.enqueue(() => this.startNow(snapshot));
  }

  reconfigure(snapshot: PresenceRenderSnapshot): void {
    if (this.disposed) return;
    this.suspensionRequested = false;
    this.latestSnapshot = snapshot;
    this.scheduleSourceRebind();
  }

  refreshCaptureSource(snapshot: PresenceRenderSnapshot): void {
    if (this.disposed) return;
    this.latestSnapshot = snapshot;
    if (this.state === "suspended") {
      void this.resume(snapshot);
      return;
    }
    if (
      nativeSurfaceKey(snapshot) !== this.startedSurfaceKey ||
      snapshot.target_frame_rate !== this.startedTargetFrameRate
    ) {
      this.scheduleSourceRebind();
      return;
    }
    this.setSnapshot(snapshot);
  }

  dispose(): Promise<void> {
    if (this.disposed) return this.operation;
    this.disposed = true;
    this.suspensionRequested = false;
    this.desiredRunning = false;
    this.clearRecovery();
    this.clearWatchdog();
    return this.enqueue(async () => {
      await this.stopNow(false);
      this.setState("disposed");
    });
  }

  currentState(): NativeRendererState {
    return this.state;
  }

  setRequestedMode(mode: PresenceRendererMode): void {
    this.requestedMode = mode;
  }

  private async startNow(snapshot: PresenceRenderSnapshot): Promise<boolean> {
    if (this.disposed || !this.desiredRunning || this.suspensionRequested) return false;
    if (this.invokeCommand === null) {
      this.report("failed", "NATIVE_GPU_UNAVAILABLE");
      this.setState("fallback");
      return false;
    }
    if (this.state === "running") {
      if (this.startedTargetFrameRate === snapshot.target_frame_rate) {
        await this.updateNow();
        return this.state === "running";
      }
      try {
        return await this.rebindNow(snapshot);
      } catch {
        // The active session remains valid until a replacement has presented its first frame.
        return false;
      }
    }
    this.setState("starting");
    this.report("initializing", null);
    const presentation = nativePresentationForSnapshot(snapshot);
    try {
      const status = parseHealthyStatus(await this.invokeCommand("pet_native_gpu_start", {
        request: {
          target_frame_rate: snapshot.target_frame_rate,
          ...presentation,
        },
      }));
      this.lastStatus = status;
      this.lastPresentationKey = presentationKey(presentation);
      this.startedTargetFrameRate = snapshot.target_frame_rate;
      this.startedSurfaceKey = nativeSurfaceKey(snapshot);
      this.consecutiveFailures = 0;
      this.setState("running");
      this.startWatchdog();
      this.report("running", null);
      return status.lifecycle === "running";
    } catch {
      await this.stopAfterFailure();
      this.setState("fallback");
      this.report("failed", "NATIVE_GPU_START_FAILED");
      this.scheduleRecovery();
      return false;
    }
  }

  private async updateNow(): Promise<void> {
    const snapshot = this.latestSnapshot;
    if (snapshot === null || this.invokeCommand === null || this.state !== "running") return;
    const presentation = nativePresentationForSnapshot(snapshot);
    const key = presentationKey(presentation);
    if (key === this.lastPresentationKey) return;
    try {
      this.lastStatus = parseHealthyStatus(await this.invokeCommand("pet_native_gpu_update", {
        request: presentation,
      }));
      this.lastPresentationKey = key;
    } catch {
      await this.failRuntime("NATIVE_GPU_UPDATE_FAILED");
    }
  }

  private async stopNow(report: boolean): Promise<void> {
    this.clearWatchdog();
    if (this.state === "idle" || this.state === "disposed") return;
    this.setState("stopping");
    if (this.invokeCommand !== null) {
      try {
        await this.invokeCommand("pet_native_gpu_stop");
      } catch {
        if (report) this.report("failed", "NATIVE_GPU_STOP_FAILED");
      }
    }
    this.lastPresentationKey = "";
    this.startedTargetFrameRate = null;
    this.startedSurfaceKey = null;
    this.sourceRebindScheduled = false;
    this.lastStatus = null;
    if (!this.disposed) this.setState("idle");
    if (report) this.report("stopped", null);
  }

  private startWatchdog(): void {
    this.clearWatchdog();
    this.watchdog = setInterval(() => {
      void this.enqueue(async () => {
        if (this.state !== "running" || this.invokeCommand === null) return;
        try {
          this.lastStatus = parseHealthyStatus(
            await this.invokeCommand("pet_native_gpu_status"),
          );
        } catch {
          await this.failRuntime("NATIVE_GPU_RUNTIME_FAILED");
        }
      });
    }, this.watchdogIntervalMs);
  }

  private clearWatchdog(): void {
    if (this.watchdog === null) return;
    clearInterval(this.watchdog);
    this.watchdog = null;
  }

  private async failRuntime(
    errorCode: Extract<PresenceRendererHealth["error_code"], `NATIVE_${string}`>,
  ): Promise<void> {
    if (this.state !== "running") return;
    this.clearWatchdog();
    await this.stopAfterFailure();
    this.setState("fallback");
    this.report("failed", errorCode);
    this.scheduleRecovery();
  }

  private async stopAfterFailure(): Promise<void> {
    if (this.invokeCommand === null) return;
    try {
      await this.invokeCommand("pet_native_gpu_stop");
    } catch {
      // The original failure remains the actionable health signal.
    }
    this.lastPresentationKey = "";
    this.startedTargetFrameRate = null;
    this.startedSurfaceKey = null;
    this.lastStatus = null;
  }

  private scheduleSourceRebind(): void {
    if (
      this.sourceRebindScheduled ||
      this.disposed ||
      !this.desiredRunning ||
      this.suspensionRequested ||
      this.state !== "running"
    ) return;
    this.sourceRebindScheduled = true;
    queueMicrotask(() => {
      const snapshot = this.latestSnapshot;
      if (
        snapshot === null ||
        this.disposed ||
        !this.desiredRunning ||
        this.suspensionRequested ||
        this.state !== "running"
      ) {
        this.sourceRebindScheduled = false;
        return;
      }
      void this.enqueue(async () => {
        let rebound = false;
        try {
          rebound = await this.rebindNow(snapshot);
        } catch {
          // Rebind is candidate-first. The active HostBackdrop surface remains live until a
          // replacement has presented, so a monitor transition never exposes a stale capture.
        } finally {
          this.sourceRebindScheduled = false;
          if (rebound) this.reconcileLatestSnapshot();
        }
      });
    });
  }

  private reconcileLatestSnapshot(): void {
    const snapshot = this.latestSnapshot;
    if (
      snapshot === null ||
      this.disposed ||
      !this.desiredRunning ||
      this.suspensionRequested ||
      this.state !== "running"
    ) return;
    if (
      nativeSurfaceKey(snapshot) !== this.startedSurfaceKey ||
      snapshot.target_frame_rate !== this.startedTargetFrameRate
    ) {
      this.scheduleSourceRebind();
      return;
    }
    if (presentationKey(nativePresentationForSnapshot(snapshot)) !== this.lastPresentationKey) {
      this.setSnapshot(snapshot);
    }
  }

  private async rebindNow(snapshot: PresenceRenderSnapshot): Promise<boolean> {
    if (this.invokeCommand === null || this.state !== "running") return false;
    const presentation = nativePresentationForSnapshot(snapshot);
    this.lastStatus = parseHealthyStatus(await this.invokeCommand("pet_native_gpu_rebind", {
      request: {
        target_frame_rate: snapshot.target_frame_rate,
        ...presentation,
      },
    }));
    this.lastPresentationKey = presentationKey(presentation);
    this.startedTargetFrameRate = snapshot.target_frame_rate;
    this.startedSurfaceKey = nativeSurfaceKey(snapshot);
    return true;
  }

  private scheduleRecovery(): void {
    if (!this.desiredRunning || this.disposed || this.suspensionRequested) return;
    const delay = this.recoveryDelaysMs[this.consecutiveFailures];
    this.consecutiveFailures += 1;
    if (delay === undefined) return;
    this.clearRecovery();
    this.recoveryTimer = setTimeout(() => {
      this.recoveryTimer = null;
      const snapshot = this.latestSnapshot;
      if (
        snapshot === null ||
        !this.desiredRunning ||
        this.disposed ||
        this.suspensionRequested
      ) return;
      void this.enqueue(() => this.startNow(snapshot));
    }, delay);
  }

  private clearRecovery(): void {
    if (this.recoveryTimer === null) return;
    clearTimeout(this.recoveryTimer);
    this.recoveryTimer = null;
  }

  private report(
    status: PresenceRendererHealth["status"],
    error_code: PresenceRendererHealth["error_code"],
  ): void {
    const nativeActive = status === "running" || status === "suspended";
    const fallback = ["context_lost", "fallback", "failed"].includes(status);
    this.options.onHealth?.({
      requested_mode: this.requestedMode,
      mode: "native",
      actual_backend: nativeActive ? "native_liquid_glass" : "none",
      optics_source: nativeActive
        ? this.lastStatus?.optics_source ?? "host_backdrop"
        : "none",
      status,
      error_code,
      fallback_reason: fallback ? error_code : null,
      monitor_refresh_hz: this.lastStatus?.display_refresh_rate_hz ?? 0,
      effective_fps: this.lastStatus?.effective_frame_rate ?? 0,
    });
  }

  private setState(state: NativeRendererState): void {
    if (this.state === state) return;
    this.state = state;
    this.options.onStateChange?.(state);
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.operation.then(operation, operation);
    this.operation = result.then(() => undefined, () => undefined);
    return result;
  }
}

export function nativePresentationForSnapshot(
  snapshot: PresenceRenderSnapshot,
): NativeGpuPresentationRequest {
  const shape = liquidShapeTargetForSnapshot(snapshot);
  const placement = snapshot.interaction?.placement;
  const scale = clamp(placement?.scale_factor ?? 1, 0.5, 4);
  const direction = placement?.expansion_direction ?? "right";
  const capsuleWidth = clamp(snapshot.input_capsule_width, 220, 360);
  const coreX = placement === undefined
    ? direction === "left" ? 544 : 96
    : (placement.anchor.x - placement.render_frame.x) / scale;
  const coreY = placement === undefined
    ? 88
    : (placement.anchor.y - placement.render_frame.y) / scale;
  return {
    capsule_visible: snapshot.input_capsule_visible,
    input_surface_visible: snapshot.input_surface_visible,
    input_surface_height: clamp(snapshot.input_capsule_height, 64, 104),
    expansion_direction: snapshot.interaction?.placement.expansion_direction ?? "right",
    visual_state: nativeVisualStateForSnapshot(snapshot),
    opacity: clamp(snapshot.opacity, 0.2, 1),
    voice_level: snapshot.motion.state === "speaking"
      ? clamp(snapshot.voice_level, 0, 1)
      : 0,
    reduced_motion: snapshot.reduced_motion,
    reduced_transparency: snapshot.reduced_transparency === true,
    increased_contrast: snapshot.increased_contrast === true,
    particles_enabled: snapshot.particles_enabled,
    frame_rate_limit: nativeFrameRateLimit(snapshot),
    shape_droplet: shape.droplet,
    shape_bridge: shape.bridge,
    shape_capsule: shape.capsule,
    returning:
      snapshot.motion.state === "returning" ||
      snapshot.interaction?.phase === "returning",
    core_x: clamp(coreX, 0, 640),
    core_y: clamp(coreY, 0, 260),
    capsule_x: direction === "left"
      ? 640 - 24 - capsuleWidth / 2
      : 24 + capsuleWidth / 2,
    capsule_y: 220,
    capsule_half_width: capsuleWidth / 2 - 8,
  };
}

export function nativeFrameRateLimit(
  snapshot: PresenceRenderSnapshot,
): NativeGpuFrameRateLimit {
  return Math.min(
    snapshot.target_frame_rate,
    snapshot.frame_rate_limit,
  ) as NativeGpuFrameRateLimit;
}

export function nativeVisualStateForSnapshot(
  snapshot: PresenceRenderSnapshot,
): NativeGpuVisualState {
  switch (snapshot.motion.state) {
    case "idle": return "idle";
    case "aware": return "aware";
    case "forming": return "forming";
    case "input": return "input";
    case "options": return "options";
    case "submitting": return "submitting";
    case "thinking": return snapshot.motion.activity === "tool" ? "tool" : "thinking";
    case "responding": return "responding";
    case "speaking": return "speaking";
    case "notify": return "notify";
    case "awaiting_confirmation": return "approval";
    case "error": return "error";
    case "returning": return "returning";
    case "suspended": return "suspended";
    case "repositioning": return "repositioning";
    case "sleeping": return "sleeping";
  }
}

function parseHealthyStatus(value: unknown): NativeGpuStatus {
  const status = nativeGpuStatusSchema.parse(value);
  if (
    status.lifecycle !== "running" ||
    status.optics_source !== "host_backdrop" ||
    status.pixel_ipc
  ) {
    throw new Error(status.error_code ?? "PRESENCE_NATIVE_GPU_UNHEALTHY");
  }
  return status;
}

function presentationKey(presentation: NativeGpuPresentationRequest): string {
  return JSON.stringify(presentation);
}

export function nativeSurfaceKey(snapshot: PresenceRenderSnapshot): string | null {
  const placement = snapshot.interaction?.placement;
  if (placement === undefined) return null;
  const workArea = placement.monitor_work_area;
  const renderFrame = placement.render_frame;
  return [
    workArea.x,
    workArea.y,
    workArea.width,
    workArea.height,
    placement.scale_factor,
    renderFrame.width,
    renderFrame.height,
  ].join(":");
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? value : minimum));
}
