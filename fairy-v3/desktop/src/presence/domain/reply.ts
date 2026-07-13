import type { AssistantTurn, Message } from "../../core/contracts";

import type { PresenceReply } from "./projection";

interface PetReplySource {
  petTaskId: string | null;
  turn: Pick<AssistantTurn, "id" | "task_id" | "status"> | null;
  streamedText: string;
  messages: ReadonlyArray<
    Pick<Message, "id" | "task_id" | "role" | "visibility" | "content" | "sequence">
  >;
}

export function projectPetReply(source: PetReplySource): PresenceReply | null {
  const taskId = source.petTaskId;
  if (taskId === null) return null;
  if (source.turn?.task_id === taskId && source.streamedText !== "") {
    return {
      id: `pet-stream:${source.turn.id}`,
      text: boundedReply(source.streamedText),
      kind: "scratch",
      streaming: source.turn.status === "running",
    };
  }
  const message = [...source.messages]
    .filter(
      (item) =>
        item.task_id === taskId &&
        item.role === "assistant" &&
        item.visibility === "user",
    )
    .sort((left, right) => left.sequence - right.sequence)
    .at(-1);
  return message === undefined
    ? null
    : {
        id: `pet-message:${message.id}`,
        text: boundedReply(message.content),
        kind: "scratch",
        streaming: false,
      };
}

function boundedReply(value: string): string {
  return Array.from(value.trim()).slice(-1_200).join("") || "Fairy is ready";
}
