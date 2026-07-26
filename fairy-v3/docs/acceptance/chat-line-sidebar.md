# Chat Line Sidebar

## Scope

This acceptance specification covers the left-side message outline, message-anchor
navigation, long-Conversation outline following, and the visual treatment of the
existing right-side transcript scrollbar.

## Observable invariants

- The outline is inside the chat viewport and stays visible while messages scroll.
- The ordinary transcript scrollbar remains on the right and keeps native wheel,
  drag, keyboard, and touch behavior.
- Durable user and assistant messages appear exactly once and in ledger order.
- The current streamed assistant response appears exactly once until its durable
  message replaces it.
- Tool messages, system notices, Realtime transcripts, Activity Rail, and optimistic
  or failed user messages never create outline items.
- Idle items render as compact role-colored lines. Hover or keyboard focus reveals
  role plus a one-line starting excerpt without shifting the transcript.
- Pointer proximity affects only nearby line length and horizontal offset.
- The current message is exposed with `aria-current="location"`.
- Click, Enter, and Space navigate to the correct message anchor.
- Reduced Motion disables proximity transitions and smooth scrolling.
- The expanded overlay remains inside constrained chat widths and creates no page
  horizontal overflow.
- Long outlines can be browsed independently. Automatic active-item following pauses
  during outline interaction and resumes 400 milliseconds after interaction ends.
- Switching Conversations removes every old item, anchor, active marker, and paused
  browsing state.

## State ownership

| State | Owner | Scope key | Lifetime |
| --- | --- | --- | --- |
| Outline items | `MessageList` projection | message ID / active Turn ID | Render |
| Message anchors | `MessageList` DOM map | item key | Mounted Conversation projection |
| Active item | `MessageList` scroll projection | current item set | Mounted projection |
| Pointer proximity | `MessageLineSidebar` motion values | outline instance | Pointer interaction |
| Outline browsing pause | `MessageLineSidebar` | outline instance | Interaction plus 400 ms |
| Transcript following | Existing `MessageList` ref | transcript scroll surface | Mounted list |

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
| --- | --- | --- | --- |
| Mixed ledger roles | Only user and assistant anchors appear | Tool or system outline items | Component test |
| Streaming completes | Temporary response becomes one durable item | Duplicate response anchors | Component test |
| Markdown and Unicode | Readable 48-code-point excerpt | Broken surrogate or raw fence prefix | Unit test |
| Click early item | Correct row moves to transcript start | Document scroll or wrong Conversation | Component and Playwright |
| Keyboard navigation | Focus reveals label; Enter/Space navigates | Pointer-only control | Component and Playwright |
| Long Conversation | Outline can browse, then resumes following | Forced snap during interaction | Component and Playwright |
| Rapid Conversation switch | Only the second item set remains | Stale labels, refs, or active marker | Component test |
| 640-pixel window | Overlay stays bounded and Composer usable | Horizontal page overflow | Playwright screenshot and bounds |
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
