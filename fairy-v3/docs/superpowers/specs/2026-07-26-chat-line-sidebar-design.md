# Chat Line Sidebar Design

## Goal

Add a lightweight conversation outline to the left edge of the existing chat
viewport while keeping the ordinary message scrollbar on the right. The outline
borrows the cursor-proximity language of React Bits Line Sidebar, but uses Fairy's
restrained dark palette and existing `motion/react` dependency.

The feature is navigation for the current Conversation, not a second history list
and not a minimap. It must not own project, Conversation, Turn, or persistence state.

## Content model

The outline contains, in ledger order:

- durable `user` messages;
- durable `assistant` messages; and
- the current streamed assistant response when no durable response for that Turn
  exists yet.

Tool messages, `system_notice` messages, Realtime transcript cards, Activity Rail,
and optimistic or failed user messages are excluded. A streamed item uses the active
Turn ID as its stable identity and disappears when the durable assistant message
replaces it, so the outline never renders both projections.

Each item shows its role and the first 48 Unicode code points of visible content.
Common Markdown structure is reduced to readable text and whitespace is collapsed.
An empty durable message receives a role-specific fallback instead of disappearing.

## Layout and motion

`MessageList` owns a relative shell containing the scrollable transcript and an
absolutely positioned left outline. The outline occupies about 36 pixels when idle,
so it does not materially narrow the existing 900-pixel message measure. Hover or
keyboard focus reveals a one-line summary layer over the left edge of the messages;
it never changes transcript layout or creates document-level horizontal overflow.

Each item is a real button. Its base line is 12 pixels wide. The active item and
items close to the pointer extend up to 26 pixels and translate up to 4 pixels. User
lines use Fairy's warm gray; assistant lines use the existing cyan-green. Motion
values drive pointer proximity without React state updates on every pointer event.
`prefers-reduced-motion` removes proximity animation and smooth scrolling.

On narrow layouts the same overlay remains available, capped at the chat width minus
56 pixels. Touch activation navigates immediately; hover and keyboard focus reveal
the summary.

## Navigation and synchronization

Message rows register their DOM anchors with the list. The existing transcript
scroll handler also determines the active outline item at a scan line 96 pixels
below the scroll viewport's top. Activating an item scrolls its anchor to the start
of the viewport, using smooth scrolling unless reduced motion is requested.

The outline has a hidden, independent vertical scrollbar for long Conversations.
It normally keeps the active item visible. Pointer hover, focus, or wheel interaction
pauses that automatic following so the user can browse the outline. Following
resumes 400 milliseconds after pointer and focus leave the outline.

Changing the item identity set clears stale anchor and browsing state. No outline
scroll position is persisted across Conversations or process restarts.

## Scrollbar styling

The transcript remains the sole primary vertical scroll surface. Its right scrollbar
uses a 10-pixel Chromium/WebView track, a rounded low-contrast gray-green thumb,
brighter hover and cyan-green active states, plus `scrollbar-width` and
`scrollbar-color` fallbacks. The existing stable gutter remains in place.

## Boundaries

- No Core, RPC, schema, database, Ledger, or Query Client change.
- No new package or runtime dependency.
- `MessageLineSidebar` and its item model stay internal to the chat module.
- Existing following-to-latest, Jump to latest, Realtime transcript, Activity Rail,
  Composer, and Conversation caching behavior remain authoritative.

## Verification

Component tests cover item projection, excerpts, streaming replacement, anchor
navigation, active tracking, reduced motion, long-outline browsing, and rapid
Conversation replacement. Playwright covers hover and focus expansion, constrained
widths, navigation, overflow, scrollbar ownership, and reduced motion. A real Tauri
dev smoke confirms the final scrollbar and focus behavior in Windows WebView2.
