# Game Companion Pet Menu Entry Acceptance

## Observable behavior

- The main workspace does not render a persistent Game Companion launcher.
- The Fairy context menu contains one `Game companion` action.
- Selecting that action opens the main Fairy window and opens the existing Realtime Game
  Companion dialog.
- Realtime provider, voice, privacy, and memory settings remain available in Settings.

## State ownership and invariants

- `RealtimeCompanion` in the main window remains the owner of session, capture, voice, and dialog
  state.
- The pet emits only the existing `realtime.open` routing intent. It does not create a realtime
  client, start a worker, capture a screen, or hold session state.
- Moving the entry must not start the realtime worker during Fairy startup.
- Repeated open requests reuse the mounted dialog owner rather than creating duplicate dialogs.

## Acceptance scenarios

1. Render the workspace with realtime support and verify no persistent `Companion` launcher is
   present.
2. Render the realtime dialog owner closed, increment `openRequest`, and verify the dialog opens.
3. Open the Fairy context menu, activate `Game companion`, and verify the `openCompanion` action is
   invoked once.
4. Verify the existing session start, cancellation, barge-in, persistence failure, and stop tests
   still pass when the dialog is opened through `openRequest`.

## Risks and boundaries

- The context menu is rendered in a separate Tauri surface, so unit tests prove intent routing but
  not native window visibility or focus.
- Native acceptance requires the running Tauri development app: right-click Fairy, select
  `Game companion`, and confirm the main window is foregrounded with exactly one dialog.
- No Core, database, provider, or realtime contract changes are required.

## Evidence

- Vitest: `RealtimeCompanion.test.tsx`
- Vitest: `PresencePanel.test.tsx`
- TypeScript build
- Manual Tauri development-runtime check
