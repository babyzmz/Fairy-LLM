# Chat Line Sidebar

## Scope

This acceptance specification covers the left-side message outline, message-anchor
navigation, long-Conversation outline following, and the visual treatment of the
existing right-side transcript scrollbar.

## Observable invariants

- The outline is inside the chat viewport and stays visible while messages scroll.
- The ordinary transcript scrollbar remains on the right and keeps native wheel,
  drag, keyboard, and touch behavior.
- One outline marker represents one durable user-led Turn exchange.
- The request title and every same-Turn assistant response appear in one preview.
- The current streamed assistant response updates its Turn exchange until durable
  completion replaces it without creating another marker.
- Tool messages, system notices, Realtime transcripts, Activity Rail, and optimistic
  or failed user messages never create outline items.
- Idle items render as compact neutral lines. Hover or keyboard focus opens one
  detached request-and-response card without shifting the transcript.
- The Line Sidebar itself stays transparent and 36 pixels wide while the detached
  card owns the expanded surface.
- Pointer proximity affects only nearby line length and horizontal offset.
- The current message is exposed with `aria-current="location"`.
- Click, Enter, and Space navigate to the correct message anchor.
- Reduced Motion disables proximity transitions and smooth scrolling.
- The detached card remains inside constrained chat widths and creates no page
  horizontal overflow.
- Long outlines can be browsed independently. Automatic active-item following pauses
  during outline interaction and resumes 400 milliseconds after interaction ends.
- Switching Conversations removes every old item, anchor, active marker, and paused
  browsing state.

## State ownership

| State | Owner | Scope key | Lifetime |
| --- | --- | --- | --- |
| Outline items | `MessageList` projection | Turn / legacy exchange key | Render |
| Message anchors | `MessageList` DOM map | durable message / stream key | Mounted Conversation projection |
| Active item | `MessageList` scroll projection | current item set | Mounted projection |
| Pointer proximity | `MessageLineSidebar` motion values | outline instance | Pointer interaction |
| Outline browsing pause | `MessageLineSidebar` | outline instance | Interaction plus 400 ms |
| Transcript following | Existing `MessageList` ref | transcript scroll surface | Mounted list |

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
| --- | --- | --- | --- |
| Mixed ledger roles | One marker groups each eligible user-led Turn | Tool or system outline items | Component test |
| Multiple responses | Same-Turn assistant messages share one preview | One marker per response | Component test |
| Streaming completes | Stream updates and becomes one durable exchange | Duplicate Turn markers | Component test |
| Legacy messages | Only safely adjacent unscoped messages pair | Cross-request or cross-Turn pairing | Component test |
| Markdown and Unicode | Readable 48/120-code-point excerpts | Broken surrogate or raw fence prefix | Unit test |
| Click early item | User request start moves to transcript start | Assistant midpoint or wrong Conversation | Component and Playwright |
| Keyboard navigation | Focus opens the detached card; Enter/Space navigates | Pointer-only control | Component and Playwright |
| Long Conversation | Outline can browse, then resumes following | Forced snap during interaction | Component and Playwright |
| Rapid Conversation switch | Only the second item set remains | Stale labels, refs, or active marker | Component test |
| 640-pixel window | Detached card stays bounded and Composer usable | Horizontal page overflow | Playwright screenshot and bounds |
| Reduced Motion | Immediate navigation and static proximity | Repeated or smooth animation | Component and Playwright |
| WebView2 scrollbar | Styled right thumb with normal behavior | Replacement custom scroller | Real Tauri dev smoke |

## Automation boundaries

- Vitest can prove projection, state reset, accessibility attributes, scroll calls,
  timers, and reduced-motion choices with controlled DOM geometry.
- Playwright can prove browser hit targets, focus, viewport bounds, scrolling, and
  visible styling in Chromium.
- Only a real Tauri WebView2 run can confirm the exact Windows scrollbar rendering.
  It is a visual smoke, not evidence for native window lifecycle or DPI behavior.

## Required verification

1. TypeScript structural preflight.
2. Direct Line Sidebar and MessageList component tests.
3. Full Desktop Vitest regression.
4. Chat-focused Playwright at 880x680 and 640x700.
5. Full Playwright regression using the controlled shared Vite lifecycle.
6. One Tauri dev WebView2 smoke when no user input competes with automation.
7. Confirm no Node, Tauri, Core, Voice, Python, or Cargo process remains afterward.
