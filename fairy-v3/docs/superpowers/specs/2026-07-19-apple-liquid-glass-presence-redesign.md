# Apple-Aligned Liquid Glass Fairy Presence Redesign

Status: Approved design; Windows optical path implemented and verified on 2026-07-19.

Date: 2026-07-19

## 1. Purpose

This specification redesigns the Windows Fairy desktop presence so it behaves like an Apple Liquid Glass control while remaining a clean-room Windows implementation. It keeps the existing Tauri 2, Rust, D3D11, Windows Graphics Capture (WGC), DirectComposition, and HLSL architecture. Unity and a second application runtime are explicitly excluded.

The work fixes the current visible failures:

- opaque white rectangles or trapezoids around the pet;
- recursive screen-capture copies, ghost rings, and stale backgrounds;
- white edge flashes during and immediately after dragging;
- strong magnification through the center instead of edge-focused lensing;
- excessive rainbow dispersion and mechanical concentric rings;
- abrupt, black, or disconnected state transitions;
- drag motion that feels like window movement rather than flexible glass;
- an oversized invisible hit region that blocks nearby desktop interaction.

## 2. Product Principles

1. Fairy is one coherent glass object, not a Shader circle placed over a rectangular WebView.
2. The entire Fairy body is Liquid Glass. Its center uses near-identity screen sampling while optical deformation grows toward the edge.
3. Glass responds to content, light, pointer velocity, interaction, and size without becoming opaque chrome.
4. Input text, icons, caret, menus, and reply content are rendered above the optical material and are never refracted.
5. Shape blending is transitional. A liquid bridge must not remain in the stable expanded state.
6. Dragging never exposes a title, taskbar item, rectangular white surface, stale capture, or black frame.
7. The pet remains useful when capture or GPU composition fails by degrading to the existing compatibility renderer.
8. Desktop pixels remain GPU-local and transient: no CPU readback, persistence, indexing, logging, or upload.

## 3. Apple Design Terminology

The implementation uses the following public Apple design concepts as behavioral references. It does not claim to reproduce Apple's private material implementation.

| Fairy requirement | Apple-aligned term | Required behavior |
| --- | --- | --- |
| Lens effect | Lensing | Dynamically bends and concentrates light, primarily near the material boundary. |
| Drag response | Interactive response | Material flexes immediately, highlights energize, and deformation follows movement. |
| Droplet and bridge | Shape blending / morphing | Nearby glass shapes merge briefly and separate through matched geometry. |
| Jelly release | Gel-like flexibility / elastic properties | A highly damped spring produces one subtle overshoot and settles quickly. |
| Open and close | Materialize transition | Thickness, lensing, highlights, and shape resolve together instead of using a plain opacity fade. |
| Input, menu, reply expansion | Matched geometry morphing | Content appears from the originating glass object on one floating visual plane. |
| Background response | Adaptivity | Tint, contrast, shadow, and dynamic range adapt to the content behind the material. |
| Larger surfaces | Material thickness adaptation | Larger controls use deeper shadow and softer scattering without becoming heavily opaque. |

The default material follows the adaptive behavior of Apple's regular glass treatment. A clear treatment is not the default because Fairy must remain legible over unknown desktop content.

Official references:

- Apple, "Meet Liquid Glass": https://developer.apple.com/videos/play/wwdc2025/219/
- Apple Human Interface Guidelines, "Materials": https://developer.apple.com/design/human-interface-guidelines/materials
- Apple, "Applying Liquid Glass to custom views": https://developer.apple.com/documentation/SwiftUI/Applying-Liquid-Glass-to-custom-views
- Apple, `GlassEffectContainer`: https://developer.apple.com/documentation/swiftui/glasseffectcontainer
- Apple, "Get to know the new design system": https://developer.apple.com/videos/play/wwdc2025/356/

## 4. Selected Architecture

The selected design combines four changes because none is sufficient by itself:

1. A GPU clean-backdrop cache replaces inverse reconstruction of self-captured pixels.
2. A native circular hit proxy replaces the visible idle `pet-input` WebView.
3. An Apple-aligned SDF optical material replaces the current strong center magnification and fixed ring stack.
4. A unified native motion model drives drag deformation, droplet formation, morphing, and spring settling.

