# Chat Line Sidebar Turn Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This repository's user instruction forbids subagents, so execution stays in the primary thread.

**Goal:** Replace per-message chat outline entries with one navigable entry per user request and Fairy response Turn, and show a detached Codex-style preview card for the exact hovered or focused exchange.

**Architecture:** `MessageLineSidebar` will project durable messages and the active stream into exchange records keyed by Turn ID, with safe adjacency fallback for legacy unscoped messages. `MessageList` will continue to own DOM anchors and scan-line activation, but each exchange will point to one start anchor. The sidebar remains a 36-pixel transparent scroll surface; one passive card is rendered outside that scroll surface and positioned beside the hovered or focused line.

**Tech Stack:** React 19, TypeScript 7, `motion/react`, Vitest with Testing Library, Playwright, Tauri 2 / Windows WebView2.

## Global Constraints

- Work only in `D:\桌面\~\deskllmchat\.worktrees\fairy-v3\fairy-v3`.
- Do not change Core, RPC, database, Ledger, Query Client, or public component contracts.
- Do not add a package or runtime dependency.
- Durable user messages, durable assistant messages, and the active streamed response are the only eligible exchange content.
- Pending or failed user messages, tool messages, system notices, Realtime transcript, and Activity Rail never create outline entries.
- Use `turn:<turn_id>` for scoped exchange identity and `exchange:<user_message_id>` for safe unscoped fallback.
- The title limit is 48 Unicode code points; the response limit is 120 Unicode code points.
- The sidebar itself remains transparent and approximately 36 pixels wide.
- The detached preview card is at most 360 pixels wide, clamps inside the chat viewport, and never changes transcript layout.
- The 96-pixel transcript scan line, 400-millisecond outline-follow resume, right native scrollbar, Jump to latest, and Conversation-scoped reset remain authoritative.
- Reduced Motion disables proximity translation, card transition, and smooth anchor navigation.
- Use `apply_patch` for source edits.
- Do not run Docker, Tauri release, or production builds.
- Stop every Node, Tauri, Cargo, Core, Voice, or Python process started during verification and remove `desktop/test-results`.

---

### Task 1: Project messages into Turn exchanges and migrate anchors

**Files:**
- Modify: `desktop/src/chat/MessageLineSidebar.tsx`
- Modify: `desktop/src/chat/MessageLineSidebar.test.tsx`
- Modify: `desktop/src/chat/MessageList.tsx`
- Modify: `desktop/src/chat/MessageList.outline.test.tsx`
- Modify: `desktop/src/chat/ChatWorkspace.test.tsx`

**Interfaces:**
- Produces:

```ts
export interface MessageLineItem {
  key: string;
  anchorKey: string;
  messageIds: readonly string[];
  title: string;
  response: string;
  streaming: boolean;
}

export function projectMessageLineItems(
  messages: Message[],
  streamedText: string,
  turnId: string | null,
): MessageLineItem[];

export function turnMessageLineKey(turnId: string): string;
export function legacyMessageLineKey(messageId: string): string;
```

- Preserves:

```ts
export function messageLineKey(messageId: string): string;
export function streamingMessageLineKey(turnId: string | null): string;
```

- `MessageList` consumes each item's `key` for active state and its `anchorKey` for scrolling.

- [ ] **Step 1: Replace projection expectations with exchange expectations**

Add focused cases to `MessageLineSidebar.test.tsx`:

```ts
it("groups one durable user request and assistant response by Turn", () => {
  const items = projectMessageLineItems(
    [
      message(1, "user", "Build the sidebar", TURN_ID),
      message(2, "assistant", "The sidebar is ready", TURN_ID),
    ],
    "",
    null,
  );

  expect(items).toEqual([
    expect.objectContaining({
      key: `turn:${TURN_ID}`,
      anchorKey: `message:${messageId(1)}`,
      messageIds: [messageId(1), messageId(2)],
      title: "Build the sidebar",
      response: "The sidebar is ready",
      streaming: false,
    }),
  ]);
});

it("keeps multiple assistant messages in one Turn exchange", () => {
  const items = projectMessageLineItems(
    [
      message(1, "user", "Inspect it", TURN_ID),
      message(2, "assistant", "First result", TURN_ID),
      message(3, "assistant", "Second result", TURN_ID),
    ],
    "",
    null,
  );

  expect(items).toHaveLength(1);
  expect(items[0]?.response).toBe("First result Second result");
});

it("updates the same Turn exchange from stream to durable response", () => {
  const streaming = projectMessageLineItems(
    [message(1, "user", "Inspect it", TURN_ID)],
    "Checking now",
    TURN_ID,
  );
  const durable = projectMessageLineItems(
    [
      message(1, "user", "Inspect it", TURN_ID),
      message(2, "assistant", "Inspection complete", TURN_ID),
    ],
    "Checking now",
    TURN_ID,
  );

  expect(streaming).toHaveLength(1);
  expect(streaming[0]).toMatchObject({
    key: `turn:${TURN_ID}`,
    response: "Checking now",
    streaming: true,
  });
  expect(durable).toHaveLength(1);
expect(durable[0]).toMatchObject({
    key: `turn:${TURN_ID}`,
    response: "Inspection complete",
    streaming: false,
  });
});

function messageId(sequence: number): string {
  return `019f7b34-9300-7000-8000-${(20 + sequence)
    .toString()
    .padStart(12, "0")}`;
}
```

