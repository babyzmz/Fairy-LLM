# Presence Liquid Glass Phase 1 Baseline

Recorded on 2026-07-16 with the development build and deterministic Presence
experiment modes.

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
  the next scheduled RAF so 60 and 144 remain real caps.

Phase 2 therefore removes the second input-window optical renderer. Phase 5 must
replace the GDI/full-frame IPC path with a native GPU capture and composition
prototype before enhanced live refraction can be considered production-ready.

## Reproduction

Use `scripts/test-presence-benchmark.ps1` with a debug executable. Release builds
intentionally disable deterministic experiment modes.
