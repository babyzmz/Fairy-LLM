/**
 * Adapted from Fairy-DSH mascot-geometry.js (Apache-2.0).
 * Copyright 2026 Chengzhibense. See licenses/Fairy-DSH-NOTICE.txt.
 * Modification: TypeScript/ES modules; numeric validation remains explicit.
 */
export const geometry = Object.freeze({
  outerDiscRadius: 68, outerStrokeWidth: 1.2, outerHaloRadius: 79,
  scleraRadius: 48, scleraContactStrokeWidth: 0.8, scleraHaloRadius: 56,
  pupilRadius: 16, highlightCenter: Object.freeze({ x: 98, y: 100.5 }),
  highlightRadius: 11, highlightHaloRadius: 18,
});
const positive = (name: string, value: number): number => {
  if (!Number.isFinite(value) || value <= 0) throw new Error(`Invalid Fairy geometry: ${name}`);
  return value;
};
for (const [name, value] of Object.entries(geometry)) {
  if (typeof value === "number") positive(name, value);
}
export const outerVisibleEdge = geometry.outerDiscRadius + geometry.outerStrokeWidth / 2;
export const scleraVisibleEdge = geometry.scleraRadius + geometry.scleraContactStrokeWidth / 2;
export const outerHaloPeak = outerVisibleEdge / geometry.outerHaloRadius;
export const scleraHaloPeak = scleraVisibleEdge / geometry.scleraHaloRadius;
export const highlightHaloPeak = geometry.highlightRadius / geometry.highlightHaloRadius;
for (const value of [outerHaloPeak, scleraHaloPeak, highlightHaloPeak]) {
  if (!(value > 0 && value < 1)) throw new Error("Invalid Fairy halo geometry");
}
export const formatRatio = (value: number): string => value.toFixed(3).replace(/^0/, "");