Add tests for unscoped adjacency, orphan assistant messages, delayed durable users,
48/120-code-point limits, and `Fairy is responding…` / `Current request`
fallbacks. Update the local helper signature to:

```ts
function message(
  sequence: number,
  role: Message["role"],
  content: string,
  turnId: string | null,
): Message
```

- [ ] **Step 2: Run the projection tests and verify the old model fails**

Run:

```powershell
npx vitest run src/chat/MessageLineSidebar.test.tsx
```

Expected: FAIL because `MessageLineItem` still exposes role/label/excerpt and
returns one entry per message.

- [ ] **Step 3: Implement deterministic exchange projection**

Replace the per-message projection with a ledger-order builder:

```ts
interface MutableExchange {
  key: string;
  anchorKey: string;
  messageIds: string[];
  userText: string;
  assistantTexts: string[];
  streamedText: string;
  streaming: boolean;
}

export function turnMessageLineKey(turnId: string): string {
  return `turn:${turnId}`;
}

export function legacyMessageLineKey(messageId: string): string {
  return `exchange:${messageId}`;
}
```

For a scoped user message, create or fill `turn:<turn_id>` and set its anchor to
`message:<user_id>`. For a scoped assistant message, append only to the matching
Turn exchange; create a response-only Turn exchange when the user has not arrived.
For unscoped messages, a user opens `exchange:<user_id>` and following assistant
messages attach until the next user. An orphan unscoped assistant creates
`exchange:<assistant_id>` anchored to itself.

When `streamedText` is non-empty, update or create `turn:<active_turn_id>` and use
`stream:<active_turn_id>` only when no durable anchor exists. Durable assistant
text takes precedence over streamed text.

Finalize each item with:

```ts
const durableResponse = exchange.assistantTexts.join(" ").trim();
const streamedResponse = exchange.streamedText.trim();

{
  key: exchange.key,
  anchorKey: exchange.anchorKey,
  messageIds: exchange.messageIds,
  title: exchange.userText.trim()
    ? messageLineExcerpt(exchange.userText, "user", 48)
    : "Current request",
  response: durableResponse
    ? messageLineExcerpt(durableResponse, "assistant", 120)
    : streamedResponse
      ? messageLineExcerpt(streamedResponse, "assistant", 120)
      : "Fairy is responding…",
  streaming: exchange.assistantTexts.length === 0 && streamedResponse.length > 0,
}
```

Change `messageLineExcerpt` to accept an explicit length while preserving Markdown
cleanup and Unicode-safe `Array.from(...).slice(...)`.

- [ ] **Step 4: Migrate MessageList active state and navigation**

Keep registering durable message DOM refs under `message:<message_id>` and the
streaming row under `stream:<turn_id>`. Change scan-line lookup and navigation to
resolve an exchange item's `anchorKey`:

```ts
const lineItemByKey = useMemo(
  () => new Map(lineItems.map((item) => [item.key, item])),
  [lineItems],
);

const updateActiveLine = useCallback((list: HTMLDivElement | null) => {
  const scanLine = list.getBoundingClientRect().top + 96;
  let next = lineItems[0]?.key ?? null;
  for (const item of lineItems) {
    const anchor = anchorRefs.current.get(item.anchorKey);
    if (anchor === undefined) continue;
    if (anchor.getBoundingClientRect().top > scanLine) break;
    next = item.key;
  }
  setActiveLineKey((current) => current === next ? current : next);
}, [lineItems]);
```

`onNavigate` must look up `lineItemByKey.get(key)?.anchorKey` before calling
`scrollToAnchor`. Conversation changes continue to key the entire sidebar instance.

- [ ] **Step 5: Adapt the existing button presentation to the exchange fields**

