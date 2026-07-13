import { drawFairyFrame } from "./CompatibilityFairyCanvas";
import {
  rendererFrameInterval,
  type PresenceRenderer,
  type PresenceRenderSnapshot,
  visualStateForSnapshot,
} from "./presenceRenderer";
import { RendererFrameLoop } from "./RendererFrameLoop";
import {
  readPerformanceHeapBytes,
  RendererPerformanceSampler,
  writePerformanceDataset,
} from "./RendererPerformanceSampler";

export class CanvasCompatibilityRenderer implements PresenceRenderer {
  private readonly context: CanvasRenderingContext2D;
  private snapshot: PresenceRenderSnapshot;
  private readonly loop: RendererFrameLoop;
  private disposed = false;
  private width = 1;
  private height = 1;
  private dpr = 1;
  private readonly performanceSampler = new RendererPerformanceSampler();
  private renderedFrames = 0;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    initialSnapshot: PresenceRenderSnapshot,
  ) {
    const context = canvas.getContext("2d", { alpha: true });
    if (context === null) throw new Error("CANVAS2D_UNAVAILABLE");
    this.context = context;
    this.snapshot = initialSnapshot;
    this.loop = new RendererFrameLoop(
      (now) => this.drawFrame(now),
      () => rendererFrameInterval(this.snapshot),
    );
  }

  start(): void {
    if (this.disposed) return;
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
  }

  suspend(): void {
    this.stop();
  }

  resize(width: number, height: number, devicePixelRatio: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    this.loop.drawImmediately();
  }

  setSnapshot(snapshot: PresenceRenderSnapshot): void {
    this.snapshot = snapshot;
    this.loop.refresh();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.loop.dispose();
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  private drawFrame(now: number) {
    const startedAt = performance.now();
    const gaze = this.snapshot.interaction?.cursor.direction ?? { x: 0, y: 0 };
    drawFairyFrame(
      this.context,
      this.canvas,
      visualStateForSnapshot(this.snapshot),
      gaze,
      this.snapshot.reduced_motion ? 0 : now / 1_000,
      {
        opacity: this.snapshot.opacity,
        particles: this.snapshot.particles_enabled,
        sizeScale: this.snapshot.size_scale,
      },
    );
    this.performanceSampler.recordCpuFrame(performance.now() - startedAt);
    this.renderedFrames += 1;
    if (this.renderedFrames % 60 === 0) {
      this.performanceSampler.recordHeap(readPerformanceHeapBytes());
      writePerformanceDataset(this.canvas, this.performanceSampler.snapshot());
    }
    this.canvas.dataset.rendered = "true";
  }
}