The existing Rust presence coordinator remains the source of physical facts. TypeScript remains responsible for semantic state and DOM content. HLSL performs optical rendering and local interpolation. No per-frame TypeScript-to-Rust IPC is introduced.

## 5. Window and Input Architecture

### 5.1 Native render surface

The DirectComposition render surface remains a transparent, topmost, non-taskbar native window. Its visual bounds may be larger than the pet to preserve Shader margins, but its hit-test result is transparent outside explicitly interactive native regions.

The window must have:

- an empty title;
- no non-client frame;
- no taskbar or Alt+Tab presence;
- no background brush that can paint white;
- no move-time bitmap copying;
- no activation during hover or drag;
- stable physical coordinates across mixed-DPI displays.

### 5.2 Circular native hit proxy

Idle and drag interaction use a small transparent native hit-proxy window aligned with the visible circular core. Its hit region is circular, not rectangular. It handles:

- left press and long-press drag;
- pointer capture during drag;
- click and double-click routing;
- right-click routing;
- hover entry and exit facts.

The proxy and render surface move as one native window group. The first drag frame derives its anchor from the current HWND physical coordinates, never from persisted or cached logical coordinates. This prevents the initial jump.

### 5.3 DOM input surface

The `pet-input` WebView is hidden while Fairy is idle, aware, or dragging. It is shown only when input, a reply card, a menu, or a routed action requires DOM controls.

When shown:

- its transparent client area is limited to the actual content bounds;
- it is positioned once from the settled native anchor;
- it does not move or resize every drag frame;
- it cannot paint a default white background before transparency is ready;
- focus is requested only after an explicit click;
- it is hidden again before the native material collapses.

The stable expanded layout contains a visible air gap between the core and the input capsule. A liquid bridge exists only during the morph transition.

## 6. Clean Backdrop Capture

### 6.1 Problem

Full-monitor WGC includes Fairy's own rendered surface. The current Shader attempts to recover the underlying desktop by subtracting the previous premultiplied overlay and dividing by `1 - alpha`. At high alpha, small timing or position errors are amplified into white bars, stale copies, and recursive rings.

### 6.2 GPU-only clean cache

The renderer maintains ping-pong GPU textures:

- `captured_frame`: the newest WGC monitor frame;
- `clean_backdrop_previous`: the last accepted clean desktop estimate;
- `clean_backdrop_current`: the repaired frame used by the optical Shader;
- `contamination_mask`: current and recent Fairy coverage in monitor coordinates.

Startup order is strict:

1. Create the WGC source and D3D resources.
2. Receive a valid monitor frame while the native Fairy surface is hidden.
3. Seed both clean textures from that frame.
4. Show the native surface only after the first clean frame is available.

Each frame then:

1. copies the current monitor capture outside the previous Fairy coverage;
2. matches the newest presented overlay that predates the WGC timestamp;
3. removes that known premultiplied overlay with a denominator clamped to `0.06`;
4. rejects out-of-gamut recovery and bounds the accepted per-frame color delta;
5. recovers sharp coverage boundaries from a two-pixel lower-alpha neighbor so old rings cannot remain in the cache;
6. retains the previous clean value only where reconstruction confidence is insufficient; and
7. swaps the clean textures after presentation.

No path divides by a value approaching zero. The optical shell remains below `0.925` coverage so animated monitor content continues to converge while Fairy is stationary. The cache never accepts the current composite directly inside known Fairy coverage, because doing so would recursively accumulate the identity rings and highlights.

### 6.3 Capture transitions

Monitor changes, DPI changes, display disconnects, device resets, and WGC source recreation invalidate the cache. Fairy freezes the last valid optical frame, hides the native surface if the cache cannot be trusted, seeds a new clean frame, and then materializes back in. It never presents an uninitialized or stale monitor texture.

## 7. Optical Material

### 7.1 Shape model

All visible glass geometry is produced from one signed-distance-field composition:

- circular identity core;
- velocity-oriented droplet;
- transient Bezier-like bridge;
- input or reply capsule;
- optional menu/reply expansion surface.

Smooth union is used only while shapes are morphing. Stable surfaces are visually separated unless the product state explicitly requires one continuous control.

### 7.2 Lensing profile

The normalized inward distance from the SDF boundary drives optics:

