import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";
import { PresenceProjection, type PresenceProjectionState } from "../domain/projection";

const revision = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const contextSchema = z.object({
  revision, projection_revision: revision, conversation_id: z.string().uuid().nullable(),
  connection_available: z.boolean(),
  submission: z.object({ id: z.string().min(1).max(128), revision }).strict().nullable(),
  turn: z.object({
    id: z.string().uuid(), conversation_id: z.string().uuid(), cancellation_revision: revision,
    cancellation_pending: z.boolean(),
    status: z.enum(["created", "running", "waiting_for_tool", "waiting_for_input", "completed", "failed", "cancelled"]),
  }).strict().nullable(),
  reply: z.object({ id: z.string().uuid(), text: z.string().min(1).max(2400)
    .refine((text) => Array.from(text).length <= 1200) }).strict().nullable(),
}).strict().refine((state) => state.turn === null || state.turn.conversation_id === state.conversation_id)
  .refine((state) => state.reply === null || state.turn !== null);

export type PetChatContext = z.infer<typeof contextSchema>;
export interface PetChatTransport {
  getContext(): Promise<unknown>;
  onContext(listener: (value: unknown) => void): Promise<() => void>;
  submit(revision: number, submissionId: string, text: string): Promise<{ binding_revision: number; turn_id: string }>;
  cancelSubmission(revision: number, submissionId: string): Promise<{ accepted: boolean; cancellation_pending: boolean | null; turn_id?: string | null }>;
  cancel(revision: number): Promise<{ accepted: boolean; cancellation_pending: boolean }>;
  newChat(revision: number, requestId: string): Promise<unknown>;
}

export function createPetChatTransport(): PetChatTransport | null {
  if (!isTauri()) return null;
  return {
    getContext: () => invoke("pet_chat_context_get"),
    onContext: (listener) => listen("pet-chat-context-changed", (event) => listener(event.payload)),
    submit: (revision, submissionId, text) => invoke("pet_chat_submit", { revision, submissionId, text }),
    cancelSubmission: (revision, submissionId) => invoke("pet_chat_submission_cancel", { revision, submissionId }),
    cancel: (revision) => invoke("pet_chat_cancel", { revision }),
    newChat: (revision, requestId) => invoke("pet_chat_new", { revision, requestId }),
  };
}

/** Subscribe before reading. A late initial read must not roll back an event. */
export function observePetChat(
  transport: Pick<PetChatTransport, "getContext" | "onContext">,
  listener: (value: PetChatContext) => void,
  onError: () => void = () => undefined,
): () => void {
  let closed = false;
  let last = -1;
  let stop: (() => void) | undefined;
  const receive = (value: unknown) => {
    if (closed) return;
    const parsed = contextSchema.safeParse(value);
    if (!parsed.success || parsed.data.projection_revision <= last) return;
    last = parsed.data.projection_revision;
    listener(parsed.data);
  };
  void transport.onContext(receive).then((unsubscribe) => {
    if (closed) { unsubscribe(); return; }
    stop = unsubscribe;
    return transport.getContext().then(receive);
  }).catch(() => { if (!closed) onError(); });
  return () => { closed = true; stop?.(); };
}

function boundedReply(text: string): string {
  // The existing presence wire bound is UTF-16; never split a surrogate pair.
  const truncated = text.slice(0, 1200);
  return /[\uD800-\uDBFF]$/u.test(truncated) ? truncated.slice(0, -1) : truncated;
}

export function mergePetChatProjection(
  base: PresenceProjectionState,
  context: PetChatContext | null,
  updatedAt: number,
): PresenceProjectionState {
  if (context === null || base.realtime_active || (context.conversation_id === null && context.submission === null)) return base;
  const state: PresenceProjectionState = {
    ...PresenceProjection.initial(), updated_at_ms: updatedAt, speaking: base.speaking,
  };
  if (context.submission !== null) {
    return { ...state, activity: "attending", work_state: "analyzing", status_text: "Reviewing your request" };
  }
  if (!context.connection_available) {
    return { ...state, activity: "needs_attention", work_state: "error", status_text: "Core connection unavailable",
      notice: { id: `pet-offline:${context.revision}`, tone: "critical", text: "Fairy needs your attention" } };
  }
  const turn = context.turn;
  if (turn?.cancellation_pending) return { ...state, activity: "working", work_state: "tool", status_text: "Stopping current task" };
  if (turn?.status === "failed") return { ...state, activity: "needs_attention", work_state: "error", status_text: "Needs attention",
    notice: { id: `pet-failed:${turn.id}`, tone: "critical", text: "Fairy needs your attention" } };
  if (turn?.status === "waiting_for_input") return { ...state, activity: "needs_attention", work_state: "awaiting_confirmation", status_text: "Waiting for your decision" };
  if (turn?.status === "waiting_for_tool") return { ...state, activity: "working", work_state: "tool", status_text: "Preparing the next step" };
  if (turn?.status === "created" || turn?.status === "running") return { ...state, activity: "working", work_state: "analyzing", status_text: "Reviewing your request" };
  if (context.reply !== null && turn?.status === "completed") return { ...state, activity: "ready", work_state: "ready", status_text: "Ready for review",
    reply: { ...context.reply, text: boundedReply(context.reply.text), kind: "scratch", streaming: false } };
  return { ...state, ambient_dialogue: base.ambient_dialogue, notice: base.notice };
}
