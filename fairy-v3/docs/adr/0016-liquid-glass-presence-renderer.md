# ADR 0016: Liquid Glass Presence Renderer

## Decision

Fairy keeps Tauri 2 and adds an isolated WebGL2 Presence renderer. Unity, UE, Godot,
continuous desktop capture, and runtime avatar images are out of scope. Three.js is
loaded only by the render surface after the raw WebGL2 gate succeeds. A procedural
Canvas 2D renderer remains the compatibility path.

The production design uses one permanent pass-through render window and one
focusable input window. The render surface owns the complete visual silhouette;
the input surface overlays only DOM controls. Rust owns window placement, global
cursor proximity, monitor changes, and focus policy. Neither surface owns a Core
client or project state.

## Feasibility Gate

The development machine is Windows 11 build 26200 with WebView2 147.0.3912.98, an
AMD integrated adapter, and an NVIDIA RTX 5060 Ti. The existing Tauri pet already
proves per-pixel transparent WebView composition on this host. The isolated probe
must additionally prove an alpha, premultiplied WebGL2 context at 100, 125, 150,
and 200 percent scale before the renderer replaces Canvas 2D.

WebGL2 failure selects compatibility mode. It does not trigger a Unity fallback.
A future Windows Composition experiment may use a host backdrop without exposing
desktop pixels, but it is not part of this implementation.

## Privacy And Product Constraints

The renderer never captures, caches, analyzes, or uploads desktop content. Its
glass appearance is synthetic and must not be described as physical refraction of
arbitrary applications. Hover may reveal input but may not activate the input
window or steal keyboard focus.
