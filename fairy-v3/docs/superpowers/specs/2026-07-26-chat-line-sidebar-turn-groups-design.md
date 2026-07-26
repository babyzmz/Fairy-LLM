# Chat Line Sidebar Turn Groups Design

## Status and scope

This design is an approved increment to
`2026-07-26-chat-line-sidebar-design.md`. It changes the outline projection and
expanded presentation only. The existing transcript, right-side native scrollbar,
message rendering, Composer, Activity Rail, Realtime transcript, Core contracts,
and persistence boundaries remain unchanged.

The visual reference is Codex's conversation outline: a permanently narrow stack
of lines with one detached preview card for the exact hovered or focused entry.
The Line Sidebar itself remains transparent.

## Exchange model

The outline represents user-led exchanges instead of individual messages. One
exchange contains:

- one durable user request;
- every durable assistant response that belongs to the same Turn; and
- the current streamed assistant response while that Turn is still running.

The stable identity is `turn:<turn_id>` when a Turn ID exists. Its navigation anchor
is the durable user message at the beginning of the Turn. When legacy messages have
no Turn ID, a durable user message opens an `exchange:<message_id>` group and
following unscoped assistant messages join it until the next durable user message.
An assistant message that cannot be paired safely becomes a response-only exchange
anchored to itself rather than being attached to the wrong request.

Pending and failed user messages remain excluded. If a streamed response arrives
before its durable user request is visible, it temporarily forms a response-only
Turn exchange. The durable user request fills the same `turn:<turn_id>` group when
the ledger projection catches up, so no duplicate item appears.

Multiple durable assistant messages in one Turn remain one outline entry. Their
readable text is joined in ledger order for the preview. A durable assistant
response replaces the streamed preview for that Turn in place.

## Preview content

Each exchange exposes two separately bounded excerpts:

- `title`: the first 48 Unicode code points of the durable user request;
- `response`: the first 120 Unicode code points of the joined durable assistant
  response or current streamed response.

Both excerpts collapse whitespace and remove common Markdown presentation markers.
The title fallback is `Current request` for a response-only exchange. A durable
request without a response uses `Fairy is responding…`. An empty durable assistant
response uses `Response`.

The accessible name combines both parts without exposing hidden tool or system
content. The outline item retains `aria-current="location"` when active.

## Visual behavior

The idle outline remains approximately 36 pixels wide and has no panel background,
border, or shadow. It renders one neutral short line per exchange. The active line
uses Fairy's restrained cyan; pointer-near lines retain the existing proximity
length and horizontal offset. Streaming state may pulse only when Reduced Motion is
not requested.

Hovering or keyboard-focusing one line opens a passive preview card to its right:

- the card is detached from the Line Sidebar rather than an expanded sidebar
  background;
- it has a dark neutral surface, subtle one-pixel border, restrained shadow, and
  rounded corners matching the Codex reference without copying its exact palette;
- the title is one emphasized line with ellipsis;
- the response is clamped to three lines in a lower-contrast color;
- only one card is visible at a time;
- the card does not accept pointer events and never changes transcript layout.

The card is at most 360 pixels wide and is capped to the chat viewport width minus
the line gutter and a 16-pixel safety inset. Its vertical position follows the
hovered or focused line and is clamped inside the chat viewport. The independent
outline scroller remains 36 pixels wide with a hidden scrollbar, so the floating
card cannot create a second visible scroll surface.

Reduced Motion removes proximity, card translation, and smooth anchor scrolling.
Opacity may change immediately.

## Navigation and active state

Every exchange registers one start anchor. Activating its button with pointer,
Enter, or Space scrolls the durable user request to the transcript start offset. A
response-only exchange scrolls to its assistant anchor.

The 96-pixel transcript scan line selects the latest exchange whose start anchor
has crossed the scan line. This keeps the same exchange active while the reader
moves through its user request, work rail, and assistant response. When the next
user request crosses the scan line, that next exchange becomes active.

Long-conversation following, the 400-millisecond browse pause, Conversation-scoped
reset, and non-persisted outline scroll position retain their existing behavior.
The preview card closes when its item leaves the outline viewport, the Conversation
changes, or neither pointer nor keyboard focus remains on that item.

## Component boundaries

`MessageList` continues to own transcript DOM anchors and active-item projection.
It maps every durable user or assistant message to an exchange key and registers
only the exchange's start element for navigation.

`MessageLineSidebar` owns:

- the internal exchange item type;
- deterministic message-to-exchange projection helpers;
- pointer proximity Motion values;
- hovered or focused exchange identity;
- independent outline following; and
- the detached preview card's bounded position.

No public component interface, Core/RPC contract, Ledger event, database schema,
Query Client key, or runtime dependency changes.

## Verification

Component tests must prove:

- one Turn produces one entry from its user and assistant messages;
- multiple assistant messages remain in one entry and preserve ledger order;
- an active stream updates the same entry and is replaced without duplication;
- unscoped legacy messages pair only by safe adjacency;
- orphan assistant and delayed durable user cases never attach to the wrong Turn;
- title and response Markdown cleanup, Unicode limits, and fallbacks;
- click and keyboard navigation target the exchange start;
- the active exchange changes only at the next exchange anchor;
- Conversation replacement clears old groups, anchors, card, and browsing state;
- Reduced Motion uses immediate navigation and card presentation.

Playwright must prove at 880x680 and 640x700:

- the line stack has one marker per exchange;
- only the hovered or focused item opens a detached preview card;
- the Line Sidebar remains transparent while the card has its own surface;
- the card stays inside the chat viewport without horizontal page overflow;
- long-outline browsing, automatic following, Jump to latest, and the right
  transcript scrollbar do not regress.

A final Tauri dev smoke confirms the detached card, focus behavior, and native
right-side scrollbar in Windows WebView2. No Docker, release, or production build is
required for this increment.
