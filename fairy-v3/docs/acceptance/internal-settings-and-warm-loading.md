# Internal Settings And Warm Loading

## Scope

This acceptance specification covers the move from a standalone Settings WebView to
an internal full-page main-window view, preservation of the warm Workspace, and the
removal of global loading states caused by local queries.

The work is complete only when the React state, query cache, native main HWND, and
external Settings navigation all preserve their documented ownership boundaries.

## Observable invariants

### Internal Settings view

- Settings renders inside the existing main WebView and occupies the full client area.
- Workspace remains mounted while Settings is visible, but is hidden, inert, and
  excluded from focus and pointer interaction.
- Returning restores the previously selected conversation, message scroll position,
  Composer draft, inspector tab, Preview identity, and active Turn projection.
- Settings mounts on first use and remains mounted after returning to Workspace.
- The Back button restores focus to the element that opened Settings.
- Escape returns only when no dialog, menu, or editable control owns the interaction.
- A model-settings deep link opens `Models`; a generic Settings request opens the last
  in-process category or `General` on first use.

### Native lifecycle

- No `settings` Tauri WebView, window configuration, or capability remains.
- Tray, pet, and main-workspace Settings actions show and focus the existing main
  HWND, then deliver a sequenced `MainViewRequest`.
- A request made before the main React listener is ready is recovered from native
  pending state exactly once.
- With `minimize_to_tray=true`, closing the main window hides it without destroying
  the HWND, WebView, CoreClient, QueryClient, subscriptions, or in-memory drafts.
- With `minimize_to_tray=false`, closing the main window exits Fairy instead of
  leaving only background surfaces alive.

### Settings query ownership

- Shared Desktop Preferences are loaded once and updated through the existing native
  change event.
- Opening `General` requests only archived projects and trash data.
- `Appearance` and `Advanced` require no Core category request.
- Models, Voice, Permissions, Skills/MCP, Knowledge, and Pet load only after their
  category is first shown.
- Reopening an already loaded category displays cached data immediately and does not
  duplicate fresh requests.
- Refresh and mutations invalidate only the active or affected category.
- Voice health reads never start the Voice Worker.

### Workspace loading ownership

- Core connection, history navigation, conversation content, and project content have
  independent states.
- A first Core startup with no cached data shows the stable application frame and a
  compact `Core starting` status, not a full-page connecting replacement.
- Loading messages for a newly selected conversation replaces only the message region.
- A late response from conversation A never renders under selected conversation B.
- Returning to a cached conversation displays its messages immediately.
- Preview, Files, Outputs, and Obsidian retain independent loading and error surfaces.
- Hovering or focusing a history row prefetches only that Conversation.
- Ledger events invalidate only their owning domain and are coalesced when repeated.

## State ownership

| State | Owner | Scope key | Lifetime |
| --- | --- | --- | --- |
| Main view | App root | main WebView | Process |
| Last Settings category | App root | main WebView | Process |
| Workspace selection and draft | Workspace subtree | conversation/workspace | Mounted process |
| Settings category data | React Query | category plus relevant scope | Query cache |
| Core connection | Workspace model | transport instance | Process |
| History loading | Workspace model | project/conversation collections | Query |
| Conversation content | React Query | conversation ID | Query cache |
| Preview content | Inspector queries | task/workspace/version | Query cache |
| Pending native navigation | Rust main-view state | monotonic sequence | Until acknowledged |

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
| --- | --- | --- | --- |
| Open and return | Workspace DOM instance and local state survive | Workspace remount or draft loss | React integration test |
| Settings deep link | Requested category opens once | General flash or lost request | React plus Rust tests |
| Category request count | General does not call unrelated domains | Eager 18-request fan-out | Settings client call log |
| Rapid chat switch | Only selected chat content appears | Previous-chat flash | Controlled query test |
| Cached return | Cached messages appear without full loading | Whole Workspace replacement | React Query integration |
| Close and reopen | Same main HWND and WebView are reused | Core reconnect or new HWND | Real Tauri/Win32 evidence |
| Tray or pet Settings | Existing main window opens internal Settings | Separate Settings window | Real Tauri evidence |
| Process-exit preference | Main close exits when tray mode is disabled | Background-only Fairy | Real Tauri evidence |

## Automation boundaries

- Vitest can prove React mounting, focus, query keys, cache use, request counts, and
  late-response fencing.
- Rust tests can prove window configuration removal, authorization policy, pending
  request sequencing, and close-policy decisions.
- Playwright can prove the internal full-page view and browser-level focus behavior.
- Only a real Tauri runtime can prove HWND identity, WebView reuse, tray behavior,
  native focus, and process exit. Vite or browser tests cannot substitute for it.

## Pre-fix evidence

- `desktop/src/main.tsx` has a `surface=settings` branch that creates a new
  `SettingsClient` and React root.
- `desktop/src-tauri/src/lib.rs` creates a standalone `settings` window and authorizes
  Settings native commands only for that label.
- `desktop/src/settings/SettingsApp.tsx` loads every settings domain in one
  `Promise.all`, then replaces the full surface with `Connecting to Fairy Core`.
- `desktop/src/app/workspaceModel.ts` includes `messagesQuery.isPending` in the global
  Workspace loading calculation.
- The main close event is not intercepted, so reopening recreates the main WebView.

## Required verification

1. Direct App, Settings, Workspace model, and transport unit tests.
2. Rust window-policy and request-sequence tests.
3. Relevant desktop regression suite and TypeScript.
4. Clippy for the desktop crate.
5. Playwright internal Settings and rapid chat switching.
6. One real Tauri dev run covering main, tray, pet, close/reopen, deep link, and HWND
   identity. Record any unavailable native scenario as unverified.

