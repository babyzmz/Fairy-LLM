# Privacy-Safe Liquid Glass Optics Design

**Status:** Superseded on 2026-07-17 by the dual-mode contract in ADR 0016

**Decision:** Route A remains the default `standard` mode; an explicit
`enhanced` mode adds bounded-sampling GPU-only Windows live refraction

**Scope:** Fairy V3 `pet-render` and its Canvas compatibility renderer

## Context

The existing WebGL material has a continuous SDF silhouette, Fresnel terms, and
three nominal color samples. It does not produce an obvious lens because all
three channels sample a low-contrast environment function in local window
coordinates. The result moves with the pet, has little spatial detail to bend,
and makes the channel offsets visually indistinguishable.

The FreeFrontend examples demonstrate convincing displacement by sampling a
known background texture or by applying an SVG displacement map to content
inside the same document. Apple's Liquid Glass presentation treats lensing,
material thickness, adaptive contrast, edge highlights, and restrained
dispersion as one coherent material response. A transparent WebView cannot
read or displace arbitrary pixels belonging to windows behind it without a
desktop-capture or native-backdrop path.

Fairy adopts those optical principles without copying their implementations.
Standard mode creates a clearly visible procedural lens without capture.
Enhanced mode uses WGC to acquire the active monitor frame. The production
shader may sample only the bounded `pet-render` rectangle plus a
32-logical-pixel optical guard band; the texture remains GPU-local and is never
cached, analyzed, logged, uploaded, read back to the CPU, or sent through
WebView IPC.

References:

- <https://freefrontend.com/css-liquid-glass/>
- <https://github.com/carolhsiaoo/awesome-liquid-glass>
- <https://github.com/Muggleee/liquid-glass>
- <https://github.com/rdev/liquid-glass-react>
- <https://github.com/shuding/liquid-glass>
- <https://developer.apple.com/videos/play/wwdc2025/219/>
- <https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositor.createhostbackdropbrush>

The production material is a clean-room implementation. It does not copy or
ship any of the referenced repositories and introduces no new runtime package.
The WebGL2/SDF project is the closest architectural reference; the React and
SVG projects are used only to validate the edge-displacement profile and the
rule that chromatic separation must leave the center clean.

## Decision

The standard renderer uses a screen-locked procedural environment field, an
SDF-derived thickness profile, surface normals, bounded spectral offsets,
directional caustics, Fresnel reflection, and premultiplied-alpha composition.
The enhanced renderer uses the same shape and optical limits while sampling a
WGC texture directly in D3D11 and presenting it through DirectComposition.

Standard mode is an optical simulation and must not be described as physically
refracting arbitrary desktop applications. Enhanced mode performs real screen
sampling with a bounded shader address region, but it is not Apple's private
material implementation or physical ray tracing.

## Rendering Architecture

### Stable screen-space environment

`ThreeLiquidRenderer` supplies the render-frame origin and monitor work area as
uniforms. The fragment shader converts each local fragment to a normalized
monitor coordinate, with the vertical orientation corrected for Windows screen
coordinates. The procedural environment is evaluated in this stable coordinate
space, so its detail slides through the lens as Fairy moves instead of being
painted onto the pet.

The environment combines four restrained signals:

1. broad neutral illumination for material readability;
2. fine diagonal luminance bands that expose displacement;
3. a low-amplitude cell field that prevents a uniform gray result; and
4. a directional key reflection whose position follows the monitor, not the
   render window.

The field remains neutral and low saturation. Fairy blue is reserved for the AI
core and state energy, not for the entire glass body.

### Thickness and normals

The signed distance field remains the single source of geometry. Interior
distance is converted to a rounded thickness profile that is zero at the edge,
rises quickly through the rim, and flattens through the center. Screen-space
derivatives of that profile produce a lens normal. A bounded low-frequency
surface perturbation may affect the normal, but never the silhouette or hit
area.

Thickness controls refraction distance, internal scattering, center
transmission, and caustic strength. The shape must remain readable over light,
dark, and textured desktops without becoming an opaque white or blue object.

### Refraction and dispersion

The shader evaluates the procedural environment at one base refracted
coordinate and two bounded spectral offsets. Red bends slightly farther than
green and blue slightly less. Dispersion is concentrated at oblique rim angles
and fades through the center, producing a narrow warm/cool split instead of a
full-object rainbow.

At 100 percent scale, the visible spectral separation is targeted at 1.25 to
2.25 logical pixels on the strongest rim and remains below 3 logical pixels at
all supported scales. Reduced Motion does not disable the static optical
response because it contains no movement.

### Caustics, reflection, and alpha

An inward caustic follows the refracted normal and key-light direction. It is
paired with a weak opposite-side attenuation so the lens reads as volume rather
than a uniformly bright outline. Two compact specular lobes and a Fresnel rim
provide the primary glass identity.

The target alpha ranges are:

- transmitted center: 0.05 to 0.11;
- ordinary rim: 0.20 to 0.31;
- brief specular or caustic peak: at most 0.42; and
- complete composed output, including the AI core: at most 0.78.

