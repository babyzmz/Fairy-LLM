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

const STYLES: Readonly<Record<FairyVisualState, LiquidVisualStyle>> = Object.freeze({
  booting: style([0.54, 0.82, 0.98], 0.82, 16, 11, 1.4),
  idle: style([0.38, 0.75, 0.9], 0.28, 10, 23, 0.42),
  hover: style([0.48, 0.82, 0.96], 0.48, 12, 29, 0.62),
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

function style(
  accent: readonly [number, number, number],
  energy: number,
  particle_count: number,
  particle_seed: number,
  pulse_speed: number,
): LiquidVisualStyle {
  return Object.freeze({ accent, energy, particle_count, particle_seed, pulse_speed });
}
