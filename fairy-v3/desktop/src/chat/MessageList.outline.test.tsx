import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssistantTurn, Message } from "../core/client";
import { MessageList } from "./MessageList";

afterEach(cleanup);

describe("MessageList outline integration", () => {
  it("navigates to the selected durable message anchor", () => {
    renderMessageList(MESSAGES);
    const list = screen.getByLabelText("Conversation messages");
    const target = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${MESSAGES[1].id}"]`,
    );
    expect(target).not.toBeNull();
    const scrollTo = vi.fn();
    Object.defineProperties(list, {
      scrollTop: { configurable: true, writable: true, value: 50 },
      scrollTo: { configurable: true, value: scrollTo },
    });
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect({ top: 100 }));
    vi.spyOn(target as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 400 }));

    fireEvent.click(
      screen.getByRole("button", { name: "Fairy: Durable answer" }),
    );

    expect(scrollTo).toHaveBeenCalledWith({ top: 332, behavior: "smooth" });
    expect(screen.getByRole("button", { name: "Fairy: Durable answer" }))
      .toHaveAttribute("aria-current", "location");
  });

  it("tracks the message crossing the transcript scan line", () => {
    renderMessageList(MESSAGES);
    const list = screen.getByLabelText("Conversation messages");
    const first = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${MESSAGES[0].id}"]`,
    );
    const second = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${MESSAGES[1].id}"]`,
    );
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect({ top: 100 }));
    vi.spyOn(first as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 120 }));
    vi.spyOn(second as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 180 }));

    fireEvent.scroll(list);

    expect(screen.getByRole("button", { name: "Fairy: Durable answer" }))
      .toHaveAttribute("aria-current", "location");
  });

  it("replaces a streamed outline item with the durable assistant message", () => {
    const view = renderMessageList([MESSAGES[0]], {
      streamedText: "Current response",
      turn: TURN,
    });
    expect(screen.getByRole("button", { name: "Fairy: Current response" }))
      .toHaveAttribute("data-streaming", "true");

    view.rerender(
      messageList(
        MESSAGES,
        {
          streamedText: "Current response",
          turn: TURN,
        },
      ),
    );

    expect(screen.getAllByRole("button", { name: /Fairy:/u })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Fairy: Durable answer" }))
      .not.toHaveAttribute("data-streaming");
  });

  it("drops every old outline item when the Conversation changes", () => {
    const view = renderMessageList(MESSAGES);
    const other = [
      {
        ...MESSAGES[0],
        id: "019f7b34-9300-7000-8000-000000000099",
        conversation_id: "019f7b34-9300-7000-8000-000000000098",
        content: "Second conversation",
      },
    ];

    view.rerender(messageList(other));

    expect(screen.queryByRole("button", { name: "You: Durable request" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fairy: Durable answer" }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "You: Second conversation" }))
      .toHaveAttribute("aria-current", "location");
  });
});

const CONVERSATION_ID = "019f7b34-9300-7000-8000-000000000010";
const TASK_ID = "019f7b34-9300-7000-8000-000000000011";
const TURN_ID = "019f7b34-9300-7000-8000-000000000012";

const TURN = {
  id: TURN_ID,
  conversation_id: CONVERSATION_ID,
  task_id: TASK_ID,
  status: "running",
} as AssistantTurn;

const MESSAGES: Message[] = [
  message(1, "user", "Durable request"),
  message(2, "assistant", "Durable answer"),
];

function renderMessageList(
  messages: Message[],
  overrides: {
    streamedText?: string;
    turn?: AssistantTurn | null;
  } = {},
) {
  return render(messageList(messages, overrides));
}

function messageList(
  messages: Message[],
  overrides: {
    streamedText?: string;
    turn?: AssistantTurn | null;
  } = {},
) {
  return (
    <MessageList
      messages={messages}
      realtimeTranscript={[]}
      streamedText={overrides.streamedText ?? ""}
      turn={overrides.turn ?? null}
      turnTraces={{}}
      turnTraceStates={{}}
      events={[]}
      pendingUserMessage={null}
      developerMode={false}
      onRetryPending={vi.fn(async () => undefined)}
      onEditPending={vi.fn()}
      onDeletePending={vi.fn()}
      onCopy={vi.fn(async () => undefined)}
      onOpenLink={vi.fn(async () => undefined)}
    />
  );
}

function message(
  sequence: number,
  role: Message["role"],
  content: string,
): Message {
  return {
    id: `019f7b34-9300-7000-8000-${(20 + sequence).toString().padStart(12, "0")}`,
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

function rect(overrides: Partial<DOMRect>): DOMRect {
  return {
    x: 0,
    y: 0,
    width: 100,
    height: 28,
    top: 0,
    right: 100,
    bottom: 28,
    left: 0,
    toJSON: () => ({}),
    ...overrides,
  };
}
