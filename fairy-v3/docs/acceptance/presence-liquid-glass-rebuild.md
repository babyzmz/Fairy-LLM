# Presence Liquid Glass Rebuild Acceptance

Recorded on 2026-07-22 for the staged replacement of the current Presence
optics and window lifecycle.

## Observable behavior

- Stable idle, input, menu, reply, and notification states never expose a
  transition capsule or a stale rectangular hit region.
- Liquid mode reports the backend that is actually presenting. The settings UI
  distinguishes Native Host Backdrop material, WebGL compatibility, Canvas compatibility,
  and unavailable states.
- The compatibility renderer remains visible while the native surface starts.
  It is disposed only after the native backend has presented two frames.
- A native failure produces one explicit fallback reason and does not oscillate
  between native and compatibility renderers.
- Native optics use the desktop compositor backdrop. Compatibility rendering is
  visibly identified and is never presented as real desktop refraction.
- Production optics do not start Windows Graphics Capture or DXGI Desktop
  Duplication. HostBackdrop owns the live identity sample and no desktop texture
  is bound to the foreground shader.
- HostBackdrop-only cannot perform per-pixel continuous displacement. The
  production renderer does not claim radial refraction, chromatic displacement,
  or Apple-equivalent lensing until a supported pixel-source path passes a new
  native capability gate.
- The complete center remains the unmodified HostBackdrop in normal mode. A
  single continuous outer material profile adds only a restrained rim,
  directional highlights, and one narrow caustic.
- The two atmosphere rings and breathing beacon are rendered by the final
  foreground pass. No broad center glow, particles, text, or desktop copy enters
  that identity layer.
- The pet core and expanded input use one native hit-region model. Pixels outside
  the active circle, input, card, or menu pass through to the application below.

## Ownership and invariants

- Rust owns native window geometry, hit regions, actual backend health, monitor
  refresh rate, and effective presentation rate.
- TypeScript owns requested mode and visual state, but cannot claim native health
  without a validated native status response.
- `PresenceRendererHealthReport.schema_version == 2` is the only accepted health
  contract for this rebuild.
- At most one renderer contributes visible Fairy pixels after native readiness.
- A late compatibility `disposed` report cannot replace a running native health
  report.

## Automated acceptance

- TypeScript unit tests cover stable capsule suppression, native two-frame
  activation, fallback disposal, bounded health serialization, failure recovery,
  and settings labels.
- Rust tests cover health schema validation, supervisor circuit breaking, empty
  and circular hit regions, the native two-frame start gate, the clear-center
  material profile, and the absence of WGC, Desktop Duplication, desktop shader
  textures, and unsupported displacement effects in production Presence.
- TypeScript and Rust formatting/type checks run at every staged commit.

## Native acceptance

The following checks require the real Tauri runtime and cannot be proven by
Vitest or a mocked native command:

1. Start in `liquid + enhanced`; compatibility pixels remain visible until the
   second native present, then disappear without a black or white frame.
2. Open Settings and verify the reported backend is `Native Host Backdrop material`, the
   optics source is Host Backdrop, the UI does not claim pixel displacement, and
   displayed effective FPS does not exceed the monitor refresh rate.
3. Force one native startup failure and verify one explicit compatibility state,
   no renderer loop, and a still-operable pet.
4. Probe the idle 180 x 180 surface with `WindowFromPoint`; all points outside the
   core circle must resolve to the underlying application.
5. Repeat drag and input expansion at 100%, 125%, 150%, and 200% scaling across
   two monitors; no stale rectangle, focus theft, or prior-frame residue is
   acceptable.

Native evidence must be recorded after all seven stages. Until then, native
visual and hit-test behavior remains explicitly unverified.

## Risks and mocked boundaries

- Unit tests mock Tauri IPC and therefore do not prove DirectComposition ordering,
  HWND z-order, per-monitor DPI, or live backdrop pixels.
- Renderer health is process-local diagnostic state and is intentionally not
  persisted or synchronized.
- A process crash can clear the diagnostic snapshot; Settings must then show an
  unavailable state rather than infer success from the saved preference.
