# Presence Liquid Glass Phase 1 Baseline

Recorded on 2026-07-16 with the development build and deterministic Presence
experiment modes.

> Historical baseline: Phase 5 replaced the measured GDI/full-frame IPC route
> on 2026-07-17 with WGC -> D3D11 -> DirectComposition. The table below remains
> useful for the migration decision, but it is not current production evidence.
>
> Superseded again on 2026-07-22: Presence no longer starts WGC. DComp
> `HostBackdropBrush` supplies the live, geometrically stable center. A GPU-only
> DXGI Desktop Duplication texture supplies the narrow displaced edge, while an
> independent D3D11 swap chain renders Fairy's material and identity foreground.
> The WGC results below are retained as historical diagnostic evidence, not
> current behavior.

## Test host

- Windows 11 build 26200
- AMD Ryzen 7 9800X3D
- NVIDIA GeForce RTX 5060 Ti (WebView2 ANGLE D3D11 renderer)
- AMD integrated GPU present
- 2560 x 1440 display at 299 Hz
- WebView2 147.0.3912.98

## Measurement method

`desktop/scripts/probe-presence-webview.mjs` resets runtime metrics after a
two-second warmup and samples the selected experiment for at least six seconds.
The experiment and target frame rate are development-only values. Production
builds reject non-normal native backdrop experiments.

| Mode | Target | FPS average | FPS P1 | GPU P95 | Capture P95 | IPC P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| normal | 60 | 59.997 | 59.524 | 0.231 ms | 15.500 ms | 24.000 ms |
| static backdrop | 60 | 60.031 | 59.524 | 0.218 ms | n/a | n/a |
| capture only | 60 | 58.047 | 28.902 | 0.065 ms | 16.880 ms | 20.600 ms |
| IPC/upload only | 60 | 60.811 | 50.505 | 0.059 ms | 0.000 ms | 16.000 ms |
| no particles | 60 | 61.027 | 51.020 | 0.063 ms | 14.802 ms | 22.600 ms |
| no refraction | 60 | 61.319 | 51.020 | 0.059 ms | 15.187 ms | 23.100 ms |
| normal | 144 | 143.960 | 99.010 | 0.060 ms | 15.148 ms | 23.000 ms |
| static backdrop | 144 | 144.005 | 99.010 | 0.059 ms | n/a | n/a |

P1 at 144 FPS reflects unavoidable 299 Hz RAF quantization: the cumulative
scheduler alternates display intervals to hold the requested average instead of
collapsing to a 100 or 150 FPS divisor.

## Decision gates

- The liquid shader is not the primary bottleneck. Disabling particles or
  refraction does not materially change timing.
- The current GDI capture exceeds the 12 ms native-capture migration gate.
- Full-frame pixel IPC exceeds the 5-8 ms native-GPU migration gate.
- Static-backdrop mode holds both configured frame-rate averages.
- Backdrop arrivals must not trigger out-of-band draws. Textures are consumed by
  the next scheduled RAF so 60, 144, and 300 remain real requested caps.

Phase 2 therefore removes the second input-window optical renderer. Phase 5 must
replace the GDI/full-frame IPC path with a native GPU capture and composition
prototype before enhanced live refraction can be considered production-ready.

## Phase 5 Amendment

The production enhanced backend now keeps the latest WGC frame as a D3D11
texture, samples it in the Liquid Glass HLSL shader, and presents through a
dedicated DirectComposition surface. There is no staging texture, `Map`, CPU
readback, encoded frame, or pixel IPC contract. Standard mode remains the
default and starts no capture session.

The current source gates prove production HLSL compilation, zero-readback
contracts, adaptive 15/30/60/144/300 scheduling, HDR/SDR format selection, device/context
recovery, transition-only liquid bridges, atomic window placement, and
process-tree cleanup. The browser visual suite covers 100, 125, 150, and 200
percent scale.

The 2026-07-17 source probe now explicitly resolves
`IGraphicsCaptureItemInterop`, calls `CreateForMonitor`, preserves the native
HRESULT, validates the selected `HMONITOR` against a fresh monitor enumeration,
and passes the pre-created monitor item into the capture worker. The probe
reported the following real host diagnostics:

- target surface: 800 x 325 at (1750, 1055)
- monitor: 2560 x 1440 at (0, 0), `XG27ACMS`, `\\.\DISPLAY37`
- adapter/output: NVIDIA GeForce RTX 5060 Ti, adapter 0, output 0
- source stage: `capture_started`
- capture item: 2560 x 1440
- HRESULT: none

The interactive Windows run completed real zero-copy WGC, D3D11 shader sampling,
DirectComposition presentation, screenshot capture, and click-through checks.
The 10-second 60 FPS run presented 600 frames at 59.90 FPS, with 6.68 ms
callback-to-present p95 and 0.15 ms present p95. The 10-second 144 FPS run
presented 1425 frames at 143.48 FPS, with 6.19 ms callback-to-present p95 and
0.14 ms present p95. Runtime cadence measured 15.13, 29.95, 59.61, and 144.23
FPS for the 15/30/60/144 limits. A separate native window-group gate proved the
WebView and DirectComposition render frames are identical, the input core proxy
is aligned, and the render surface remains click-through. The managed sandbox's
earlier `0x80070424` result is retained only as evidence that the failure was
specific to that non-interactive service context.

The 2026-07-18 high-refresh gate requested 300 FPS and confirmed the new setting,
native contract, deadline pacing, capture reuse, and display-aware cap. The active
`XG27ACMS` mode reported 60 Hz, so the renderer intentionally selected 60 effective
FPS and presented 299 frames in five seconds at 59.79 FPS; DirectComposition
present p95 was 0.11 ms. A true 300 FPS performance claim remains gated on an
interactive monitor mode that Windows reports at 300 Hz or above.

## 2026-07-19 Live Composite Amendment

The active `XG27ACMS` mode now reports 300 Hz. The production source remains the
pre-created `CreateForMonitor` item: the capture item and monitor are both
2560 x 1440, `capture_source_stage` is `monitor_capture_started`, and
`capture_window_handle` is null. There is no window-source fallback.

The GPU clean-backdrop pass now reconstructs the known previous premultiplied
overlay with a finite denominator, gamut confidence, bounded temporal change,
and lower-alpha neighbor recovery at sharp coverage edges. This removes the
stale-cache behavior that updated only while dragging and prevents low-alpha
identity lines from recursively becoming white arcs.

An interactive stationary-lens gate placed a controlled red then green native
surface behind an unmoving Fairy and captured the real monitor composite after
each change. The mean RGB difference across 12,576 lens pixels was 73.39 at 60
FPS, 73.68 at 144 FPS, and 73.74 at 300 FPS. The corresponding native runs
measured 59.81, 142.91, and 297.68 average FPS. At 300 FPS the P1 rate was
200.12 FPS, callback-to-present p95 was 3.71 ms, and DirectComposition present
p95 was 0.218 ms. These runs also passed HLSL compilation, window alignment,
transparent hit testing, process-tree cleanup, and controlled visual capture.
The physical click injection was intentionally skipped in these three
background-update samples so an active user pointer could not invalidate an
otherwise unrelated optical test; the earlier dedicated click-through gate
remains the acceptance evidence for that behavior.

## Reproduction

Use `scripts/test-presence-benchmark.ps1` with a debug executable. Release builds
intentionally disable deterministic experiment modes.