- inner region, approximately 30 percent of the radius: near-identity sampling with only the restrained material and identity layers;
- transition region: one continuous normal-driven refraction grows through the middle of the body instead of appearing only as a narrow border;
- outer region, approximately 30 percent: strongest refraction, Fresnel response, edge compression, and restrained dispersion.

Interactive edge refraction targets roughly 4 to 10 logical pixels. Idle refraction is lower. Chromatic dispersion targets 0.1 to 0.35 physical pixels and has a hard maximum of 0.45 physical pixels. The center must not behave as a magnifying lens. Once displacement exceeds a subpixel threshold, the refracted sample replaces the underlying desktop nearly opaquely; blending two readable desktop copies is forbidden.

### 7.3 Surface response

The material combines:

- edge-focused refraction from the SDF normal;
- subtle Fresnel reflectance;
- two geometry-dependent highlights, not fixed white arcs;
- a narrow inner dark edge that defines thickness;
- a soft lower lip for perceived depth;
- low-amplitude caustic energy driven by motion and curvature;
- restrained RGB separation only at high curvature;
- adaptive tint, shadow, and highlight range based on sampled backdrop luminance.

The fixed stack of heavy decorative rings is removed. Fairy retains two restrained atmospheric line rings and a subtle breathing center point on a dedicated foreground identity plane. This identity plane is composited above the glass, never changes the lens sampling coordinates, and cannot disappear when capture contrast changes.

### 7.4 Content plane

Text, icons, caret, badges, and buttons render in an undistorted content plane above the optical material. The content plane may receive adaptive contrast or a localized dimming field, but never uses refracted coordinates. Nested glass layers are avoided.

## 8. Motion and State Transitions

### 8.1 State ownership

TypeScript owns semantic state:

`idle`, `aware`, `listening`, `analyzing`, `tool`, `streaming`, `speaking`, `approval`, `ready`, `error`, `sleeping`, and DOM content visibility.

Rust owns physical facts:

- pointer position and velocity;
- press, capture, drag, and release;
- monitor and work-area bounds;
- DPI and physical anchor;
- renderer health and frame cadence.

The native renderer interpolates all visual parameters per frame from a compact snapshot. It does not wait for per-frame TypeScript updates.

### 8.2 Hover and expansion sequence

The normal sequence is:

1. `aware`: highlight and internal energy orient toward the pointer.
2. `materialize`: edge thickness and lensing increase without changing hit bounds.
3. `droplet`: a small directional lobe forms toward the future capsule.
4. `shape_blend`: a short bridge connects the source and target geometry.
5. `capsule_morph`: the input material reaches its stable dimensions.
6. `content_reveal`: DOM content fades and translates into place after the material stabilizes.

On close, content disappears first, then the material reverses the sequence. The bridge is absent from the stable expanded state.

### 8.3 Drag response

During drag:

- the leading edge compresses slightly;
- the trailing edge stretches along pointer velocity;
- the circular core becomes a restrained velocity-oriented ellipse;
- highlights travel across the surface in the movement direction;
- caustic energy increases briefly with acceleration;
- capture masks cover both recent and current geometry.

On release, a highly damped spring returns the shape to rest with at most one overshoot of 3 percent and a 240 to 320 ms settle time. Repeated bouncing, teleportation, black frames, and changes to the resting visual style are forbidden.

### 8.4 Work states

Work states alter only semantic energy cues:

- analyzing: slow inward light concentration;
- tool: restrained amber traveling highlight;
- streaming: blue-white directional flow;
- speaking: amplitude-smoothed radial pulse;
- approval: warm stationary emphasis;
- ready: mint completion glint followed by idle;
- error: short coral boundary pulse, then stable readable state;
- sleeping: reduced opacity, particles, and cadence.

They do not replace the optical material or create separate reply messages.

## 9. Frame Cadence and Performance

The user-selectable active frame-rate modes remain 60, 144, and 300 FPS. The renderer is display-limited and must not issue catch-up bursts when presentation is late.

Targets on the current RTX system:

