import { describe, expect, it } from "vitest";

import { projectPetReply } from "./reply";

describe("projectPetReply", () => {
  it.each(["completed", "cancelled", "failed"] as const)(
    "never leaves a %s turn showing a streaming cursor",
    (status) => {
      expect(projectPetReply({
        petTaskId: "task-pet",
        turn: { id: "turn-pet", task_id: "task-pet", status },
        streamedText: "A partial durable reply",
        messages: [],
      })).toEqual({
        id: "pet-stream:turn-pet",
        text: "A partial durable reply",
        kind: "scratch",
        streaming: false,
      });
    },
  );

  it("streams only the running pet turn and ignores another task", () => {
    expect(projectPetReply({
      petTaskId: "task-pet",
      turn: { id: "turn-pet", task_id: "task-pet", status: "running" },
      streamedText: "First token",
      messages: [],
    })?.streaming).toBe(true);
    expect(projectPetReply({
      petTaskId: "task-pet",
      turn: { id: "turn-other", task_id: "task-other", status: "running" },
      streamedText: "Another task",
      messages: [],
    })).toBeNull();
  });

  it("projects one latest durable assistant message for the pet task", () => {
    const reply = projectPetReply({
      petTaskId: "task-pet",
      turn: null,
      streamedText: "",
      messages: [
        message("message-2", "task-pet", 2, "Latest reply"),
        message("message-other", "task-other", 3, "Not for the pet"),
        message("message-1", "task-pet", 1, "Earlier reply"),
      ],
    });
    expect(reply).toEqual({
      id: "pet-message:message-2",
      text: "Latest reply",
      kind: "scratch",
      streaming: false,
    });
  });
});

function message(id: string, task_id: string, sequence: number, content: string) {
  return {
    id,
    task_id,
    sequence,
    content,
    role: "assistant" as const,
    visibility: "user" as const,
  };
}
