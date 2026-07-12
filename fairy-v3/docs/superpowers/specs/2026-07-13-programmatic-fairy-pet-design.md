# Programmatic Fairy Pet Design

## Scope

Fairy V3 replaces the image-based Presence avatar and the separate Guide window with
one programmatic Windows companion. The companion is a presentation and routing
surface. It never owns a `CoreClient`, project state, approval authority, model
credentials, or execution capabilities.

The existing durable Presence reducer remains the trust boundary. Only public,
user-visible events and a bounded scratch-chat reply projection may cross into the
Pet window. The legacy Qt implementation is a behavior reference for motion, input,
and state transitions; no Qt code is copied.

## Window And Layout

The Pet uses one transparent Tauri window. Its collapsed size contains only the
Fairy core. Quick input, reply cards, and the context menu expand the same window
toward the upper left while preserving the core's bottom-right screen coordinate.
Collapsing reverses the resize without moving the core. This avoids a permanently
large transparent window intercepting input over other applications.

The collapsed view is 176 by 176 physical-independent pixels. The expanded view is
420 by 360, bounded by the active monitor work area. Monitor-relative position,
edge snapping, and reset behavior remain local device preferences.

## Canvas Renderer

`FairyCanvas` is a Canvas 2D renderer with no bitmap avatar dependency. It draws an
outer aura, dark floating shell, ice-white ring, inner blue core, orbit light,
particles, and gaze offset. A deterministic state style controls speed, amplitude,
palette accents, orbit behavior, and particle density.

States are booting, idle, hover, listening, analyzing, tool, streaming, speaking,
awaiting confirmation, error, sleeping, and dragging. Durable events select work
states; hover, listening, speaking, sleep, and dragging are local presentation
overlays. Hidden documents pause animation. Idle rendering is capped at 15 FPS,
active rendering at 60 FPS. Reduced Motion renders stable state frames without
continuous orbit, bob, pulse, or particles.

## Interaction

- Single click toggles a scratch-chat quick input.
- Double click opens and focuses the main window.
- Enter submits only after IME composition ends. The prompt is routed to the main
  window, which creates or selects a scratch Conversation and starts the Turn.
- Reply cards show bounded scratch-chat output and expand toward the upper left.
- Approval cards contain no decision controls; activating one only opens the main
  window.
- Drag begins after a 5 px pointer threshold and preserves monitor-relative state.
- The custom context menu provides New chat, pet auto-play, mute, always on top,
  open main window, settings, reset position, and exit.

The Pet publishes typed route requests over `BroadcastChannel`. Arbitrary Core
methods, project identifiers, command parameters, and secrets are not valid channel
messages. Preference mutation and exit use dedicated Tauri commands that authorize
only the `pet` window and accept bounded Pet-specific inputs.

## Voice And Replies

Quick input always targets scratch chat. The main window remains the owner of
Assistant Turns and native voice sessions. Pet-originated Task identity is kept in
the main renderer so `voice_auto_play_pet` applies only to replies triggered from
the Pet; ordinary main-window chat remains manual by default. Mute immediately
stops Pet-originated playback. PCM and model text are never sent as Pet commands.

`PresenceProjection` may contain at most one bounded scratch reply projection. It
does not retain a transcript or Task history. Project Task completion remains a
generic notice and opens the main workspace for review.

## Removed Surface

The `guide` Tauri window, Guide capability, React entry point, Guide tests, and the
`fairy-blue-ring.webp` runtime asset are removed. The Windows `.ico` remains the
application icon. The resource manifest is updated so no runtime avatar bitmap is
shipped.

## Verification

Unit tests cover projection filtering, channel schema rejection, Canvas state
mapping, IME behavior, click/double-click arbitration, anchored resize, Pet-only
preferences, and local dismissal. Rust tests prove Pet commands reject every other
window and that the Pet still cannot call Core, Voice Worker, capture, Shell, or
settings RPC methods.

Playwright covers collapsed and expanded layouts, reply and approval cards, context
menu actions, drag threshold, Reduced Motion, 100/125/200 percent scaling, no
overlap, and canvas nonblank pixel checks. Final verification includes the complete
desktop and repository gates.
