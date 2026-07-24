# ADR 0021: DDA Liquid Glass Production Optics

## Status

Accepted on 2026-07-24. This supersedes the production optical-source stance of
ADR 0020, which held that the production renderer stays on the identity
`host_backdrop_identity` material and does not start a desktop-capture source.
ADR 0020 remains the authoritative record of the Windows Composition API
boundary analysis and of why the July 22 HostBackdrop+monitor hybrid was
rejected; that analysis is the justification that led to this decision. ADR 0016
remains the historical record of the two-window Presence architecture.

This ADR realizes the approved
`docs/superpowers/specs/2026-07-22-dda-liquid-glass-source-design.md` design and
the `docs/acceptance/presence-edge-lensing.md` optical refinement.

## Decision

Fairy's production Windows Presence renderer uses **DXGI Desktop Duplication
(DDA)** as its live optical source. `DesktopTextureSource` owns one duplication
session for the monitor containing the Fairy core and publishes immutable GPU
frame handles to the D3D11 render thread. The pixel shader samples one desktop
texture through one continuous SDF-derived displacement field: identity through
the inner half-radius, growing smoothly through the middle band, and peaking as a
single thick edge lens at the outer shoulder. A final identity pass draws the
atmosphere rings, particles, and breathing beacon without entering refraction.

`host_backdrop_identity` (`CreateHostBackdropBrush`) is retained as the explicit
identity **fallback**, not the primary path. It is selected only when Desktop
Duplication is unavailable, and it never claims per-pixel refraction.

## Verified API and privacy boundary

The security boundary from ADR 0020 is unchanged and remains executable in the
native shader-contract test
(`presence_native_gpu.rs::native_shader_contract_allows_only_gpu_resident_dda_pixels`):

| Property | Rule | Enforcement |
| --- | --- | --- |
| Optical source | DDA `DesktopTextureSource`; HostBackdrop identity fallback | Contract requires `DesktopTextureSource`, `set_window_excluded_from_dda`, `Texture2D<float4> desktop_texture`, `sample_continuous_liquid_glass`, and retains `CreateHostBackdropBrush()` for fallback |
| Window Graphics Capture | Never used in production | Contract forbids `GraphicsCaptureItem`, `CreateForMonitor`, `NativeCaptureHandler` |
| Composition displacement | Not used | Contract forbids `DisplacementMapEffect` |
| CPU readback | Forbidden | Contract forbids `D3D11_USAGE_STAGING`, `D3D11_MAP_READ`, `frame.buffer(`, `ReadPixels` |
| Pixel IPC to WebView | Forbidden | Contract forbids `postMessage`, `ReadPixels` |
| Rejected July 22 hybrid | Removed, not weakened | Contract forbids `core_warp`, `capsule_warp`, `captured - overlay.rgb`, `sample_desktop_linear`, `previous_overlay`, `sample_refracted_desktop` |

Sampling is hard-clamped to the bounded surface rectangle plus a small optical
guard band. The sampled texture stays GPU-local and ephemeral: it is never
written to disk, Ledger, logs, model context, or telemetry. Diagnostics contain
dimensions, timings, and hashes only.

## Window exclusion and general visibility

Every native Presence HWND is registered with `WCA_EXCLUDED_FROM_DDA` before its
first visible frame, and the exclusion is re-applied after HWND recreation. This
exclusion is specific to Desktop Duplication, so Fairy self-exclusion prevents
optical recursion **without** hiding the companion from ordinary Windows Graphics
Capture, screenshots, or remote-display software. `WDA_EXCLUDEFROMCAPTURE`
remains forbidden for Presence. Native health reports the actual exclusion
mechanism and whether a separate WGC visibility probe passed.

## Renderer health contract

`PresenceRendererHealthReport` (schema v3) makes the source explicit and is
validated by `PresenceRendererSupervisor`:

- `NativeLiquidGlass` requires `mode = native`, `optics_source =
  desktop_duplication`, `dda_exclusion = applied`, and a present `source_format`
  and `adapter_luid`.
- `NativeIdentityFallback` requires `mode = native` and `optics_source =
  host_backdrop_identity`.
- `WebglCompatibility` and `CanvasCompatibility` remain the procedural,
  no-capture paths.

A renderer that reports `desktop_duplication` without an applied DDA exclusion,
or that reports pixel readback, is rejected.

## Monitor handoff and failure

Monitor handoff starts a candidate duplication session on the destination
adapter and swaps the texture and origin atomically on the render thread only
after the candidate produces a validated fresh frame; it never repositions the
Tauri windows. Desktop Duplication loss (secure desktop, per-session duplication
limit, disconnected or unsupported session) stops the texture source, selects
`host_backdrop_identity` with a specific failure reason, and uses bounded
exponential retry. Native and compatibility renderers cannot oscillate. A failed
source never affects Core, chat, Voice, or the main window.

## Consequences

- The production renderer now legitimately reports a `desktop_duplication`
  optical source; the July 22 completion-audit amendment that asserted
  `host_backdrop_identity` as the only production source is superseded by this
  ADR.
- True edge-responsive desktop refraction is now a delivered capability on
  machines where Desktop Duplication is available, not a deferred capability
  gate.
- The identity center still shows the real composed desktop; only the outer
  shoulder bends it. Reduced Transparency may add a bounded accessibility fill.

## Validation status

Rust shader-contract, CPU-mirror optical, HLSL compilation, DDA self-exclusion,
foreground-identity, and no-concentric-lens gates are executable and run in the
Rust workspace tests. The **native DDA optical behavior** — real
`IDXGIOutput5::DuplicateOutput1` acquisition, DDA-only exclusion revealing the
true underlying pixels while remaining visible to ordinary WGC capture,
stationary-lens updates within one display frame, cross-monitor handoff without
recursion, and the p95 frame-time budgets — requires the interactive Tauri/D3D11
runtime on the target Windows machine and is verified there, not in dev or
browser gates. The native runtime acceptance in
`docs/acceptance/presence-edge-lensing.md` must be re-run on the target machine
before a release is certified against this ADR.

## Sources

- Fairy design: `docs/superpowers/specs/2026-07-22-dda-liquid-glass-source-design.md`
- Fairy acceptance: `docs/acceptance/presence-edge-lensing.md`
- ADR 0020 (superseded production stance; retained boundary analysis)
- Microsoft, DXGI Desktop Duplication:
  https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api
- Microsoft, `IDXGIOutput5::DuplicateOutput1`:
  https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_5/nf-dxgi1_5-idxgioutput5-duplicateoutput1
- Microsoft, `WINDOWCOMPOSITIONATTRIB` (`WCA_EXCLUDED_FROM_DDA`):
  https://learn.microsoft.com/en-us/windows/win32/dwm/windowcompositionattrib
- Apple, `Meet Liquid Glass`, WWDC25:
  https://developer.apple.com/videos/play/wwdc2025/219/
