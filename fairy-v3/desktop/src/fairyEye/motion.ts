/* Copyright 2026 Chengzhibense. Apache-2.0.
 * Adapted from Fairy-DSH mascot-motion-clock.js and mascot-runtime.js.
 * Modified: typed ESM; one active-time clock also drives lashes and pulse;
 * no plugin globals, timers, or independent CSS animation timelines. */
export function createMotionClock(now: () => number = () => performance.now()) {
  let epoch = now();
  let accumulated = 0;
  let rate = 1;
  let paused = false;
  const elapsed = () => accumulated + (paused ? 0 : Math.max(0, now() - epoch) * rate);
  const commit = () => { accumulated = elapsed(); epoch = now(); };
  return {
    elapsed,
    phase: () => ((elapsed() / 1440) % 1 + 1) % 1,
    setRate(next: number) { commit(); rate = Number.isFinite(next) && next > 0 ? Math.min(3, next) : 1; },
    pause() { if (!paused) { commit(); paused = true; } },
    resume() { if (paused) { epoch = now(); paused = false; } },
    isPaused: () => paused,
  };
}

/** Original cubic-bezier(.72, 0, .28, 1), evaluated against elapsed time. */
export function eyeProgress(phase: number): number {
  const cycle = ((phase % 1) + 1) % 1;
  const x = cycle <= .5 ? cycle * 2 : 2 - cycle * 2;
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  let low = 0, high = 1;
  for (let i = 0; i < 12; i++) {
    const t = (low + high) / 2;
    const value = 3 * (1-t) * (1-t) * t * .72 + 3 * (1-t) * t * t * .28 + t*t*t;
    if (value < x) low = t; else high = t;
  }
  const t = (low + high) / 2;
  return 3 * (1-t) * t * t + t*t*t;
}

export function eyeMotionAt(elapsedMs: number) {
  const ms = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  const scale = (from: number, to: number, lead: number) => from + (to-from) * eyeProgress((ms+lead)/1440);
  const progress = eyeProgress(ms / 1440);
  const pulsePhase = (ms % 4000) / 4000;
  // Retain the original outward wave and long quiet interval, on our one clock.
  const wave = Math.min(1, pulsePhase / .36);
  const stops = [[0,0], [.05,.72], [.12,.52], [.20,.19], [.27,0], [1,0]];
  let pulseOpacity = 0;
  for (let i = 1; i < stops.length; i++) {
    if (pulsePhase <= stops[i][0]) {
      const [x0,y0] = stops[i-1], [x1,y1] = stops[i];
      pulseOpacity = y0 + (y1-y0) * (pulsePhase-x0)/(x1-x0);
      break;
    }
  }
  return {
    sclera: scale(.985,.91,0), layerThree: scale(1,.90,45),
    layerTwo: scale(1,.87,90), layerOne: scale(1,.85,180),
    lashAngle: (ms % 15000) / 15000 * 360,
    thinkingY: 14.5 + progress, thinkingScaleY: .55 + .45*progress,
    comfortingY: -4 + 8*progress,
    pulseScale: .98 + 1.8 * (1 - Math.pow(1-wave, 2)), pulseOpacity,
  };
}
