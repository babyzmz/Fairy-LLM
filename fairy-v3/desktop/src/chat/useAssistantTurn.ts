import { useCallback, useMemo, useRef, useState } from "react";

import type {
  AssistantTurn,
  CoreClient,
  EventEnvelope,
  TaskCreateInput,
} from "../core/client";

export interface AssistantTurnClient {
  tasks: Pick<CoreClient["tasks"], "create">;
  documents: Pick<CoreClient["documents"], "import">;
  assistant: {
    turns: Pick<CoreClient["assistant"]["turns"], "create" | "run" | "cancel" | "retry">;
  };
}

interface UseAssistantTurnOptions {
  client: AssistantTurnClient;
  conversationId: string | null;
  profileId: string | null;
  operationMode: TaskCreateInput["operation_mode"];
  events: EventEnvelope[];
  onTaskCreated?(taskId: string): void;
  onSettled?(): void | Promise<void>;
}

interface AssistantTurnState {
  turn: AssistantTurn | null;
  isBusy: boolean;
  error: string | null;
  streamedText: string;
  send(value: string, files: File[]): Promise<void>;
  cancel(): Promise<void>;
  retry(): Promise<void>;
  reset(): void;
}

interface AssistantDelta {
  eventId: string;
  cursor: number;
  turnId: string;
  modelRound: number;
  chunkIndex: number;
  text: string;
}

