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
  it("projects only durable user and assistant messages plus the current response", () => {
    const items = projectMessageLineItems(
      [
        message(1, "user", "First request"),
        message(2, "tool", "private tool payload"),
        message(3, "system_notice", "Core notice"),
        message(4, "assistant", "First answer"),
      ],
      "Answer in progress",
      TURN_ID,
    );

    expect(items.map((item) => [item.role, item.excerpt, item.streaming])).toEqual([
      ["user", "First request", false],
      ["assistant", "First answer", false],
      ["assistant", "Answer in progress", true],
    ]);
  });

  it("creates readable bounded excerpts from Markdown and Unicode", () => {
    expect(
      messageLineExcerpt(
        "## [Fairy link](https://example.com)\n\n```ts\nconst ready = true;\n```",
        "assistant",
      ),
    ).toBe("Fairy link const ready = true;");
    expect(messageLineExcerpt("   ", "user")).toBe("Attachment message");
    expect(messageLineExcerpt("", "assistant")).toBe("Response");
    expect(Array.from(messageLineExcerpt("🧚".repeat(60), "assistant"))).toHaveLength(48);
  });

  it("exposes the active location and navigates with its stable key", () => {
    const onNavigate = vi.fn();
    render(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="message:first"
        onNavigate={onNavigate}
      />,
    );

    expect(screen.getByRole("navigation", { name: "Conversation outline" })).toBeVisible();
    expect(screen.getByRole("button", { name: "You: First request" }))
      .toHaveAttribute("aria-current", "location");
    fireEvent.click(screen.getByRole("button", { name: "Fairy: First answer" }));
    expect(onNavigate).toHaveBeenCalledWith("message:second", true);
  });

  it("pauses active-item following while browsing and resumes after pointer and focus leave", () => {
    vi.useFakeTimers();
    const view = render(
      <MessageLineSidebar
        items={ITEMS}
        activeKey="message:first"
        onNavigate={vi.fn()}
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Conversation outline" });
    const scroller = screen.getByTestId("message-line-sidebar-scroller");
    const second = screen.getByRole("button", { name: "Fairy: First answer" });
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
        activeKey="message:second"
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
    key: "message:first",
    role: "user",
    label: "You",
    excerpt: "First request",
    streaming: false,
  },
  {
    key: "message:second",
    role: "assistant",
    label: "Fairy",
    excerpt: "First answer",
    streaming: false,
  },
] as const;

function message(
  sequence: number,
  role: Message["role"],
  content: string,
): Message {
  return {
    id: `019f7b34-9300-7000-8000-${sequence.toString().padStart(12, "0")}`,
    conversation_id: CONVERSATION_ID,
    task_id: TASK_ID,
    turn_id: TURN_ID,
    sequence,
    role,
    visibility: "user",
    content,
    created_at: `2026-07-26T00:00:0${sequence}Z`,
  };
}
