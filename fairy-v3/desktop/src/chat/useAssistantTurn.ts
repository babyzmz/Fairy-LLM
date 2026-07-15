import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AssistantTurn,
  CoreClient,
  EventEnvelope,
  ModelSelectionPreference,
  TaskCreateInput,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";

export interface AssistantTurnClient {
  tasks: Pick<CoreClient["tasks"], "create">;
  documents: Pick<CoreClient["documents"], "import">;
  assistant: {
    turns: Pick<
      CoreClient["assistant"]["turns"],
      "create" | "get" | "start" | "cancel" | "retry"
    >;
  };
}

export interface AssistantDraft {
  id: string;
  value: string;
  files: File[];
  images: PendingImageAttachment[];
}

export interface OptimisticUserMessage {
  id: string;
  content: string;
  createdAt: string;
  attachmentCount: number;
  status: "sending" | "failed";
  error: string | null;
}

interface UseAssistantTurnOptions {
  client: AssistantTurnClient;
  conversationId: string | null;
  profileId?: string | null;
  modelSelection?: ModelSelectionPreference | null;
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
  pendingUserMessage: OptimisticUserMessage | null;
  send(
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  sendToConversation(
    conversationId: string,
    value: string,
    files: File[],
    images?: PendingImageAttachment[],
  ): Promise<void>;
  cancel(): Promise<void>;
  resume(): Promise<void>;
  retry(): Promise<void>;
  retryPending(): Promise<void>;
  deletePending(): void;
  takePendingForEdit(): AssistantDraft | null;
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
  const [pendingUserMessage, setPendingUserMessage] =
    useState<OptimisticUserMessage | null>(null);
  const [stateConversationId, setStateConversationId] = useState(options.conversationId);
  const operationRef = useRef(0);
  const busyRef = useRef(false);
  const conversationRef = useRef(options.conversationId);
  const statusEventCursorRef = useRef(0);
  const pendingDraftRef = useRef<AssistantDraft | null>(null);

  const settle = useCallback(async () => {
    await options.onSettled?.();
  }, [options]);

  useEffect(() => {
    if (conversationRef.current === options.conversationId) return;
    conversationRef.current = options.conversationId;
    ++operationRef.current;
    busyRef.current = false;
    statusEventCursorRef.current = 0;
    pendingDraftRef.current = null;
    setStateConversationId(options.conversationId);
    setTurn(null);
    setIsBusy(false);
    setError(null);
    setPendingUserMessage(null);
  }, [options.conversationId]);

  const executeDraft = useCallback(
    async (draft: AssistantDraft, conversationOverride?: string) => {
      if (busyRef.current) throw new Error("An assistant turn is already running");
      const conversationId = required(
        conversationOverride ?? options.conversationId,
        "Conversation is unavailable",
      );
      if (conversationRef.current !== conversationId) {
        ++operationRef.current;
        conversationRef.current = conversationId;
        busyRef.current = false;
        statusEventCursorRef.current = 0;
      }
      const modelSource =
        options.modelSelection === null || options.modelSelection === undefined
          ? {
              profile_id: required(options.profileId, "Model provider is unavailable"),
            }
          : {
              model_selection: {
                mode: options.modelSelection.mode,
                model_id: options.modelSelection.model_id,
                revision: options.modelSelection.revision,
              },
            };
      const operation = ++operationRef.current;
      setStateConversationId(conversationId);
      pendingDraftRef.current = draft;
      setPendingUserMessage({
        id: draft.id,
        content: draft.value,
        createdAt: new Date().toISOString(),
        attachmentCount: draft.files.length + draft.images.length,
        status: "sending",
        error: null,
      });
      busyRef.current = true;
      setIsBusy(true);
      setError(null);
      setTurn(null);
      let committed = false;
      try {
        const taskContext = await options.client.tasks.create({
          conversation_id: conversationId,
          user_request: draft.value,
          operation_mode: options.operationMode,
          execution_target: "local",
          idempotency_key: idempotencyKey("task"),
        });
        options.onTaskCreated?.(taskContext.task.id);
        for (const file of draft.files) {
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
          ...modelSource,
          idempotency_key: idempotencyKey("assistant"),
          image_attachments: draft.images.map((image) => ({
            media_type: image.media_type,
            png_base64: image.png_base64,
            content_hash: image.content_hash,
            width: image.width,
            height: image.height,
            source_label: image.source_label,
            captured_at_ms: image.captured_at_ms,
            persistence: image.persistence,
          })),
        });
        if (operation !== operationRef.current) return;
        committed = true;
        setTurn(created);
        const started = await options.client.assistant.turns.start(created.id);
        if (operation !== operationRef.current) return;
        setTurn(started);
        if (!isActive(started)) {
          busyRef.current = false;
          setIsBusy(false);
        }
        await settle();
        if (operation === operationRef.current) {
          pendingDraftRef.current = null;
          setPendingUserMessage(null);
        }
      } catch (caught) {
        if (operation === operationRef.current) {
          const message = errorMessage(caught);
          setError(message);
          busyRef.current = false;
          setIsBusy(false);
          if (committed) {
            pendingDraftRef.current = null;
            setPendingUserMessage(null);
            await settle();
          } else {
            setPendingUserMessage((current) =>
              current?.id === draft.id
                ? { ...current, status: "failed", error: message }
                : current,
            );
          }
        }
        throw caught;
      }
    },
    [options, settle],
  );

