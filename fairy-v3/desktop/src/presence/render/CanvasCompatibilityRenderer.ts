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
import { liquidCapsuleGeometryForSnapshot } from "./liquidGeometry";
import {
  LiquidVisualTransition,
  liquidVisualStyleForSnapshot,
} from "./liquidVisualState";

export class CanvasCompatibilityRenderer implements PresenceRenderer {
  private readonly context: CanvasRenderingContext2D;
  private snapshot: PresenceRenderSnapshot;
  private readonly loop: RendererFrameLoop;
  private disposed = false;
  private width = 1;
  private height = 1;
  private dpr = 1;
  private readonly performanceSampler = new RendererPerformanceSampler();
  private readonly visualMotion: LiquidVisualTransition;
  private renderedFrames = 0;
  private hasRendered = false;
  private performanceSamplingComplete = false;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    initialSnapshot: PresenceRenderSnapshot,
  ) {
    const context = canvas.getContext("2d", { alpha: true });
    if (context === null) throw new Error("CANVAS2D_UNAVAILABLE");
    this.context = context;
    this.snapshot = initialSnapshot;
    this.visualMotion = new LiquidVisualTransition(
      visualStateForSnapshot(initialSnapshot),
      liquidVisualStyleForSnapshot(initialSnapshot),
      performance.now(),
    );
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
    this.visualMotion.setTarget(
      visualStateForSnapshot(snapshot),
      liquidVisualStyleForSnapshot(snapshot),
      performance.now(),
      snapshot.reduced_motion,
    );
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
    const visualStyle = this.visualMotion.sample(now);
    const interaction = this.snapshot.interaction;
    const gaze = interaction?.cursor.direction ?? { x: 0, y: 0 };
    const center = interaction === null
      ? { x: 96 * this.dpr, y: 88 * this.dpr }
      : {
          x: interaction.placement.anchor.x - interaction.placement.render_frame.x,
          y: interaction.placement.anchor.y - interaction.placement.render_frame.y,
        };
    drawFairyFrame(
      this.context,
      this.canvas,
      visualStateForSnapshot(this.snapshot),
      gaze,
      this.snapshot.reduced_motion ? 0 : now / 1_000,
      {
        opacity: this.snapshot.opacity,
        reducedTransparency: this.snapshot.reduced_transparency === true,
        increasedContrast: this.snapshot.increased_contrast === true,
        particles: this.snapshot.particles_enabled,
        sizeScale: this.snapshot.size_scale,
        stateStyle: {
          accent: hexStyle(visualStyle.accent),
          secondary: hexStyle(lighten(visualStyle.accent, 0.82)),
          speed: visualStyle.pulse_speed,
          energy: visualStyle.energy,
          particles: Math.round(visualStyle.particle_count),
        },
        center,
        capsuleGeometry: this.snapshot.input_capsule_visible
          ? (() => {
              const capsule = liquidCapsuleGeometryForSnapshot(
                this.snapshot,
                this.width,
                this.height,
                this.dpr,
              );
              return {
                center: {
                  x: capsule.center_x,
                  y: this.canvas.height - capsule.center_y,
                },
                width: capsule.half_width * 2,
              };
            })()
          : null,
      },
    );
    if (!this.performanceSamplingComplete) {
      this.performanceSampler.recordCpuFrame(performance.now() - startedAt);
    }
    this.renderedFrames += 1;
    if (!this.performanceSamplingComplete && this.renderedFrames % 60 === 0) {
      this.performanceSampler.recordHeap(readPerformanceHeapBytes());
      writePerformanceDataset(this.canvas, this.performanceSampler.snapshot());
      this.performanceSamplingComplete = this.performanceSampler.cpuSamplingComplete;
      this.canvas.dataset.timingComplete = String(this.performanceSamplingComplete);
    }
    if (!this.hasRendered) {
      this.hasRendered = true;
      this.canvas.dataset.rendered = "true";
    }
  }
}

function lighten(
  color: readonly [number, number, number],
  amount: number,
): [number, number, number] {
  return [
    color[0] + (1 - color[0]) * amount,
    color[1] + (1 - color[1]) * amount,
    color[2] + (1 - color[2]) * amount,
  ];
}

function hexStyle(color: readonly [number, number, number]): string {
  return `#${color
    .map((value) => Math.round(value * 255).toString(16).padStart(2, "0"))
    .join("")}`;
}
