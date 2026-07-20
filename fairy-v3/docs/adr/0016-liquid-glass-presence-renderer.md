# ADR 0016: Liquid Glass Presence Renderer

## Decision

Fairy keeps Tauri 2 and uses two bounded Liquid Glass implementations. Standard
mode uses an isolated Three.js/WebGL2 renderer with a procedural environment;
enhanced mode uses Windows Graphics Capture, D3D11, and DirectComposition to
refract the bounded pixels behind `pet-render` without CPU readback or pixel
IPC. Unity, UE, Godot, and runtime avatar images are out of scope. A procedural
Canvas 2D renderer remains the compatibility path.

The production design uses one permanent pass-through render window and one
focusable input window. The render surface owns the complete visual silhouette;
the input surface overlays only DOM controls. At rest the compact input window
shares the 260-pixel render height: its upper 144-pixel transparent hit target
covers the visible core and its short, dynamically sized DOM composer sits below
the core. Click and context-menu interaction therefore need no global mouse hook.
During reply/menu presentation the input window expands without creating a
second glass silhouette. The rest of the render window remains pass-through.
Rust owns window placement, global cursor proximity, monitor changes, and focus
policy. Neither surface owns a Core client or project state.

The droplet and Bezier bridge are transition geometry only. The stable interactive
shape leaves a narrow optical gap between the core and capsule, while both are
drawn by the same renderer. The DOM form contributes no second border,
background, blur layer, glass silhouette, capture request, or WebGL context.

## Feasibility Gate

The development machine is Windows 11 build 26200 with WebView2 147.0.3912.98, an
AMD integrated adapter, and an NVIDIA RTX 5060 Ti. The browser gate proves an
alpha, premultiplied WebGL2 context at 100, 125, 150, and 200 percent scale. The
enhanced gate must separately prove a zero-copy WGC-to-D3D11-to-DirectComposition
path at 60, 144, and 300 FPS on displays that expose those refresh modes;
browser evidence cannot substitute for this native gate.
The interactive Windows native smoke validates the live `HMONITOR`, resolves
`IGraphicsCaptureItemInterop`, creates the monitor capture item, and presents the
captured texture through D3D11 and DirectComposition. The measured 60 and 144
FPS runs sustained 59.90 and 143.48 FPS respectively, with callback-to-present
p95 below 7 ms and DirectComposition present p95 below 0.2 ms. The cadence gate
also proves 15/30/60/144 runtime limits. The 300 FPS request and display-aware
cap are proven on the current 60 Hz mode; sustained 300 FPS remains a hardware
mode gate. Native frame and hit-test checks
prove the WebView render, composition surface, and input core proxy remain
aligned and click-through behavior is preserved.

WebGL2 failure selects compatibility mode. Enhanced-mode failure returns to the
WebView renderer and never enables another capture path. It does not trigger a
Unity fallback or disable the main Fairy window.

## Material Reference Selection

The July 2026 material refresh uses the `awesome-liquid-glass` collection as a
research index, not as a runtime dependency. Fairy keeps a clean-room shader so
the material can share its SDF with the existing morph and avoid a second DOM or
SVG silhouette.

- `Muggleee/liquid-glass` is the primary implementation reference because its
  WebGL2, GLSL, and SDF pipeline matches Fairy's renderer and demonstrates
  texture refraction, bounded channel separation, highlight, and shadow stages.
- `rdev/liquid-glass-react` is a secondary optical reference for keeping the
  center legible while concentrating displacement and chromatic separation at
  the edge. Its SVG/backdrop component is not embedded because it can only
  displace content inside the same web document.
- `shuding/liquid-glass` and the CSS/SVG recreations remain comparison fixtures,
  not production code, for the same backdrop and continuously regenerated map
  limitations.
- Apple's WWDC25 material guidance remains authoritative for lensing, adaptive
  thickness, directional highlights, interaction illumination, and restrained
  use of the material.

The stable shape follows the approved Fairy board: a 144-pixel circular core and
one aligned glass input capsule are separated by an 8-logical-pixel gap. The
droplet and liquid bridge only exist during reveal and return transitions.

## Privacy And Product Constraints

The renderer has two explicit material sources. Compatibility and standard
privacy mode use only the procedural environment. Enhanced live refraction is an
explicit device preference. WGC necessarily acquires the active monitor frame,
but it remains GPU-local and the production shader hard-clamps all lookups to the
`pet-render` rectangle plus a 32-logical-pixel optical guard band. Textures are
never persisted, analyzed, logged, uploaded, copied to the CPU, or sent through
WebView IPC. `pet-input` is not authorized to capture and contains no optical
renderer. The former GDI/full-frame IPC experiment is not a production route.
Hover may reveal input but may not activate the input window or steal keyboard
focus.

## Runtime And Recovery

The render and input WebViews are created serially after the main window loads;
they are not static Tauri startup windows. Rust moves the pair in one deferred
window-position transaction and keeps input above render without forcing either
window into the topmost band when the user disables always-on-top. The visible
core anchor remains stable across negative monitor coordinates, DPI changes,
work-area changes, and compact/expanded input sizes.

The selected active frame target is 60, 144, or 300 FPS. The native backend
reads the active monitor's current refresh rate and caps presentation and WGC
sampling to that physical rate; browser renderers inherit the same cap from RAF.
Idle, reduced-motion, power-saving, and full-screen policies may cap animation
at 30 or 15 FPS. Hidden and suspended surfaces stop rendering. Display, DPI,
power-resume, HDR color-space, device-loss, and capture-loss events invalidate
the native surface and enter a bounded watchdog restart. Three native failures
in five minutes force standard or compatibility rendering for that session;
repeated compatibility failure may disable only the Pet.

The Fairy process owns a Windows Job Object with kill-on-close. Development and
native smoke scripts place Vite, Fairy, Core Python, and descendant Node helpers
in the same lifecycle group so success, failure, Ctrl+C, and parent termination
cannot leave test services running.