Keep the current inline summary only as a compile-safe intermediate state until
Task 2 replaces it with the detached card:

```tsx
<button
  aria-current={active ? "location" : undefined}
  aria-label={`${item.title}: ${item.response}`}
  data-line-key={item.key}
  data-streaming={item.streaming ? "true" : undefined}
  onClick={() => onNavigate(item.key, !reducedMotion)}
>
  <span className="message-line-sidebar-mark-slot" aria-hidden="true">
    <m.span className="message-line-sidebar-mark" style={{ width, x }} />
  </span>
  <span className="message-line-sidebar-summary">
    <strong>{item.title}</strong>
    <span>{item.response}</span>
  </span>
</button>
```

Remove role-specific button data because one marker now represents both roles.

- [ ] **Step 6: Update integration tests for one entry per exchange**

In `MessageList.outline.test.tsx`, assert:

```ts
expect(screen.getAllByRole("button", { name: /Durable request/u })).toHaveLength(1);
fireEvent.click(
  screen.getByRole("button", {
    name: "Durable request: Durable answer",
  }),
);
expect(scrollTo).toHaveBeenCalledWith({ top: 332, behavior: "smooth" });
```

Add a three-exchange scan-line test proving the active key changes at user anchors,
not at assistant anchors. Update `ChatWorkspace.test.tsx` queries so the detached
outline accessible label may contain both the request and response while transcript
assertions remain scoped to `Conversation messages`.

- [ ] **Step 7: Run focused component tests and TypeScript**

Run:

```powershell
npx vitest run src/chat/MessageLineSidebar.test.tsx src/chat/MessageList.outline.test.tsx src/chat/ChatWorkspace.test.tsx
npx tsc --noEmit
```

Expected: all focused tests PASS and TypeScript exits 0.

- [ ] **Step 8: Review and commit the exchange projection**

Run:

```powershell
git diff --check
git diff -- desktop/src/chat/MessageLineSidebar.tsx desktop/src/chat/MessageList.tsx
```

Stage only the five Task 1 files and commit:

```powershell
git commit -m "refactor(desktop): group chat outline by turn"
```

### Task 2: Render one detached Codex-style preview card

**Files:**
- Modify: `desktop/src/chat/MessageLineSidebar.tsx`
- Modify: `desktop/src/chat/MessageLineSidebar.test.tsx`
- Modify: `desktop/src/chat/MessageLineSidebar.reduced.test.tsx`
- Modify: `desktop/src/app/workspace-chat.css`

**Interfaces:**
- Consumes `MessageLineItem` from Task 1.
- Produces one `.message-line-preview-card` with:

```tsx
<strong>{item.title}</strong>
<span>{item.response}</span>
```

- The card is present only for the item selected by pointer hover or keyboard focus.

- [ ] **Step 1: Write failing card interaction tests**

Add tests that render two exchange items and prove only the exact hovered/focused
item owns the card:

```ts
fireEvent.pointerEnter(
  screen.getByRole("button", { name: "First request: First response" }),
);
expect(screen.getByRole("tooltip")).toHaveTextContent(
  "First request",
);
expect(screen.getByRole("tooltip")).toHaveTextContent(
  "First response",
);

fireEvent.pointerEnter(
  screen.getByRole("button", { name: "Second request: Second response" }),
);
expect(screen.getAllByRole("tooltip")).toHaveLength(1);
expect(screen.getByRole("tooltip")).toHaveTextContent(
  "Second request",
);
```

Also assert pointer leave closes a non-focused card, focus keeps a card open after
pointer leave, blur closes it, and Conversation remount removes the old preview.

- [ ] **Step 2: Run the sidebar tests and verify card behavior fails**

Run:

```powershell
npx vitest run src/chat/MessageLineSidebar.test.tsx src/chat/MessageLineSidebar.reduced.test.tsx
```

Expected: FAIL because the current summary is rendered in every item and the entire
sidebar expands.

- [ ] **Step 3: Add item-level preview ownership**

Add refs for the navigation root and preview card plus item-level hover/focus
identity:

```ts
const navRef = useRef<HTMLElement>(null);
const previewRef = useRef<HTMLDivElement>(null);
const hoveredItemRef = useRef<string | null>(null);
const focusedItemRef = useRef<string | null>(null);
const [previewKey, setPreviewKey] = useState<string | null>(null);
const [previewTop, setPreviewTop] = useState(8);
const [previewMaxWidth, setPreviewMaxWidth] = useState(360);
```