  const send = useCallback(
    (
      value: string,
      files: File[],
      images: PendingImageAttachment[] = [],
    ) =>
      executeDraft({
        id: idempotencyKey("optimistic-message"),
        value,
        files: [...files],
        images: [...images],
      }),
    [executeDraft],
  );

  const sendToConversation = useCallback(
    (
      conversationId: string,
      value: string,
      files: File[],
      images: PendingImageAttachment[] = [],
    ) =>
      executeDraft(
        {
          id: idempotencyKey("optimistic-message"),
          value,
          files: [...files],
          images: [...images],
        },
        conversationId,
      ),
    [executeDraft],
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

  const resume = useCallback(async () => {
    if (turn === null || turn.status !== "waiting_for_tool" || busyRef.current) return;
    const operation = ++operationRef.current;
    busyRef.current = true;
    setIsBusy(true);
    setError(null);
    try {
      const started = await options.client.assistant.turns.start(turn.id);
      if (operation === operationRef.current) {
        setTurn(started);
        if (!isActive(started)) {
          busyRef.current = false;
          setIsBusy(false);
          await settle();
        }
      }
    } catch (caught) {
      if (operation === operationRef.current) {
        busyRef.current = false;
        setIsBusy(false);
        setError(errorMessage(caught));
      }
      throw caught;
    }
  }, [options.client.assistant.turns, settle, turn]);

  useEffect(() => {
    if (turn === null) return;
    const statusEvent = [...options.events].reverse().find(
      (event) =>
        event.task_id === turn.task_id &&
        event.cursor > statusEventCursorRef.current &&
        STATUS_EVENT_TYPES.has(event.event_type),
    );
    if (statusEvent === undefined) return;
    statusEventCursorRef.current = statusEvent.cursor;
    const operation = operationRef.current;
    void options.client.assistant.turns
      .get(turn.id)
      .then(async (current) => {
        if (operation !== operationRef.current) return;
        setTurn(current);
        const active = isActive(current);
        busyRef.current = active;
        setIsBusy(active);
        if (!active) await settle();
      })
      .catch((caught) => {
        if (operation === operationRef.current) setError(errorMessage(caught));
      });
  }, [options.client.assistant.turns, options.events, settle, turn]);

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
      const started = await options.client.assistant.turns.start(created.id);
      if (operation === operationRef.current) {
        setTurn(started);
        if (!isActive(started)) {
          busyRef.current = false;
          setIsBusy(false);
          await settle();
        }
      }
    } catch (caught) {
      if (operation === operationRef.current) {
        busyRef.current = false;
        setIsBusy(false);
        setError(errorMessage(caught));
      }
      throw caught;
    }
  }, [options.client.assistant.turns, settle, turn]);

  const retryPending = useCallback(async () => {
    const draft = pendingDraftRef.current;
    if (draft === null) return;
    await executeDraft(draft);
  }, [executeDraft]);

  const deletePending = useCallback(() => {
    pendingDraftRef.current = null;
    setPendingUserMessage(null);
    setError(null);
  }, []);

  const takePendingForEdit = useCallback((): AssistantDraft | null => {
    const draft = pendingDraftRef.current;
    pendingDraftRef.current = null;
    setPendingUserMessage(null);
    setError(null);
    return draft;
  }, []);

  const reset = useCallback(() => {
    ++operationRef.current;
    busyRef.current = false;
    setTurn(null);
    setIsBusy(false);
    setError(null);
    pendingDraftRef.current = null;
    setPendingUserMessage(null);
    conversationRef.current = options.conversationId;
    setStateConversationId(options.conversationId);
  }, [options.conversationId]);

  const streamedText = useMemo(
    () => (turn !== null && isTerminal(turn) ? "" : assistantDeltaText(options.events, turn?.id ?? null)),
    [options.events, turn?.id, turn?.status],
  );

  const visibleInConversation = stateConversationId === options.conversationId;

  return {
    turn: visibleInConversation ? turn : null,
    isBusy: visibleInConversation && isBusy,
    error: visibleInConversation ? error : null,
    streamedText: visibleInConversation ? streamedText : "",
    pendingUserMessage: visibleInConversation ? pendingUserMessage : null,
    send,
    sendToConversation,
    cancel,
    resume,
    retry,
    retryPending,
    deletePending,
    takePendingForEdit,
    reset,
  };
}

const STATUS_EVENT_TYPES = new Set([
  "assistant.turn.started",
  "assistant.turn.completed",
  "assistant.turn.cancelled",
  "assistant.turn.failed",
  "assistant.budget.approval_requested",
  "command.waiting_approval",
  "approval.requested",
]);

function isActive(turn: AssistantTurn): boolean {
  return turn.status === "created" || turn.status === "running";
}

export function assistantDeltaText(
  events: EventEnvelope[],
  turnId: string | null,
): string {
  if (turnId === null) return "";
  const resetCursor = events.reduce(
    (latest, event) =>
      event.event_type === "assistant.message.projection_reset" &&
      event.payload.turn_id === turnId
        ? Math.max(latest, event.cursor)
        : latest,
    0,
  );
  const ordered = events
    .map(toAssistantDelta)
    .filter((delta): delta is AssistantDelta => delta !== null)
    .filter((delta) => delta.turnId === turnId)
    .filter((delta) => delta.cursor > resetCursor)
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

function required(value: string | null | undefined, message: string): string {
  if (value === undefined || value === null || value.trim().length === 0) {
    throw new Error(message);
  }
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
