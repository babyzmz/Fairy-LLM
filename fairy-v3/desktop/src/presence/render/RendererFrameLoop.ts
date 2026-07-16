export interface AnimationFrameScheduler {
  now(): number;
  request(callback: FrameRequestCallback): number;
  cancel(frame: number): void;
}

const browserScheduler: AnimationFrameScheduler = {
  now: () => performance.now(),
  request: (callback) => requestAnimationFrame(callback),
  cancel: (frame) => cancelAnimationFrame(frame),
};

export class RendererFrameLoop {
  private frame: number | null = null;
  private active = false;
  private disposed = false;
  private lastDrawAt = 0;
  private nextDrawAt = 0;
  private scheduledInterval = Number.POSITIVE_INFINITY;

  constructor(
    private readonly drawFrame: (now: number) => void,
    private readonly frameInterval: () => number,
    private readonly scheduler: AnimationFrameScheduler = browserScheduler,
  ) {}

  get running(): boolean {
    return this.active && !this.disposed;
  }

  start(): void {
    if (this.disposed || this.active) return;
    this.active = true;
    this.drawNow();
    this.schedule();
  }

  stop(): void {
    this.active = false;
    this.cancelScheduledFrame();
  }

  refresh(): void {
    if (!this.running) return;
    const interval = this.frameInterval();
    if (!Number.isFinite(interval)) {
      this.cancelScheduledFrame();
      this.drawNow();
      return;
    }
    if (interval !== this.scheduledInterval) {
      this.scheduledInterval = interval;
      this.nextDrawAt = this.scheduler.now() + interval;
    }
    this.schedule();
  }

  drawImmediately(): void {
    if (this.running) this.drawNow();
  }

  dispose(): void {
    if (this.disposed) return;
    this.stop();
    this.disposed = true;
  }

  private readonly tick = (now: number) => {
    this.frame = null;
    if (!this.running) return;
    const interval = this.frameInterval();
    if (interval !== this.scheduledInterval) {
      this.scheduledInterval = interval;
      this.nextDrawAt = Math.min(this.nextDrawAt, this.lastDrawAt + interval);
    }
    if (this.lastDrawAt === 0 || now >= this.nextDrawAt - 0.001) {
      this.drawFrame(now);
      this.lastDrawAt = now;
      if (Number.isFinite(interval)) {
        const overdue = Math.max(0, now - this.nextDrawAt);
        const elapsedIntervals = Math.floor(overdue / interval) + 1;
        this.nextDrawAt += elapsedIntervals * interval;
      }
    }
    this.schedule();
  };

  private drawNow() {
    const now = this.scheduler.now();
    this.drawFrame(now);
    this.lastDrawAt = now;
    this.scheduledInterval = this.frameInterval();
    this.nextDrawAt = Number.isFinite(this.scheduledInterval)
      ? now + this.scheduledInterval
      : Number.POSITIVE_INFINITY;
  }

  private schedule() {
    if (
      !this.running ||
      this.frame !== null ||
      !Number.isFinite(this.frameInterval())
    ) {
      return;
    }
    this.frame = this.scheduler.request(this.tick);
  }

  private cancelScheduledFrame() {
    if (this.frame !== null) this.scheduler.cancel(this.frame);
    this.frame = null;
  }
}