Button pointer enter/leave and focus/blur callbacks update the two refs. Focus takes
precedence over hover. Render one card as a sibling of the independent scroller:

```tsx
{previewItem === undefined ? null : (
  <m.div
    ref={previewRef}
    className="message-line-preview-card"
    role="tooltip"
    style={{ top: previewTop, maxWidth: previewMaxWidth }}
  >
    <strong>{previewItem.title}</strong>
    <span>{previewItem.response}</span>
  </m.div>
)}
```

The card uses `pointer-events: none`. Recompute its top on item selection, outline
scroll, and parent resize. Clamp with:

```ts
const top = Math.min(
  Math.max(8, itemRect.top - navRect.top - 8),
  Math.max(8, navRect.height - cardHeight - 8),
);
const maxWidth = Math.max(180, Math.min(360, shellRect.right - navRect.left - 60));
```

Close the card if the selected item's bounds no longer intersect the navigation
viewport.

- [ ] **Step 4: Replace expanded-sidebar CSS with a detached card**

Keep the navigation and scroller exactly 36 pixels wide. Remove the
`:hover`/`:focus-within` width expansion and per-item inline summary rules. Add:

```css
.message-line-sidebar {
  width: 36px;
  overflow: visible;
  background: transparent;
}

.message-line-sidebar-scroller {
  width: 36px;
  overflow-x: hidden;
}

.message-line-preview-card {
  position: absolute;
  z-index: 2;
  left: 46px;
  width: 360px;
  display: grid;
  gap: 7px;
  padding: 12px 14px;
  pointer-events: none;
  border: 1px solid #3a3d3b;
  border-radius: 14px;
  background: #2b2c2b;
  box-shadow: 0 14px 34px rgb(0 0 0 / 24%);
}

.message-line-preview-card strong {
  overflow: hidden;
  color: #f1f3f1;
  font-size: 13px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-line-preview-card span {
  display: -webkit-box;
  overflow: hidden;
  color: #aeb3af;
  font-size: 12px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
```

Use Fairy's existing dark palette while preserving the reference's detached rounded
surface. Keep the line stack neutral and make only the active line cyan. In the
Reduced Motion media query, remove card opacity/translation transitions and the
streaming pulse.

- [ ] **Step 5: Verify focused card, Reduced Motion, and sizing**

Run:

```powershell
npx vitest run src/chat/MessageLineSidebar.test.tsx src/chat/MessageLineSidebar.reduced.test.tsx
npx tsc --noEmit
```

Expected: PASS. The Reduced Motion test must still expect immediate navigation and
the Playwright Reduced Motion scenario in Task 3 must assert the card transition
duration is `0s`.

- [ ] **Step 6: Review and commit the detached card**

Run:

```powershell
git diff --check
git diff -- desktop/src/chat/MessageLineSidebar.tsx desktop/src/app/workspace-chat.css
```

Stage only the four Task 2 files and commit:

```powershell
git commit -m "feat(desktop): add exchange preview cards"
```

### Task 3: Update acceptance fixtures and run full visual regression

**Files:**
- Modify: `docs/acceptance/chat-line-sidebar.md`
- Modify: `desktop/e2e/support/coreFixture.ts`
- Modify: `desktop/e2e/chat-workspace.spec.ts`
- Modify: `desktop/e2e/release.spec.ts` only if its scoped transcript assertion needs adjustment
- Create during visual QA: `desktop/design-qa.md`

**Interfaces:**
- The seeded long Conversation contains 15 Turn exchanges made from 30 durable
  messages.
- The Playwright-visible item name is `<request title>: <response excerpt>`.
- The Line Sidebar is transparent; `.message-line-preview-card` owns the only
  expanded surface.

- [ ] **Step 1: Update the acceptance contract before changing E2E assertions**

Replace per-message invariants with:

```markdown
- One outline marker represents one durable user-led Turn exchange.
- The request title and every same-Turn assistant response appear in one preview.
- A streamed response updates its Turn exchange and durable completion replaces it
  without creating another marker.
- Hover or keyboard focus opens one detached preview card; the Line Sidebar itself
  stays transparent and 36 pixels wide.
```

Update the state ownership table so item identity is Turn/exchange key and anchor
identity remains a durable message or stream DOM key.

- [ ] **Step 2: Seed explicit Turn pairs in the long-conversation fixture**

In `coreFixture.ts`, assign the same non-null Turn ID to each user/assistant pair:

