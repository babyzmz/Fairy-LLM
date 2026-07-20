import { CanvasCompatibilityRenderer } from "./CanvasCompatibilityRenderer";
import type { PresenceExperimentMode } from "../diagnostics/experimentMode";
import {
  type PresenceRenderer,
  type PresenceRendererHealth,
  type PresenceRenderSnapshot,
} from "./presenceRenderer";
import {
  detectPresenceRendererCapability,
  type PresenceRendererMode,
  resolvePresenceRendererMode,
} from "./rendererSupport";
import { RendererRecoveryCircuit } from "./rendererRecovery";

interface PresenceRendererHostOptions {
  webglCanvas: HTMLCanvasElement;
  compatibilityCanvas: HTMLCanvasElement;
  requestedMode: PresenceRendererMode;
  experimentMode?: PresenceExperimentMode;
  initialSnapshot: PresenceRenderSnapshot;
  onHealth(health: PresenceRendererHealth): void;
  now?: () => number;
}

export class PresenceRendererHost implements PresenceRenderer {
  private active: PresenceRenderer | null = null;
  private activeMode: "liquid" | "compatibility" = "compatibility";
  private snapshot: PresenceRenderSnapshot;
  private width = 1;
  private height = 1;
  private dpr = 1;
  private running = false;
  private suspended = false;
  private disposed = false;
  private generation = 0;
  private readonly recovery = new RendererRecoveryCircuit();
  private readonly now: () => number;

  constructor(private readonly options: PresenceRendererHostOptions) {
    this.snapshot = options.initialSnapshot;
    this.now = options.now ?? Date.now;
    options.webglCanvas.addEventListener("webglcontextlost", this.onContextLost);
    options.webglCanvas.addEventListener("webglcontextrestored", this.onContextRestored);
    this.showCanvas("compatibility");
  }

  async start(): Promise<void> {
    if (this.disposed) return;
    this.running = true;
    this.suspended = false;
    if (this.active !== null) {
      await this.active.start();
      this.report(this.activeMode, "running", null);
      return;
    }
    this.report("compatibility", "initializing", null);
    const capability = detectPresenceRendererCapability(this.options.webglCanvas);
    const mode = resolvePresenceRendererMode(this.options.requestedMode, capability);
    if (mode === "compatibility") {
      this.activateCompatibility(capability.error_code);
      return;
    }
    await this.activateLiquid();
  }

  stop(): void {
    this.running = false;
    this.active?.stop();
    this.report(this.activeMode, "stopped", null);
  }

  suspend(): void {
    this.suspended = true;
    this.active?.suspend();
    this.report(this.activeMode, "suspended", null);
  }

  resize(width: number, height: number, devicePixelRatio: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
    this.active?.resize(this.width, this.height, this.dpr);
  }

  setSnapshot(snapshot: PresenceRenderSnapshot): void {
    this.snapshot = snapshot;
    this.active?.setSnapshot(snapshot);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation += 1;
    this.options.webglCanvas.removeEventListener("webglcontextlost", this.onContextLost);
    this.options.webglCanvas.removeEventListener("webglcontextrestored", this.onContextRestored);
    this.active?.dispose();
    this.active = null;
    this.report(this.activeMode, "disposed", null);
  }

  private async activateLiquid() {
    const generation = ++this.generation;
    try {
      const { ThreeLiquidRenderer } = await import("./ThreeLiquidRenderer");
      if (this.disposed || generation !== this.generation) return;
      const next = new ThreeLiquidRenderer(
        this.options.webglCanvas,
        this.snapshot,
        this.options.experimentMode ?? "normal",
      );
      next.resize(this.width, this.height, this.dpr);
      next.setSnapshot(this.snapshot);
      if (this.running && !this.suspended) next.start();
      this.active?.dispose();
      this.active = next;
      this.activeMode = "liquid";
      this.showCanvas("liquid");
      this.report("liquid", this.suspended ? "suspended" : "running", null);
    } catch {
      if (this.disposed || generation !== this.generation) return;
      this.activateCompatibility("SHADER_INITIALIZATION_FAILED");
    }
  }

  private activateCompatibility(
    errorCode: PresenceRendererHealth["error_code"],
  ) {
    this.generation += 1;
    try {
      const next = new CanvasCompatibilityRenderer(
        this.options.compatibilityCanvas,
        this.snapshot,
      );
      next.resize(this.width, this.height, this.dpr);
      next.setSnapshot(this.snapshot);
      this.active?.dispose();
      this.active = next;
      this.activeMode = "compatibility";
      this.showCanvas("compatibility");
      if (this.running && !this.suspended) next.start();
      this.report(
        "compatibility",
        errorCode === null ? "running" : "fallback",
        errorCode,
      );
    } catch {
      this.active?.dispose();
      this.active = null;
      this.activeMode = "compatibility";
      this.report("compatibility", "failed", "CANVAS2D_UNAVAILABLE");
    }
  }

  private readonly onContextLost = (event: Event) => {
    event.preventDefault();
    if (this.disposed) return;
    this.active?.stop();
    this.recovery.recordContextLoss(this.now());
    this.report("liquid", "context_lost", "WEBGL_CONTEXT_LOST");
    this.activateCompatibility("WEBGL_CONTEXT_LOST");
  };

  private readonly onContextRestored = () => {
    if (
      this.disposed ||
      this.options.requestedMode === "compatibility" ||
      this.recovery.compatibilityLocked ||
      !this.running
    ) {
      return;
    }
    void this.activateLiquid();
  };

  private showCanvas(mode: "liquid" | "compatibility") {
    this.options.webglCanvas.dataset.active = String(mode === "liquid");
    this.options.compatibilityCanvas.dataset.active = String(mode === "compatibility");
  }

  private report(
    mode: "liquid" | "compatibility",
    status: PresenceRendererHealth["status"],
    error_code: PresenceRendererHealth["error_code"],
  ) {
    this.options.onHealth({ mode, status, error_code });
  }
}