const MEDIA_TYPES: Readonly<Record<string, string>> = {
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".markdown": "text/markdown",
  ".html": "text/html",
  ".htm": "text/html",
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export function useAssistantTurn(options: UseAssistantTurnOptions): AssistantTurnState {
  const [turn, setTurn] = useState<AssistantTurn | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operationRef = useRef(0);
  const busyRef = useRef(false);

  const settle = useCallback(async () => {
    await options.onSettled?.();
  }, [options]);

  const send = useCallback(
    async (value: string, files: File[]) => {
      if (busyRef.current) throw new Error("An assistant turn is already running");
      const conversationId = required(options.conversationId, "Conversation is unavailable");
      const profileId = required(options.profileId, "Model provider is unavailable");
      const operation = ++operationRef.current;
      busyRef.current = true;
      setIsBusy(true);
      setError(null);
      setTurn(null);
      try {
        const taskContext = await options.client.tasks.create({
          conversation_id: conversationId,
          user_request: value,
          operation_mode: options.operationMode,
          execution_target: "local",
          idempotency_key: idempotencyKey("task"),
        });
        options.onTaskCreated?.(taskContext.task.id);
        for (const file of files) {
          await options.client.documents.import({
            task_id: taskContext.task.id,
            filename: file.name,
            media_type: documentMediaType(file),
            content_base64: await fileToBase64(file),
            visibility: "conversation",
            user_confirmed: true,
            idempotency_key: idempotencyKey("document"),
          });
        }
        const created = await options.client.assistant.turns.create({
          task_id: taskContext.task.id,
          profile_id: profileId,
          idempotency_key: idempotencyKey("assistant"),
        });
        if (operation !== operationRef.current) return;
        setTurn(created);
        const completed = await options.client.assistant.turns.run(created.id);
        if (operation === operationRef.current) setTurn(completed);
      } catch (caught) {
        if (operation === operationRef.current) setError(errorMessage(caught));
        throw caught;
      } finally {
        if (operation === operationRef.current) {
          busyRef.current = false;
          setIsBusy(false);
          await settle();
        }
      }
    },
    [options, settle],
  );

  const cancel = useCallback(async () => {
    if (turn === null || !busyRef.current) return;
    ++operationRef.current;
    busyRef.current = false;
    setError(null);
    try {
      const cancelled = await options.client.assistant.turns.cancel({
        turn_id: turn.id,
        expected_cancellation_revision: turn.cancellation_revision,
      });
      setTurn(cancelled);
    } catch (caught) {
      setError(errorMessage(caught));
      throw caught;
    } finally {
      setIsBusy(false);
      await settle();
    }
  }, [options.client.assistant.turns, settle, turn]);

  const retry = useCallback(async () => {
    if (turn === null || !isTerminal(turn)) {
      throw new Error("Only a terminal assistant turn can be retried");
    }
    if (busyRef.current) throw new Error("An assistant turn is already running");
    const operation = ++operationRef.current;
    busyRef.current = true;
    setIsBusy(true);
    setError(null);
    try {
      const created = await options.client.assistant.turns.retry({
        turn_id: turn.id,
        idempotency_key: idempotencyKey("assistant-retry"),
      });
      if (operation !== operationRef.current) return;
      setTurn(created);
      const completed = await options.client.assistant.turns.run(created.id);
      if (operation === operationRef.current) setTurn(completed);
    } catch (caught) {
      if (operation === operationRef.current) setError(errorMessage(caught));
      throw caught;
    } finally {
      if (operation === operationRef.current) {
        busyRef.current = false;
        setIsBusy(false);
        await settle();
      }
    }
  }, [options.client.assistant.turns, settle, turn]);

  const reset = useCallback(() => {
    ++operationRef.current;
    busyRef.current = false;
    setTurn(null);
    setIsBusy(false);
    setError(null);
  }, []);

  const streamedText = useMemo(
    () => assistantDeltaText(options.events, turn?.id ?? null),
    [options.events, turn?.id],
  );

  return { turn, isBusy, error, streamedText, send, cancel, retry, reset };
}

export function assistantDeltaText(
  events: EventEnvelope[],
  turnId: string | null,
): string {
  if (turnId === null) return "";
  const ordered = events
    .map(toAssistantDelta)
    .filter((delta): delta is AssistantDelta => delta !== null)
    .filter((delta) => delta.turnId === turnId)
    .filter((delta) => delta.eventId.length > 0)
    .sort(
      (left, right) =>
        left.modelRound - right.modelRound ||
        left.chunkIndex - right.chunkIndex ||
        left.cursor - right.cursor,
    );
  const eventIds = new Set<string>();
  const chunkCoordinates = new Set<string>();
  const chunks: string[] = [];
  for (const delta of ordered) {
    if (eventIds.has(delta.eventId)) continue;
    eventIds.add(delta.eventId);
    const coordinate = `${delta.modelRound}:${delta.chunkIndex}`;
    if (chunkCoordinates.has(coordinate)) continue;
    chunkCoordinates.add(coordinate);
    chunks.push(delta.text);
  }
  return chunks.join("");
}

function toAssistantDelta(event: EventEnvelope): AssistantDelta | null {
  if (event.event_type !== "assistant.message.delta") return null;
  const payload = event.payload;
  if (
    typeof payload.turn_id !== "string" ||
    typeof payload.model_round !== "number" ||
    typeof payload.chunk_index !== "number" ||
    typeof payload.text !== "string"
  ) {
    return null;
  }
  return {
    eventId: event.id,
    cursor: event.cursor,
    turnId: payload.turn_id,
    modelRound: payload.model_round,
    chunkIndex: payload.chunk_index,
    text: payload.text,
  };
}

function isTerminal(turn: AssistantTurn): boolean {
  return ["completed", "cancelled", "failed"].includes(turn.status);
}

function required(value: string | null, message: string): string {
  if (value === null || value.trim().length === 0) throw new Error(message);
  return value;
}

function documentMediaType(file: File): string {
  const dot = file.name.lastIndexOf(".");
  const extension = dot < 0 ? "" : file.name.slice(dot).toLowerCase();
  const mediaType = MEDIA_TYPES[extension];
  if (mediaType === undefined) throw new Error(`Unsupported document type: ${file.name}`);
  return mediaType;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error(`Could not encode ${file.name}`));
        return;
      }
      const separator = result.indexOf(",");
      if (separator < 0) {
        reject(new Error(`Could not encode ${file.name}`));
        return;
      }
      resolve(result.slice(separator + 1));
    };
    reader.readAsDataURL(file);
  });
}

function idempotencyKey(operation: string): string {
  return `desktop:${operation}:${crypto.randomUUID()}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Assistant request failed";
}