- 60 FPS mode: GPU frame time p95 below 8 ms;
- 144 FPS mode: GPU frame time p95 below 6 ms;
- 300 FPS mode: GPU frame time p95 below 3.2 ms when the display and compositor permit it;
- no CPU readback or per-frame cross-process IPC;
- no unbounded allocation, command-list growth, or capture queue growth;
- hidden surfaces stop rendering;
- inactive idle mode may reduce cadence after 15 seconds, while pointer interaction immediately resumes the selected active rate.

If the display cannot present the selected rate, health reporting exposes the actual cadence without busy-looping.

## 10. Accessibility

- Reduced Motion removes velocity stretch, shape blending, and elastic overshoot. It uses a 160 ms materialize transition.
- Reduced Transparency switches to a high-contrast compatibility material while preserving Fairy state and input behavior.
- Increased Contrast strengthens the content scrim and boundary contrast, not chromatic dispersion.
- Keyboard focus is never taken on hover.
- Pointer interaction outside the circular proxy or visible DOM content remains available to applications underneath.

## 11. Failure Handling

- WGC unavailable: switch to the procedural compatibility renderer and report a concise renderer status.
- WGC frame stale: freeze the last valid clean backdrop briefly, then fall back; do not show recursive capture.
- D3D device or context loss: hide the native surface, recreate resources, seed a clean frame, and materialize back in.
- Shader compilation failure: retain compatibility rendering without affecting the main Fairy window or Core.
- Native hit proxy failure: disable hover and use the tray or main window; never show the idle input WebView as a fallback hit target.
- DOM input creation failure: open the main window instead of exposing an empty transparent or white rectangle.

## 12. Verification

### Automated

- HLSL compilation and resource lifecycle tests.
- Shader math tests proving finite output and no inverse near-zero alpha division.
- Pixel tests proving alpha is zero outside the SDF and no opaque rectangle exists outside visible material.
- Clean-backdrop tests for stationary, moving, overlapping-mask, monitor-change, and stale-frame cases.
- Rust tests for first-drag anchoring, circular hit testing, pointer capture, mixed-DPI crossing, and release settling.
- TypeScript tests for state ordering, content reveal timing, Reduced Motion, and semantic work-state projection.
- Window tests proving `pet-input` is hidden during idle and drag and that no Fairy auxiliary window has a title or taskbar entry.

### Real desktop

- Light, dark, patterned, video, game, HDR, and high-contrast backgrounds.
- Background movement while Fairy is stationary.
- Fairy movement while background content changes.
- Dragging across application boundaries without retaining wallpaper or the previous window.
- Cross-monitor dragging at mixed DPI and 60, 144, and 300 Hz.
- Repeated first drag after startup, sleep resume, display reconnect, and renderer restart.
- Input, reply card, menu, and main-window routing.

### Visual acceptance

- The whole circular body reads as glass while the central backdrop remains structurally unchanged and readable.
- The atmospheric rings and breathing point remain on an undistorted foreground identity plane.
- Lensing grows toward the outer edge and never becomes a full-center magnifier.
- Dispersion is visible only as a restrained high-curvature accent.
- The breathing center identity remains visible in every state.
- No white rectangle, white bar, black flash, recursive copy, stale backdrop, ghost ring, or fixed title appears.
- Dragging and release feel continuous, flexible, and highly damped.
- Droplet and bridge exist only during transitions.
- Text and icons remain sharp and undistorted.
- Nearby desktop controls remain clickable outside the visible circular or DOM interaction regions.

## 13. Non-Goals

- Reproducing Apple's private rendering implementation or claiming pixel identity with Apple platforms.
- Introducing Unity, Unreal Engine, Godot, or a second pet runtime.
- Capturing, persisting, analyzing, or transmitting desktop imagery outside GPU-local composition.
- Reworking Fairy's application icon, domain state, chat protocol, or Core execution architecture.
- Adding decorative bloom, heavy neon, permanent rainbow rings, or a persistent liquid bridge.

## 14. Implementation Order

1. Replace inverse self-capture reconstruction with the GPU clean-backdrop cache.
2. Add the native circular hit proxy and hide the idle/drag input WebView.
3. Replace the optical profile with center-preserving edge lensing and adaptive material response.
4. Implement unified SDF droplet, transient bridge, matched morph, drag deformation, and damped release.
5. Bind semantic Fairy states to restrained material energy cues.
6. Complete automated, cross-monitor, visual, and 60/144/300 FPS validation before tuning cosmetic details.