```ts
const pairIndex = Math.floor(index / 2) + 1;
const turnId =
  `0198f4de-0114-7000-8000-${String(500 + pairIndex).padStart(12, "0")}`;

return {
  ...scratchMessage,
  id: messageId,
  turn_id: turnId,
  sequence,
  role,
  content: role === "user"
    ? `Outline request ${String(sequence).padStart(2, "0")} — stable message anchor`
    : `Outline response ${String(sequence).padStart(2, "0")} — stable message anchor`,
};
```

- [ ] **Step 3: Rewrite the long-conversation Playwright expectations**

Expect 15 buttons rather than 30. Hover the first marker and assert:

```ts
await expect(outline.getByRole("button")).toHaveCount(15);
await first.hover();
const card = page.getByRole("tooltip");
await expect(card).toContainText("Outline request 01");
await expect(card).toContainText("Outline response 02");

expect(await outline.evaluate((element) => ({
  width: element.getBoundingClientRect().width,
  background: getComputedStyle(element).backgroundColor,
}))).toEqual({
  width: 36,
  background: "rgba(0, 0, 0, 0)",
});
await expect(card).toHaveCSS("background-color", "rgb(43, 44, 43)");
```

Check the card bounds against the `message-list-shell` bounds at both 880x680 and
640x700. Click the first exchange and retain the existing transcript scroll,
`aria-current`, outline browse-pause, resume-follow, Jump to latest, scrollbar, and
document-overflow assertions.

- [ ] **Step 4: Run focused component and chat E2E suites**

Run:

```powershell
npx vitest run src/chat/MessageLineSidebar.test.tsx src/chat/MessageLineSidebar.reduced.test.tsx src/chat/MessageList.outline.test.tsx src/chat/ChatWorkspace.test.tsx
npx playwright test e2e/chat-workspace.spec.ts
```

Expected: all focused tests PASS, including 880x680, 640x700, long Conversation,
focus, and Reduced Motion scenarios.

- [ ] **Step 5: Perform screenshot-based design QA**

Open the supplied reference:

```text
C:\Users\54487\AppData\Local\Temp\codex-clipboard-4654e20c-9ddd-4521-b76e-5851839e9837.png
```

Open the generated 880x680 expanded-card screenshot from `desktop/test-results`.
Compare the same hovered state for marker density, detached-card placement, corner
radius, title/body hierarchy, absence of sidebar background, and overlap behavior.
Write `desktop/design-qa.md` with:

```markdown
# Chat Line Sidebar Design QA

Reference: Codex turn preview screenshot supplied by the user.
Implementation: Fairy 880x680 hovered exchange screenshot.

## Findings

- P0: none
- P1: none
- P2: none
- P3: list only non-blocking polish differences

final result: passed
```

If any P0/P1/P2 exists, fix it in Task 2 files, recapture the same viewport and
state, then replace the QA findings only after the issue is resolved.

- [ ] **Step 6: Run the complete Desktop regression once**

Run in this order:

```powershell
npx tsc --noEmit
npm test -- --run
npx playwright test --workers=1
```

Expected:

- TypeScript exits 0.
- All Desktop Vitest files and tests pass.
- All Playwright functional and performance tests pass in the single controlled
  Vite lifecycle.

- [ ] **Step 7: Run one native WebView2 smoke**

When the user is not interacting with the Fairy window:

```powershell
npm run tauri -- dev
```

Verify one visible Fairy main window, `CORE READY`, one marker per exchange, detached
preview card on hover/focus, correct navigation, and the ordinary right transcript
scrollbar. Confirm Voice Worker, Realtime, and Game Companion do not preheat.

Terminate the exact Tauri dev process tree started by this step. Do not stop
unrelated Node or Python processes.

- [ ] **Step 8: Clean generated artifacts and confirm no process residue**

Resolve `desktop/test-results` to an absolute path, verify it is inside the Desktop
project root, and remove it recursively. Check for Fairy, Cargo, Core, Voice, Python,
and task-started Node processes. `desktop/design-qa.md` is durable review evidence
and remains in the commit.

- [ ] **Step 9: Commit acceptance and regression evidence**

Run:

```powershell
git diff --check
git status --short
```

Stage only Task 3 files plus any Task 2 correction required by design QA and commit:

```powershell
git commit -m "test(desktop): verify grouped chat outline behavior"
```

- [ ] **Step 10: Final clean-state audit**

Run:

```powershell
git status --short
git log -4 --oneline
```

Expected: no status entries. Report the design, implementation, and regression
commits; exact TypeScript, Vitest, Playwright, and native smoke results; every
modified existing test and why; skipped Docker/release/provider checks; artifact
cleanup; and process cleanup.
