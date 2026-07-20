# Fairy Presence Motion Continuity Design

## Scope

This change improves animation continuity and removes black flashes from the existing Fairy
presence renderer. It does not change the current Liquid Glass silhouette, optical constants,
window dimensions, interaction rules, or visual identity.

## Problems

1. Work states are represented as discrete enum values. The native shader switches accent,
   energy, pulse, and notification effects with equality checks, so most state changes are hard
   cuts.
2. The TypeScript motion snapshot records progress for only a few interaction states and does not
   provide a continuously sampled visual transition for work states.
3. Drag and surface lifecycle signals can stop and restart the native renderer. During that gap the
   transparent host exposes a mismatched compatibility frame or an unpresented surface.
4. Native and compatibility renderers do not share a first-frame handoff contract.

## Architecture

### Interruptible visual state motion

The native renderer owns a small visual-state transition engine. It samples a continuous style
vector containing accent color, energy, pulse, notification wave, and voice contribution. When a
new state arrives, the engine uses the currently displayed vector as the next transition's origin.
Rapid transitions therefore remain continuous instead of snapping back to the previous enum.

Transitions use monotonic native time and an ease-in-out curve. Interaction transitions use
180-320 ms, work-state transitions use 220-360 ms, and urgent approval/error transitions use
140-180 ms. Reduced Motion uses a short 80 ms material dissolve while disabling deformation and
oscillating motion.

### Persistent renderer session

State changes, drag start/end, input expansion, and ordinary placement updates must not stop the
native session. Drag mode is already represented inside the native renderer and remains the only
drag control. Monitor or capture-source changes use candidate rebind: the current session remains
visible until the candidate has presented a valid transparent frame.

A full stop is reserved for application shutdown, explicit compatibility mode, disabling the pet,
or unrecoverable device loss.

### Transparent first-frame handoff

Every swap-chain buffer is cleared to premultiplied transparent black before drawing. A new native
surface remains hidden until it has presented a valid frame. The compatibility renderer remains a
standby surface, but its visibility changes only after native first-frame readiness or confirmed
native failure. No handoff may expose an opaque placeholder.

### Compatibility renderer

The Canvas renderer receives the same interruptible state semantics. It interpolates its visual
style locally and never recreates the canvas for an ordinary state change. This keeps fallback
behavior coherent without duplicating native lifecycle logic.

## State Transitions

The transition matrix covers:

- idle, aware, forming, input, options, returning, and sleeping;
- submitting, thinking, tool, responding, and speaking;
- notify, approval, and error;
- repositioning and recovery from drag or monitor changes.

Loops such as thinking pulse and speaking level continue from a global monotonic phase. A state
transition blends loop amplitude and color; it does not reset the global phase.

## Failure Handling

- A failed capture rebind keeps the current renderer visible with its last valid optical surface.
- A device-loss recovery keeps the compatibility surface available until the native candidate has
  presented.
- Repeated recovery failure selects compatibility mode without restarting the presence WebView.
- Stale or out-of-order presentation updates are ignored by revision/key checks.

## Verification

- Unit-test interrupted visual transitions and all state pairs used by the product.
- Verify state updates do not call native stop/start or surface reconfigure.
- Verify drag start/end does not stop the native session.
- Verify current back buffers are always cleared to transparent alpha.
- Stress at least 1,000 state updates and rapid work-state changes.
- Run the renderer at 60, 144, and 300 FPS targets; effective cadence remains display-limited.
- Exercise hover, input reveal, response, speaking, approval, error, drag, and cross-monitor paths
  in the development application while checking for black or opaque pixels.
