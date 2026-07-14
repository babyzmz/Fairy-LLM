# ADR 0016: Liquid Glass Presence Renderer

## Decision

Fairy keeps Tauri 2 and adds an isolated WebGL2 Presence renderer. Unity, UE, Godot,
continuous desktop capture, and runtime avatar images are out of scope. Three.js is
loaded only by the render surface after the raw WebGL2 gate succeeds. A procedural
Canvas 2D renderer remains the compatibility path.

The production design uses one permanent pass-through render window and one
focusable input window. The render surface owns the complete visual silhouette;
the input surface overlays only DOM controls. At rest the input window is a
144-pixel transparent hit proxy over the visible core so click and context-menu
interaction do not require a global mouse hook. During reveal it expands over the
same physical bounds as the unified liquid capsule. The rest of the render window
remains pass-through. Rust owns window placement, global cursor proximity,
monitor changes, and focus policy. Neither surface owns a Core client or project
state.

The droplet and Bezier bridge are transition geometry only. The stable interactive
shape directly unions the core and capsule, while the DOM form contributes no
second border, background, blur layer, or glass silhouette.

## Feasibility Gate

The development machine is Windows 11 build 26200 with WebView2 147.0.3912.98, an
AMD integrated adapter, and an NVIDIA RTX 5060 Ti. The existing Tauri pet already
proves per-pixel transparent WebView composition on this host. The isolated probe
must additionally prove an alpha, premultiplied WebGL2 context at 100, 125, 150,
and 200 percent scale before the renderer replaces Canvas 2D.

WebGL2 failure selects compatibility mode. It does not trigger a Unity fallback.
A future Windows Composition experiment may use a host backdrop without exposing
desktop pixels, but it is not part of this implementation.

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

The stable shape follows the approved Fairy board: a 144-pixel circular core
directly unions with one glass input capsule. The droplet and liquid bridge only
exist during the reveal and return transitions.

## Privacy And Product Constraints

The renderer never captures, caches, analyzes, or uploads desktop content. Its
glass appearance is synthetic and must not be described as physical refraction of
arbitrary applications. Hover may reveal input but may not activate the input
window or steal keyboard focus.
