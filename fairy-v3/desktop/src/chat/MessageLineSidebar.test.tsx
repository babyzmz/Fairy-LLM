import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Message } from "../core/client";
import {
  messageLineExcerpt,
  MessageLineSidebar,
  projectMessageLineItems,
} from "./MessageLineSidebar";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("MessageLineSidebar", () => {
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
      {
        key: `turn:${TURN_ID}`,
        anchorKey: `message:${messageId(1)}`,
        messageIds: [messageId(1), messageId(2)],
        title: "Build the sidebar",
        response: "The sidebar is ready",
        streaming: false,
      },
    ]);
  });

  it("keeps multiple assistant messages in one Turn and filters non-chat roles", () => {
    const items = projectMessageLineItems(
      [
        message(1, "user", "Inspect it", TURN_ID),
        message(2, "tool", "private tool payload", TURN_ID),
        message(3, "assistant", "First result", TURN_ID),
        message(4, "system_notice", "Core notice", TURN_ID),
        message(5, "assistant", "Second result", TURN_ID),
      ],
      "",
      null,
    );

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      key: `turn:${TURN_ID}`,
      messageIds: [messageId(1), messageId(3), messageId(5)],
      response: "First result Second result",
      streaming: false,
    });
  });

  it("updates one Turn exchange from stream to durable response without duplication", () => {
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

  it("pairs legacy messages only by safe adjacency and preserves orphan responses", () => {
    const items = projectMessageLineItems(
      [
        message(1, "assistant", "Orphan response", null),
        message(2, "user", "Legacy request", null),
        message(3, "assistant", "Legacy response", null),
        message(4, "assistant", "Follow-up detail", null),
        message(5, "user", "Next request", null),
        message(6, "assistant", "Next response", null),
      ],
      "",
      null,
    );

    expect(items).toEqual([
      expect.objectContaining({
        key: `exchange:${messageId(1)}`,
        anchorKey: `message:${messageId(1)}`,
        title: "Current request",
        response: "Orphan response",
      }),
      expect.objectContaining({
        key: `exchange:${messageId(2)}`,
        anchorKey: `message:${messageId(2)}`,
        title: "Legacy request",
        response: "Legacy response Follow-up detail",
      }),
      expect.objectContaining({
        key: `exchange:${messageId(5)}`,
        anchorKey: `message:${messageId(5)}`,
        title: "Next request",
        response: "Next response",
      }),
    ]);
  });

  it("fills a response-only Turn when the durable user request arrives later", () => {
    const responseOnly = projectMessageLineItems(
      [message(2, "assistant", "Already responding", TURN_ID)],
      "",
      null,
    );
    const complete = projectMessageLineItems(
      [
        message(1, "user", "Delayed request", TURN_ID),
        message(2, "assistant", "Already responding", TURN_ID),
      ],
      "",
      null,
    );

    expect(responseOnly).toEqual([
      expect.objectContaining({
        key: `turn:${TURN_ID}`,
        anchorKey: `message:${messageId(2)}`,
        title: "Current request",
      }),
    ]);
    expect(complete).toEqual([
      expect.objectContaining({
        key: `turn:${TURN_ID}`,
        anchorKey: `message:${messageId(1)}`,
        title: "Delayed request",
      }),
    ]);
  });

  it("creates readable independently bounded title and response excerpts", () => {
    expect(
      messageLineExcerpt(
        "## [Fairy link](https://example.com)\n\n```ts\nconst ready = true;\n```",
        "assistant",
        120,
      ),
    ).toBe("Fairy link const ready = true;");
    expect(messageLineExcerpt("   ", "user")).toBe("Attachment message");
    expect(messageLineExcerpt("", "assistant")).toBe("Response");
    expect(Array.from(messageLineExcerpt("🧚".repeat(60), "assistant"))).toHaveLength(48);
    expect(
      Array.from(messageLineExcerpt("答".repeat(140), "assistant", 120)),
    ).toHaveLength(120);

    const noResponse = projectMessageLineItems(
      [message(1, "user", "Waiting request", TURN_ID)],
      "",
      null,
    );
    expect(noResponse[0]?.response).toBe("Fairy is responding…");
  });

  it("exposes the active location and navigates with its stable key", () => {
    const onNavigate = vi.fn();
    render(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="turn:first"
        onNavigate={onNavigate}
      />,
    );

    expect(screen.getByRole("navigation", { name: "Conversation outline" })).toBeVisible();
    expect(screen.getByRole("button", { name: "First request: First response" }))
      .toHaveAttribute("aria-current", "location");
    fireEvent.click(screen.getByRole("button", { name: "Second request: Second response" }));
    expect(onNavigate).toHaveBeenCalledWith("turn:second", true);
  });

  it("shows one detached preview for the exact hovered or focused exchange", () => {
    const view = render(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="turn:first"
        onNavigate={vi.fn()}
      />,
    );
    const first = screen.getByRole("button", {
      name: "First request: First response",
    });
    const second = screen.getByRole("button", {
      name: "Second request: Second response",
    });

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.pointerEnter(first);
    expect(screen.getByRole("tooltip")).toHaveTextContent("First request");
    expect(screen.getByRole("tooltip")).toHaveTextContent("First response");

    fireEvent.pointerEnter(second);
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Second request");

    fireEvent.pointerLeave(second);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.focus(first);
    fireEvent.pointerEnter(first);
    fireEvent.pointerLeave(first);
    expect(screen.getByRole("tooltip")).toHaveTextContent("First response");
    fireEvent.blur(first, { relatedTarget: document.body });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.pointerEnter(second);
    view.rerender(
      <MessageLineSidebar
        items={[ITEMS[0]]}
        activeKey="turn:first"
        onNavigate={vi.fn()}
      />,
    );
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("pauses active-item following while browsing and resumes after pointer and focus leave", () => {
    vi.useFakeTimers();
    const view = render(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="turn:first"
        onNavigate={vi.fn()}
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Conversation outline" });
    const scroller = screen.getByTestId("message-line-sidebar-scroller");
    const second = screen.getByRole("button", {
      name: "Second request: Second response",
    });
    const scrollTo = vi.fn();
    Object.defineProperties(scroller, {
      clientHeight: { configurable: true, value: 40 },
      scrollTop: { configurable: true, writable: true, value: 0 },
      scrollTo: { configurable: true, value: scrollTo },
    });
    Object.defineProperties(second, {
      offsetTop: { configurable: true, value: 90 },
      offsetHeight: { configurable: true, value: 28 },
    });

    fireEvent.pointerEnter(nav);
    fireEvent.focus(second);
    view.rerender(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="turn:second"
        onNavigate={vi.fn()}
      />,
    );
    expect(scrollTo).not.toHaveBeenCalled();

    fireEvent.pointerLeave(nav);
    vi.advanceTimersByTime(400);
    expect(scrollTo).not.toHaveBeenCalled();

    fireEvent.blur(second, { relatedTarget: document.body });
    vi.advanceTimersByTime(399);
    expect(scrollTo).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(scrollTo).toHaveBeenCalledWith({ top: 86, behavior: "smooth" });
  });
});

const TURN_ID = "019f7b34-9300-7000-8000-000000000001";
const CONVERSATION_ID = "019f7b34-9300-7000-8000-000000000002";
const TASK_ID = "019f7b34-9300-7000-8000-000000000003";

const ITEMS = [
  {
    key: "turn:first",
    anchorKey: "message:first",
    messageIds: ["first-user", "first-assistant"],
    title: "First request",
    response: "First response",
    streaming: false,
  },
  {
    key: "turn:second",
    anchorKey: "message:second",
    messageIds: ["second-user", "second-assistant"],
    title: "Second request",
    response: "Second response",
    streaming: false,
  },
] as const;

function messageId(sequence: number): string {
  return `019f7b34-9300-7000-8000-${sequence.toString().padStart(12, "0")}`;
}

function message(
  sequence: number,
  role: Message["role"],
  content: string,
  turnId: string | null,
): Message {
  return {
    id: messageId(sequence),
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    turn_id: turnId,
    sequence,
    role,
    visibility: "user",
    content,
    created_at: `2026-07-26T00:00:0${sequence}Z`,
  };
}