The shader continues to output premultiplied color with `NoBlending`, matching
the transparent WebView composition path. Pixels outside the SDF and particle
field remain fully transparent and mouse-pass-through.

### Internal Fairy core and states

The AI core, particles, and state accent are composited after the neutral glass
response. Their coordinates are refracted by a smaller version of the lens
offset so the inner light appears suspended inside the material. Work states
may alter energy, pulse rate, accent, and particle count; they must not change
the material into unrelated colored themes.

Idle uses the quietest caustic and dispersion values. Hover and interaction
increase lens readability slightly. Speaking modulates only internal light and
surface micro-response. Approval, ready, and error states retain their semantic
amber, mint, and coral accents inside the core.

### Canvas compatibility

Canvas 2D cannot reproduce spectral sampling, but compatibility mode must retain
the same visual identity. It will use layered radial gradients, an asymmetric
inner highlight, a restrained warm/cool pair of rim arcs, and the existing
state-driven core. It must not add raster assets or claim refraction parity with
WebGL2.

## Data Flow and Boundaries

`PresenceInteractionSnapshot` remains the authoritative source for placement.
No new Core RPC or database entity is introduced. Renderer uniforms are derived
locally from the latest placement and visual snapshot. Enhanced mode sends only
bounded presentation state and lifecycle commands; the acquired monitor texture
stays inside the native GPU pipeline and frame pixels never cross the
Rust/WebView boundary.

The render surface remains unable to call Core, Voice, settings mutation, file
operations, execution, or approvals. No optical data enters the Ledger. Optical
runtime state is limited to GPU-local capture textures, shader resources, and
uniform data derived from approved window placement.

## Failure Handling

Shader compilation, context loss, or repeated renderer failure continues to
select Canvas compatibility mode. Invalid monitor dimensions use a deterministic
unit work area and report degraded renderer health instead of producing NaN
coordinates. Monitor changes update the uniforms on the next placement snapshot
without rebuilding the material.

No failure path may silently enable enhanced capture, expand its region, expose
pixels to the CPU, exit Fairy, or interfere with the main workspace. Native,
WebGL, and compatibility failure budgets remain independent.

## Verification

### Unit and shader contract

- Verify monitor-coordinate normalization for positive and negative origins,
  mixed DPI, and both expansion directions.
- Verify all optical constants are bounded and scaling preserves logical-pixel
  dispersion limits.
- Compile the shader in the WebGL2 probe.
- Assert the material contains thickness, spectral, caustic, and screen-space
  environment stages.
- Assert standard mode starts with no capture and enhanced mode contains no CPU
  readback, encoded frame, pixel IPC, or persistence contract.
- Compile both the WebGL2 shader and the production HLSL shader model.

### Pixel and visual evidence

- Render idle, hover, interactive, speaking, approval, ready, and error states
  against light, dark, checker, high-frequency, and complex-color fixtures.
- Pixel checks must prove transparent exterior pixels, a lower-alpha center,
  a higher-alpha rim, nonzero warm/cool rim separation, and directional caustic
  contrast.
- Moving the render-frame origin while holding local geometry fixed must change
  the internal environment phase, proving that the environment is screen
  locked.
- Review screenshots at 100, 125, 150, and 200 percent scaling with no black
  rectangle, clipped bridge, seam, or incoherent color band.

### Runtime and release gates

- Run the native high-performance NVIDIA, power-saving AMD, and GPU-disabled
  compatibility matrix.
- Run one 30-minute soak on NVIDIA and one on AMD using the final shader. GPU
  frame p95 must remain below 8 ms, average CPU below one core's 5 percent, and
  steady private-memory growth below 10 MiB.
- Re-run Vitest, production Playwright, Rust, TypeScript, the complete V3 gate,
  release bundle construction, media hashes, 0.1.0-to-0.2.0 upgrade, installed
  native smoke, uninstall, and residue checks.

## Acceptance Criteria

1. The lens and chromatic split are visible without relying on Fairy blue glow.
2. The material reads as transparent volume on light, dark, and complex
   backgrounds while remaining restrained at idle.
3. Environment detail remains screen locked when the pet moves.
4. Standard mode performs no capture; enhanced mode requires an explicit saved
   preference, keeps the acquired monitor texture GPU-only, and hard-clamps
   shader sampling to the Pet surface and optical guard band.
5. Reduced Motion, renderer fallback, pass-through, focus safety, placement,
   performance, installer, and full Fairy V3 gates continue to pass.

## Non-Goals

- Sampling outside the bounded enhanced Pet surface and optical guard band, or
  retaining, analyzing, or uploading any acquired pixels.
- Capturing from `pet-input`, bypassing protected-content behavior, or adding a
  background capture path that runs without explicit enhanced-mode consent.
- Adding Unity, WebGPU, Win2D displacement, or `HostBackdropBrush` in this
  milestone.
- Recreating Apple's private material implementation or claiming pixel-identical
  Apple Liquid Glass behavior.
- Adding user-facing optical tuning controls before the fixed material is
  visually and operationally certified.
