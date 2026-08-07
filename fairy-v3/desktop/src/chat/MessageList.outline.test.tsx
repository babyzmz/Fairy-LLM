import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssistantSchedule, AssistantTurn, Message } from "../core/client";
import { MessageList } from "./MessageList";

afterEach(cleanup);

describe("MessageList outline integration", () => {
  it("navigates to the selected durable message anchor", () => {
    renderMessageList(MESSAGES);
    const list = screen.getByLabelText("Conversation messages");
    const target = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${MESSAGES[0].id}"]`,
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
      screen.getByRole("button", {
        name: "Durable request: Durable answer",
      }),
    );

    expect(scrollTo).toHaveBeenCalledWith({ top: 332, behavior: "smooth" });
    expect(screen.getByRole("button", {
      name: "Durable request: Durable answer",
    }))
      .toHaveAttribute("aria-current", "location");
  });

  it("tracks exchanges at their user anchors instead of assistant rows", () => {
    renderMessageList(THREE_EXCHANGES);
    const list = screen.getByLabelText("Conversation messages");
    const first = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${THREE_EXCHANGES[0].id}"]`,
    );
    const second = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${THREE_EXCHANGES[2].id}"]`,
    );
    const third = document.querySelector<HTMLElement>(
      `[data-message-line-key="message:${THREE_EXCHANGES[4].id}"]`,
    );
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect({ top: 100 }));
    vi.spyOn(first as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 120 }));
    vi.spyOn(second as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 180 }));
    vi.spyOn(third as HTMLElement, "getBoundingClientRect")
      .mockReturnValue(rect({ top: 260 }));

    fireEvent.scroll(list);

    expect(screen.getByRole("button", {
      name: "Second request: Second answer",
    }))
      .toHaveAttribute("aria-current", "location");
  });

  it("replaces a streamed outline item with the durable assistant message", () => {
    const view = renderMessageList([MESSAGES[0]], {
      streamedText: "Current response",
      turn: TURN,
    });
    expect(screen.getByRole("button", {
      name: "Durable request: Current response",
    }))
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

    expect(screen.getAllByRole("button", {
      name: /Durable request:/u,
    })).toHaveLength(1);
    expect(screen.getByRole("button", {
      name: "Durable request: Durable answer",
    }))
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

    expect(screen.queryByRole("button", {
      name: "Durable request: Durable answer",
    }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("button", {
      name: "Second conversation: Fairy is responding…",
    }))
      .toHaveAttribute("aria-current", "location");
  });

  it("orders schedule cards in the durable timeline without adding outline lines", () => {
    render(
      messageList(
        [MESSAGES[0], { ...MESSAGES[1], sequence: 3 }],
        {},
        [schedule({ timeline_sequence: 2 })],
      ),
    );

    const sequences = Array.from(document.querySelectorAll("[data-message-sequence]"))
      .map((node) => node.getAttribute("data-message-sequence"));
    expect(sequences).toEqual(["1", "2", "3"]);
    expect(screen.getAllByRole("button", { name: /Durable request:/u })).toHaveLength(1);
    expect(document.querySelector(`[data-schedule-id]`)).not.toBeNull();
  });

  it("locates a scheduled task selected from the background panel", async () => {
    const scheduled = schedule();
    const onLocated = vi.fn();
    const view = render(messageList(MESSAGES, {}, [scheduled]));
    const list = screen.getByLabelText("Conversation messages");
    const scrollTo = vi.fn();
    Object.defineProperties(list, {
      scrollTop: { configurable: true, writable: true, value: 0 },
      scrollTo: { configurable: true, value: scrollTo },
    });
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect({ top: 100 }));
    const card = document.querySelector<HTMLElement>(`[data-schedule-id="${scheduled.id}"]`);
    vi.spyOn(card as HTMLElement, "getBoundingClientRect").mockReturnValue(rect({ top: 360 }));

    view.rerender(messageList(MESSAGES, {
      timelineTarget: {
        key: "locate-1",
        scheduleId: scheduled.id,
        turnId: null,
      },
      onTimelineTargetLocated: onLocated,
    }, [scheduled]));

    await waitFor(() => expect(onLocated).toHaveBeenCalledWith("locate-1"));
    expect(scrollTo).toHaveBeenCalledWith({ top: 242, behavior: "smooth" });
    expect(card).toHaveFocus();
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

const THREE_EXCHANGES: Message[] = [
  message(1, "user", "First request", `${TURN_ID}-1`),
  message(2, "assistant", "First answer", `${TURN_ID}-1`),
  message(3, "user", "Second request", `${TURN_ID}-2`),
  message(4, "assistant", "Second answer", `${TURN_ID}-2`),
  message(5, "user", "Third request", `${TURN_ID}-3`),
  message(6, "assistant", "Third answer", `${TURN_ID}-3`),
];

function renderMessageList(
  messages: Message[],
  overrides: MessageListOverrides = {},
) {
  return render(messageList(messages, overrides));
}

function messageList(
  messages: Message[],
  overrides: MessageListOverrides = {},
  schedules: AssistantSchedule[] = [],
) {
  return (
    <MessageList
      messages={messages}
      schedules={schedules}
      realtimeTranscript={[]}
      streamedText={overrides.streamedText ?? ""}
      turn={overrides.turn ?? null}
      timelineTarget={overrides.timelineTarget ?? null}
      onTimelineTargetLocated={overrides.onTimelineTargetLocated}
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

interface MessageListOverrides {
  streamedText?: string;
  turn?: AssistantTurn | null;
  timelineTarget?: {
    key: string;
    scheduleId: string | null;
    turnId: string | null;
  } | null;
  onTimelineTargetLocated?(key: string): void;
}

function schedule(overrides: Partial<AssistantSchedule> = {}): AssistantSchedule {
  return {
    id: "019f7b34-9300-7000-8000-000000000070",
    conversation_id: CONVERSATION_ID,
    task_id: null,
    project_id: null,
    workspace_id: "019f7b34-9300-7000-8000-000000000071",
    version_id: null,
    instruction: "Summarize the workspace",
    operation_mode: "answer",
    trigger_kind: "daily",
    trigger_rule: { local_time: "09:30" },
    timezone: "Australia/Sydney",
    next_fire_at: "2026-08-08T23:30:00Z",
    execution_target: "local",
    profile_id: null,
    model_selection: null,
    permission_profile: "standard",
    timeline_sequence: 2,
    status: "active",
    active_revision: 1,
    consecutive_failures: 0,
    attention_code: null,
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
    last_fire_at: null,
    paused_at: null,
    completed_at: null,
    cancelled_at: null,
    ...overrides,
  };
}

function message(
  sequence: number,
  role: Message["role"],
  content: string,
  turnId = TURN_ID,
): Message {
  return {
    id: `019f7b34-9300-7000-8000-${(20 + sequence).toString().padStart(12, "0")}`,
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
