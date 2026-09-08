# Fairy SVG eye — implementation and acceptance

Base: babyzmz/Fairy-LLM@98f2f4ab438f96c4daf20e75d3be8aba0b291bf6
Visual source: Chengzhibense/Fairy-DSH@d639887a386b0de6fef2e61c97dc268631854591

## Scope
Port the upstream procedural eye geometry, colors, concentric breathing, rotating
lids, radial glows, scanline halo and pulse. Keep the existing liquid-glass default.
Add an independently switchable chat stage and an HDD Eye pet form. Do not change
Agent, model routing, memory, browser automation, voice generation, or permissions.

## Ownership
Each mount owns its SVG IDs, animation clock, RAF, observers and event listeners.
Chat inputs belong to its conversationId; stale turns and unrelated voice playback
must not become its state. Desktop inputs belong to the existing presence snapshot.
A visual is read-only: no RPC tools and no new permission or camera/microphone access.
Desktop preferences remain the persistent authority, with missing fields migrated.

## Execution sequence
1. Write/run failing pure-module tests for source geometry, IDs, timing, states,
   DPI placement and native selection.
2. Port geometry/templates and timing into typed, dependency-free visual modules.
3. Implement lifecycle-owned DOM controller and a thin React mount wrapper.
4. Add chat/pet adapters and versioned settings patches against the fixed base.
5. Verify pure modules and real Chromium multiple-instance/lifecycle/visual behavior.
6. Package guarded install/rollback tools, pinned clean-source assembly, preview,
   upstream notices, exact changed-file manifest and evidence.

## Required checks and environment limits
Automatable here: pure TypeScript module checking; Node behavior tests; Chromium
SVG rendering, independent IDs, pointer tracking, reduced motion, visibility,
remount/dispose, clipping, offline preview, installer safety and rollback.
Required on Windows and NOT substitutable with Chromium: actual Tauri/Core build,
native D3D11 start/stop and redraw, tray/window lifecycle, hit testing, input focus,
IME, moving across monitors and DPI 100/125/150/200%, 20 live form switches while
streaming/awaiting approval/playing TTS, restoration of original liquid optics.

## Invariants
- Liquid Glass remains the default and its saved controls are not reset.
- Switching to HDD Eye stops/unmounts the old visual; lifecycle signals cannot
  re-request native rendering while the eye is selected.
- All SVG URL/href references resolve to the same instance; no document-global IDs.
- One RAF chain per live instance; no CSS/SMIL secondary animation loops.
- Hidden, suspended, reduced-motion and disposed instances have no running loop.
- Actual audio state is distinguished from streaming text.
- No fixed overlay blocks the composer; the chat stage has an allocated layout slot.
- Migration must not overwrite a divergent existing source file silently.
- Backups are made before any source write; rollback refuses to destroy later edits.

## Evidence
See the package's evidence/VERIFICATION.md for actual commands, results and unverified
items. A source implementation or a passing browser test is not a Windows release.
