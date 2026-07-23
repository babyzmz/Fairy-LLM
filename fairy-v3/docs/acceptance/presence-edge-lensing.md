# Presence Edge Lensing Acceptance

Recorded on 2026-07-23 for the Apple-aligned edge-lensing refinement of the
native DDA Liquid Glass renderer.

## Observable behavior

- The central half-radius preserves the captured desktop structure without
  magnification, convergence, a gray fill, or a second readable copy.
- Background features begin to bend gently after the half-radius and compress
  most strongly through the outer shoulder of the glass.
- The final rim reads as a thick transparent boundary. Geometric displacement,
  rather than chromatic fringe or opaque highlight, supplies the primary lens
  cue.
- Atmospheric rings, particles, and the breathing beacon remain in the final
  identity pass and keep their original geometry.
- With placement, drag, and shape morph held constant, idle breathing and
  semantic-state animation cannot change any desktop sample coordinate.
  State motion belongs to the material and identity passes so fine text behind
  a stationary edge does not shimmer.
- Core, transitional droplets, and input capsules use the same normalized
  monotonic field. Thin shapes scale displacement with their optical radius.

## Optical invariants

- Core displacement is zero through `0.50R` and remains below `0.5px` at
  `0.60R`.
- Refraction remains below `2px` at `0.70R`, then reaches at least `11.5px` by
  `0.94R`; the full core maximum is `12.5px`.
- The radial source-coordinate Jacobian remains above `0.25` for both the
  72-pixel core radius and the 24-pixel thin-shape radius. The mapping must
  never fold or repeat source pixels.
- Chromatic displacement is confined to the outer ten percent and remains at
  or below `1.1px` before surface scaling.
- Each output pixel uses one primary desktop coordinate. No stacked backdrop
  copies, concentric bands, center scaling, or broad blur are permitted.
- `elapsed_seconds`, `state_elapsed_seconds`, `state_pulse`, voice level, and
  semantic activation must not feed the desktop UV path. Only stable geometry,
  explicit shape morph, and drag/release deformation may move that path.

## Automated acceptance

- Rust shader-contract tests assert the production constants and curve
  definition.
- CPU mirror tests sample the profile densely, verify identity and rim
  thresholds, and enforce the minimum normalized source-coordinate Jacobian.
- Shader dependency tests reject time-varying state from both the core SDF and
  the desktop-sampling function.
- Existing HLSL compilation, DDA source, foreground identity, and no-concentric-
  lens tests remain mandatory.

## Native acceptance

The following checks require the real Tauri/D3D runtime:

1. Place Fairy over fine horizontal and vertical text. The center remains
   readable and stationary while only the outer shoulder bends the glyphs.
2. Place Fairy over a checkerboard. Grid lines curve continuously around the
   rim without duplicated cells, radial seams, or rings.
3. Keep Fairy stationary over moving video. Lensing updates within one display
   frame and does not depend on dragging.
4. Drag horizontally and vertically, then release. No stale edge, white line,
   black frame, or one-frame style switch is allowed.
5. Repeat with the core on both the left and right side of its render surface,
   on the expanded input capsule, and at supported DPI scales.
6. Keep a high-contrast checkerboard or fine text static for five seconds in
   idle, hover, thinking, and speaking states. The sampled background edge must
   remain spatially fixed; only the foreground identity/material light may
   animate.

Unit tests cannot substitute for these native pixel and presentation checks.
