import type { LiquidShapeTarget } from "./liquidGlassMaterial";

const MAXIMUM_OVERSHOOT = 0.04;
const SPRING_FREQUENCY_HZ = 5.5;
const SPRING_DAMPING_RATIO = 0.82;
const MAXIMUM_STEP_SECONDS = 1 / 120;
const MAXIMUM_FRAME_DELTA_MS = 50;

export interface LiquidMotionSample extends LiquidShapeTarget {
  speech_level: number;
  return_bounce: number;
}

interface ReturnEnvelope {
  startedAt: number;
  origin: LiquidShapeTarget;
  reducedMotion: boolean;
}

class BoundedSpring {
  private velocity = 0;
  private target: number;

  constructor(private value: number) {
    this.target = value;
  }

  setTarget(target: number, immediate: boolean) {
    this.target = clampUnit(target);
    if (!immediate) return;
    this.value = this.target;
    this.velocity = 0;
  }

  step(deltaSeconds: number) {
    if (deltaSeconds <= 0) return;
    const angularFrequency = Math.PI * 2 * SPRING_FREQUENCY_HZ;
    const stepCount = Math.max(1, Math.ceil(deltaSeconds / MAXIMUM_STEP_SECONDS));
    const step = deltaSeconds / stepCount;
    for (let index = 0; index < stepCount; index += 1) {
      const acceleration =
        angularFrequency * angularFrequency * (this.target - this.value) -
        2 * SPRING_DAMPING_RATIO * angularFrequency * this.velocity;
      this.velocity += acceleration * step;
      this.value += this.velocity * step;
      const bounded = Math.max(
        -MAXIMUM_OVERSHOOT,
        Math.min(1 + MAXIMUM_OVERSHOOT, this.value),
      );
      if (bounded !== this.value) this.velocity = 0;
      this.value = bounded;
    }
    if (Math.abs(this.target - this.value) < 0.0001 && Math.abs(this.velocity) < 0.001) {
      this.value = this.target;
      this.velocity = 0;
    }
  }

  sample(): number {
    return this.value;
  }
}

export class SpeechLevelEnvelope {
  private value = 0;
  private target = 0;

  setTarget(target: number, immediate = false) {
    this.target = clampUnit(target);
    if (immediate) this.value = this.target;
  }

  step(deltaMs: number): number {
    const duration = this.target > this.value ? 60 : 180;
    const blend = 1 - Math.exp(-Math.max(0, deltaMs) / duration);
    this.value += (this.target - this.value) * blend;
    if (Math.abs(this.target - this.value) < 0.0001) this.value = this.target;
    return this.value;
  }

  sample(): number {
    return this.value;
  }
}

export class LiquidMotionController {
  private readonly droplet: BoundedSpring;
  private readonly bridge: BoundedSpring;
  private readonly capsule: BoundedSpring;
  private readonly speech = new SpeechLevelEnvelope();
  private lastSampleAt: number | null = null;
  private returning: ReturnEnvelope | null = null;

  constructor(initialShape: LiquidShapeTarget, initialSpeechLevel = 0) {
    this.droplet = new BoundedSpring(initialShape.droplet);
    this.bridge = new BoundedSpring(initialShape.bridge);
    this.capsule = new BoundedSpring(initialShape.capsule);
    this.speech.setTarget(initialSpeechLevel, true);
  }

  setShapeTarget(target: LiquidShapeTarget, immediate = false) {
    this.droplet.setTarget(target.droplet, immediate);
    this.bridge.setTarget(target.bridge, immediate);
    this.capsule.setTarget(target.capsule, immediate);
  }

  setSpeechTarget(target: number, immediate = false) {
    this.speech.setTarget(target, immediate);
  }

  beginReturn(now: number, reducedMotion = false) {
    if (reducedMotion) {
      this.returning = null;
      this.setShapeTarget({ droplet: 0, bridge: 0, capsule: 0 }, true);
      return;
    }
    this.returning = {
      startedAt: now,
      origin: {
        droplet: clampUnit(this.droplet.sample()),
        bridge: clampUnit(this.bridge.sample()),
        capsule: clampUnit(this.capsule.sample()),
      },
      reducedMotion: false,
    };
    this.setShapeTarget({ droplet: 0, bridge: 0, capsule: 0 });
  }

  cancelReturn(now: number) {
    const returning = this.returning;
    if (returning !== null) {
      const shape = returnShapeAt(returning, now);
      this.setShapeTarget(shape, true);
    }
    this.returning = null;
  }

  resetClock() {
    this.lastSampleAt = null;
  }

  sample(now: number): LiquidMotionSample {
    const deltaMs = this.lastSampleAt === null
      ? 0
      : Math.min(MAXIMUM_FRAME_DELTA_MS, Math.max(0, now - this.lastSampleAt));
    this.lastSampleAt = now;
    const returning = this.returning;
    if (returning !== null) {
      const elapsed = Math.max(0, now - returning.startedAt);
      const contractionDuration = returning.reducedMotion ? 160 : 220;
      const shape = returnShapeAt(returning, now);
      const bounceElapsed = elapsed - contractionDuration;
      const completed = elapsed >= contractionDuration + 180;
      const returnBounce = completed || returning.reducedMotion || bounceElapsed <= 0
        ? 0
        : Math.sin(Math.PI * Math.min(1, bounceElapsed / 180)) * MAXIMUM_OVERSHOOT;
      if (completed) {
        this.returning = null;
        this.setShapeTarget({ droplet: 0, bridge: 0, capsule: 0 }, true);
      }
      return {
        ...shape,
        speech_level: this.speech.step(deltaMs),
        return_bounce: returnBounce,
      };
    }
    const deltaSeconds = deltaMs / 1_000;
    this.droplet.step(deltaSeconds);
    this.bridge.step(deltaSeconds);
    this.capsule.step(deltaSeconds);
    return {
      droplet: clampUnit(this.droplet.sample()),
      bridge: clampUnit(this.bridge.sample()),
      capsule: clampUnit(this.capsule.sample()),
      speech_level: this.speech.step(deltaMs),
      return_bounce: 0,
    };
  }
}

function clampUnit(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

function easeInOutCubic(value: number): number {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - Math.pow(-2 * value + 2, 3) / 2;
}

function returnShapeAt(envelope: ReturnEnvelope, now: number): LiquidShapeTarget {
  const elapsed = Math.max(0, now - envelope.startedAt);
  const duration = envelope.reducedMotion ? 160 : 220;
  const progress = easeInOutCubic(Math.min(1, elapsed / duration));
  const scale = 1 - progress;
  const bridgeTransition = progress < 1 && envelope.origin.bridge < 0.01
    ? Math.sin(Math.PI * progress) * envelope.origin.capsule
    : 0;
  return {
    droplet: Math.max(envelope.origin.droplet * scale, bridgeTransition * 0.72),
    bridge: Math.max(envelope.origin.bridge * scale, bridgeTransition),
    capsule: envelope.origin.capsule * scale,
  };
}
