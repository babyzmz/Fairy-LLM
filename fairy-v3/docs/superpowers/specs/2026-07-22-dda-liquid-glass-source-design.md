# DDA Liquid Glass Source Design

## Status

Approved on 2026-07-22. This design covers the feasibility gate and production
boundary for a sampleable desktop texture behind Fairy. It does not enable the
new source until the native gate proves the required behavior on the target
Windows machine.

## Problem

`HostBackdropBrush` gives Windows Composition an identity sample of pixels
behind a window, but Fairy cannot bind those pixels as a D3D shader resource.
Windows Composition also does not support a displacement-map effect over that
brush. HostBackdrop-only therefore cannot implement continuous per-pixel radial
refraction.

The previous Windows Graphics Capture path exposed a D3D texture, but a monitor
capture also contained Fairy. Cleaning or reconstructing the overlay produced
recursion, stale frames, repeated text and monitor-handoff artifacts. Excluding
Fairy with `WDA_EXCLUDEFROMCAPTURE` also made the companion disappear from
ordinary capture and remote-display paths, which violates the product contract.

## Decision

Use DXGI Desktop Duplication as the candidate texture source and apply
`WCA_EXCLUDED_FROM_DDA` only to Fairy-owned Presence windows. This exclusion is
specific to Desktop Duplication. Fairy remains unrestricted for Windows Graphics
Capture, screenshots and remote-display software.

The capture implementation is derived from the MIT-licensed
`windows-capture` 2.0.0 DDA module and kept in the dedicated
`fairy-windows-capture-dda` crate. Fairy retains only GPU frame acquisition,
format negotiation, adapter/output selection and recovery. The API accepts the
D3D11 device and output selected by Fairy, so capture and DirectComposition
rendering share the same adapter. The edge optics are informed by the
MIT-licensed `Pondot/liquidDX11` implementation, but its center lens scaling,
Dear ImGui shell, windowing code and capture-affinity behavior are not used.

Production remains on `host_backdrop_identity` until all feasibility gates pass.
No fallback may claim continuous refraction.

## Feasibility Gate

The probe runs in an interactive local Windows session and performs these steps:

1. Select the output containing a bounded test rectangle and create a D3D11
   device on that output's adapter.
2. Acquire a baseline frame through `IDXGIOutput5::DuplicateOutput1`, falling
   back to `IDXGIOutput1::DuplicateOutput` only when format negotiation is the
   sole unsupported operation.
3. Create a temporary top-level Fairy-owned test window with deterministic
   opaque pixels. Apply `WCA_EXCLUDED_FROM_DDA` before showing it.
4. Acquire fresh Desktop Duplication frames until the desktop timestamp advances.
   The test rectangle must match the baseline within a bounded tolerance and
   must not become black, stale or contain the test color.
5. Capture the same rectangle through the existing ordinary screen-capture path.
   That image must contain the test color, proving that DDA-only exclusion does
   not hide Fairy from general recording.
6. Remove the exclusion, acquire another DDA frame and verify that the test color
   becomes visible. This negative control prevents a false pass caused by a
   window that was never composed.
7. Destroy the test window, release every duplication frame and restore all
   process/window state even when a step fails.

The probe records HRESULTs, adapter/output identity, monitor coordinates, pixel
error metrics, frame timestamps and cleanup state. It never persists captured
pixels and never exposes them through WebView IPC.

## Production Source

After the gate passes, `DesktopTextureSource` owns one duplication session for
the monitor containing the Fairy core. It publishes immutable GPU frame handles
to the existing D3D render thread. The render thread samples only the bounded
surface rectangle plus a small optical guard band.

- No CPU readback or RGBA WebView transfer is allowed in production.
- Cursor capture is omitted.
- Dirty/move rectangles update the retained monitor texture when available.
- The shader uses one source texture and one continuous SDF-derived displacement
  field. It cannot layer scaled copies of the desktop.
- Identity is maintained through the center; refraction grows smoothly through
  the middle band and peaks at the outer edge.
- Foreground identity rings, particles and the breathing beacon are rendered in
  a final pass and never enter refraction or chromatic dispersion.

## Window Exclusion And Visibility

Every native Presence HWND is registered with the DDA-only exclusion before its
first visible frame. The exclusion is removed during shutdown. Registration is
revision-fenced and re-applied after HWND recreation.

`WDA_EXCLUDEFROMCAPTURE` remains forbidden for Presence. The native status must
report the actual exclusion mechanism and whether a separate WGC visibility
probe passed.

## Monitor Handoff

The core position, not the wide input surface, selects the output. Crossing an
output boundary starts a candidate duplication session on the destination
adapter. The old source remains active until the candidate has produced a fresh
frame and its coordinates are validated. Handoff changes the texture and source
origin atomically on the render thread; it never repositions the Tauri windows.

Mixed-adapter handoff may require a second D3D device and a shared texture. If
the adapters cannot share the resource, the renderer uses one identity frame
during source recreation instead of presenting stale pixels.

## Failure And Fallback

Desktop Duplication is unavailable on secure desktops, can hit the per-session
duplication limit and can report disconnected or unsupported sessions. These
conditions stop the texture source and select `host_backdrop_identity` with a
specific failure reason. Remote sessions are not treated as successful DDA
unless the real gate passes inside that session.

Repeated access loss uses bounded exponential retry. Native and compatibility
renderers cannot oscillate. A failed source never affects Core, chat, Voice or
the main window.

## Privacy And Lifecycle

The sampled texture remains GPU-local and ephemeral. It is not written to disk,
Ledger, logs, model context or telemetry. Diagnostics contain dimensions,
timings and hashes only. The source starts only while Liquid Glass is visible and
stops when the pet is hidden, disabled, suspended or the process exits.

## Acceptance Criteria

- DDA exclusion reveals the real underlying pixels rather than black or stale
  content.
- The test overlay remains visible through the ordinary WGC capture path.
- A stationary Fairy reflects changing desktop content within one display frame.
- Horizontal, vertical and cross-monitor movement produces no recursive image,
  old-frame trail, seam, flash or whole-window jump.
- Center displacement stays below 0.75 physical pixels; edge displacement is
  continuous and bounded to 4-8 physical pixels.
- No repeated text, concentric magnification boundaries or foreground-ring
  dispersion is present.
- GPU frame time p95 remains below 8 ms and capture-to-present p95 remains below
  one display interval at 60 and 144 Hz.
- DDA failure selects an explicit HostBackdrop identity fallback.

## Sources

- https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api
- https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_5/nf-dxgi1_5-idxgioutput5-duplicateoutput1
- https://learn.microsoft.com/en-us/windows/win32/dwm/windowcompositionattrib
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity
- https://github.com/NiiightmareXD/windows-capture/tree/2.0.0
- https://github.com/Pondot/liquidDX11
