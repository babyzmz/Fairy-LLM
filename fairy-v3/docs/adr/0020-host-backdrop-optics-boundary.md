# ADR 0020: Host Backdrop Optics Boundary

## Status

Accepted on 2026-07-22. This supersedes the production optical-source portions
of ADR 0016. ADR 0016 remains historical evidence for the earlier capture
experiments and the two-window Presence architecture.

## Decision

Fairy's production Windows Presence renderer uses
`Compositor.CreateHostBackdropBrush` as an identity sample of the composed
desktop behind the native window. It does not claim that HostBackdrop performs
pixel displacement or continuous radial refraction.

The foreground D3D11 surface is limited to:

- the edge material mask;
- Fresnel-like rim lighting, one narrow caustic, and directional highlights;
- the two Fairy atmosphere rings and breathing beacon;
- state and interaction motion that does not resample desktop pixels.

The production path does not start WGC or DXGI Desktop Duplication and does not
bind a desktop texture to the Presence pixel shader.

## Verified API Boundary

| Capability | Result | Evidence |
| --- | --- | --- |
| Sample the desktop before Fairy's window is drawn | Supported | `CreateHostBackdropBrush` returns a brush that samples behind the visual before the host window is drawn. |
| Read HostBackdrop pixels in application code or a D3D shader | Unsupported | Microsoft documents that applications cannot read the brush's pixel data back. |
| Apply a single 2D affine transform in a Composition effect graph | Supported | The Windows Composition effect list includes 2D affine transform. This is a global transform, not a radial displacement field. |
| Apply a per-pixel displacement map to HostBackdrop | Unsupported | Win2D marks `DisplacementMapEffect` as `NoComposition` and explicitly states it is not supported by Windows Composition. |
| Apply a custom pixel shader directly to HostBackdrop | Unsupported | Composition only compiles its supported `IGraphicsEffect` set; custom pixel-shader effects are not a supported Composition source path. |
| Refract a separately captured monitor texture in D3D11 | Technically possible, rejected for production | The capture contains Fairy unless the window is excluded or the source is reconstructed. Sampling outside the silhouette duplicates unrelated content; excluding the window conflicts with recording and remote-desktop visibility. |

The supported 2D affine transform cannot reproduce Apple's edge-responsive
lensing. Masking or blending a scaled copy with the identity backdrop creates
two source coordinates in the transition band and therefore visible ghosting.
It is not used as a substitute for per-pixel refraction.

## Rejected Production Path

The July 22 hybrid implementation combined an identity HostBackdrop center with
a DXGI monitor texture sampled outside Fairy's silhouette. This avoided direct
self-capture, but it copied neighboring text and imagery inward. The result was
coordinate convergence, repeated details, concentric boundaries, and a dark
convex-lens appearance. Those artifacts are structural, not parameter-tuning
problems, so the path is removed rather than weakened.

## Product Consequences

- Native health reports `host_backdrop`, never a monitor-edge refraction source.
- Settings describe the backend as a native Host Backdrop material with no
  pixel displacement.
- The clear center shows the real composed desktop. Normal mode adds no center
  material veil; Reduced Transparency may add a bounded accessibility fill.
- Only one continuous edge material profile is drawn. It is a material cue, not
  claimed refraction or chromatic dispersion of the desktop.
- True desktop refraction remains a future capability gate. It requires either
  a new supported Windows Composition displacement API or an explicitly
  consented capture mode with separately accepted visibility and recursion
  tradeoffs.

## Sources

- Apple, `Meet Liquid Glass`, WWDC25:
  https://developer.apple.com/videos/play/wwdc2025/219/
- Microsoft, `Compositor.CreateHostBackdropBrush`:
  https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositor.createhostbackdropbrush
- Microsoft, `Composition effects`:
  https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-effects
- Microsoft Win2D, `DisplacementMapEffect`:
  https://microsoft.github.io/Win2D/WinUI3/html/T_Microsoft_Graphics_Canvas_Effects_DisplacementMapEffect.htm
