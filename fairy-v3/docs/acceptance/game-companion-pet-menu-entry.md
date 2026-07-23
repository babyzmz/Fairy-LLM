# Game Companion Pet Menu Entry Acceptance

## Observable behavior

- The main workspace does not render a persistent Game Companion launcher.
- The Fairy context menu contains one `Game companion` action.
- Selecting that action opens or focuses one dedicated `Fairy Game Companion` secondary window.
- Opening the Companion must not show, focus, or otherwise wake the main Fairy workspace.
- Closing the Companion hides the secondary window so an active or recently completed session can
  be resumed without creating a duplicate window.
- Realtime provider, voice, privacy, and memory settings remain available in Settings.

## State ownership and invariants

- The dedicated `companion` surface owns the Realtime session, capture-source selection, voice, and
  Companion UI state.
- The pet invokes only the native `open_companion_window` route. It does not create a Core client,
  start a worker, capture a screen, or hold session state.
- The `companion` surface uses a restricted Core RPC allow list containing only Realtime session
  and accepted game-memory methods. It cannot access projects, workspaces, files, ordinary chat,
  browser control, provider configuration, or general Core RPC.
- The Companion may enumerate capture sources, but the one-shot `capture_surface` host command
  remains main-window-only.
- Realtime presence is projected back to the main Presence bridge through a typed cross-window
  state channel; the pet still receives only the safe Presence projection.
- Moving the entry must not start the realtime worker during Fairy startup.
- Repeated open requests reuse the existing secondary window rather than creating duplicate
  windows or Realtime owners.

## Acceptance scenarios

1. Render the workspace with realtime support and verify no persistent `Companion` launcher is
   present.
2. Open the Fairy context menu, activate `Game companion`, and verify `open_companion_window` is
   invoked once while `open_main_window` and the legacy `realtime.open` broadcast are not invoked.
3. Mount the dedicated Companion surface and verify it immediately renders the Realtime UI without
   a modal launcher or main-window dependency.
4. Close and reopen the native Companion window and verify the same window label is reused.
5. Verify the Companion RPC allow list accepts only Realtime session and game-memory methods.
6. Verify capture-source listing is available to the Companion while one-shot capture remains
   denied.
7. Verify session start, connecting cancellation, barge-in, persistence failure, interruption, and
   stop paths still terminate loading and release worker and voice resources.
8. Verify Realtime presence updates reach the main Presence projection after rapid Companion
   hide/show, and late state from a disposed window cannot overwrite the current state.

## Risks and boundaries

- The context menu is rendered in a separate Tauri surface, so unit tests prove intent routing but
  not native window visibility or focus.
- Native acceptance requires the running Tauri development app: right-click Fairy, select
  `Game companion`, and confirm exactly one secondary window appears while the main window remains
  hidden or unfocused.
- Native close/hide behavior and single-instance focus require a real Tauri runtime; Vitest cannot
  substitute for this evidence.
- No database, cloud, provider, or Realtime domain contract changes are required.

## Evidence

- Vitest: `RealtimeCompanion.test.tsx`
- Vitest: `RealtimeCompanionWindowApp.test.tsx`
- Vitest: `PresencePanel.test.tsx`
- Vitest: `DualSurface.test.tsx`
- Vitest: `tauriTransport.test.ts`
- Rust: window authorization, Companion method allow list, and capture-scope tests
- TypeScript build
- Tauri development runtime, 2026-07-23:
  - one `Fairy Game Companion` secondary window rendered without raising the main workspace;
  - the authorized game-window source list populated in the secondary surface;
  - the custom close action hid the window and removed it from the visible-window list;
  - only one Companion instance was observed;
  - the configured Realtime provider reported an unavailable balance, so a paid live session was
    not claimed as verified.
