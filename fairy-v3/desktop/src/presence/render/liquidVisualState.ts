import type { FairyVisualState } from "./CompatibilityFairyCanvas";
import type { PresenceRenderSnapshot } from "./presenceRenderer";
import { visualStateForSnapshot } from "./presenceRenderer";

export interface LiquidVisualStyle {
  accent: readonly [number, number, number];
  energy: number;
  particle_count: number;
  particle_seed: number;
  pulse_speed: number;
}

export interface LiquidVisualTransitionSample extends LiquidVisualStyle {
  progress: number;
}

const STYLES: Readonly<Record<FairyVisualState, LiquidVisualStyle>> = Object.freeze({
  booting: style([0.54, 0.82, 0.98], 0.82, 16, 11, 1.4),
  idle: style([0.05, 0.38, 0.95], 0.28, 10, 23, 0.42),
  hover: style([0.03, 0.42, 1.0], 0.48, 12, 29, 0.62),
  listening: style([0.4, 0.85, 0.76], 0.64, 13, 31, 0.82),
  analyzing: style([0.4, 0.72, 0.98], 0.76, 15, 37, 1.12),
  tool: style([0.94, 0.68, 0.25], 0.88, 16, 41, 1.24),
  streaming: style([0.34, 0.84, 0.76], 0.82, 17, 43, 1.38),
  speaking: style([0.46, 0.9, 0.78], 0.9, 18, 47, 1.08),
  awaiting_confirmation: style([0.94, 0.66, 0.22], 0.72, 14, 53, 0.72),
  ready: style([0.38, 0.82, 0.64], 0.5, 12, 59, 0.54),
  error: style([0.95, 0.4, 0.35], 0.84, 12, 61, 0.92),
  sleeping: style([0.5, 0.58, 0.64], 0.12, 8, 67, 0.16),
  dragging: style([0.58, 0.76, 0.92], 0.68, 14, 71, 1.0),
});

export function liquidVisualStyleForSnapshot(
  snapshot: PresenceRenderSnapshot,
): LiquidVisualStyle {
  return STYLES[visualStateForSnapshot(snapshot)];
}

export class LiquidVisualTransition {
  private state: FairyVisualState;
  private from: LiquidVisualStyle;
  private target: LiquidVisualStyle;
  private startedAt: number;
  private durationMs = 0;
  private current: LiquidVisualTransitionSample;

  constructor(
    initialState: FairyVisualState,
    initialStyle: LiquidVisualStyle,
    startedAt = 0,
  ) {
    this.state = initialState;
    this.from = initialStyle;
    this.target = initialStyle;
    this.startedAt = startedAt;
    this.current = { ...initialStyle, progress: 1 };
  }

  setTarget(
    state: FairyVisualState,
    style: LiquidVisualStyle,
    now: number,
    reducedMotion: boolean,
  ): void {
    if (state === this.state) return;
    const current = this.sample(now);
    const previous = this.state;
    this.state = state;
    this.from = current;
    this.target = style;
    this.startedAt = now;
    this.durationMs = liquidVisualTransitionDuration(
      previous,
      state,
      reducedMotion,
    );
    this.current = { ...current, progress: 0 };
  }

  sample(now: number): LiquidVisualTransitionSample {
    const rawProgress = this.durationMs <= 0
      ? 1
      : clamp((now - this.startedAt) / this.durationMs, 0, 1);
    const progress = easeInOutCubic(rawProgress);
    this.current = {
      accent: [
        lerp(this.from.accent[0], this.target.accent[0], progress),
        lerp(this.from.accent[1], this.target.accent[1], progress),
        lerp(this.from.accent[2], this.target.accent[2], progress),
      ],
      energy: lerp(this.from.energy, this.target.energy, progress),
      particle_count: lerp(
        this.from.particle_count,
        this.target.particle_count,
        progress,
      ),
      particle_seed: rawProgress < 1
        ? this.from.particle_seed
        : this.target.particle_seed,
      pulse_speed: lerp(this.from.pulse_speed, this.target.pulse_speed, progress),
      progress,
    };
    return this.current;
  }
}

export function liquidVisualTransitionDuration(
  previous: FairyVisualState,
  next: FairyVisualState,
  reducedMotion: boolean,
): number {
  if (reducedMotion) return 80;
  if (next === "error" || next === "awaiting_confirmation") return 160;
  if (previous === "dragging" || next === "dragging") return 180;
  if (next === "streaming" || next === "speaking") return 240;
  if (next === "sleeping" || next === "idle") return 320;
  return 280;
}

function style(
  accent: readonly [number, number, number],
  energy: number,
  particle_count: number,
  particle_seed: number,
  pulse_speed: number,
): LiquidVisualStyle {
  return Object.freeze({ accent, energy, particle_count, particle_seed, pulse_speed });
}

function easeInOutCubic(value: number): number {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - ((-2 * value + 2) ** 3) / 2;
}

function lerp(start: number, end: number, progress: number): number {
  return start + (end - start) * progress;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
