import { z } from "zod";

import {
  type PresenceProjectionState,
  presenceProjectionStateSchema,
} from "../domain/projection";

export type PresenceRequest =
  | { kind: "projection" }
  | { kind: "workspace.open" }
  | { kind: "realtime.open" }
  | { kind: "chat.new" }
  | { kind: "chat.send"; submission_id: string; text: string }
  | { kind: "chat.cancel"; submission_id: string }
  | { kind: "input.state"; open: boolean; focused: boolean }
  | { kind: "voice.stop" };

export type PresenceSubmissionFailure = "offline" | "busy" | "unavailable";

export interface PresenceSubmissionUpdate {
  submission_id: string;
  status: "accepted" | "failed" | "cancelled";
  failure: PresenceSubmissionFailure | null;
}

export interface PresenceChannel {
  publishProjection(projection: PresenceProjectionState): void;
  publishSubmission(update: PresenceSubmissionUpdate): void;
  requestProjection(): void;
  requestWorkspaceOpen(): void;
  requestRealtimeOpen?(): void;
  requestNewChat(): void;
  requestChatSend(text: string, submissionId: string): void;
  requestChatCancel(submissionId: string): void;
  requestVoiceStop(): void;
  publishInputState?(open: boolean, focused: boolean): void;
  onProjection(listener: (projection: PresenceProjectionState) => void): () => void;
  onSubmission(listener: (update: PresenceSubmissionUpdate) => void): () => void;
  onRequest(listener: (request: PresenceRequest) => void): () => void;
  close(): void;
}

let fallbackSubmissionSequence = 0;

export function createPresenceSubmissionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  fallbackSubmissionSequence += 1;
  return `pet-${Date.now().toString(36)}-${fallbackSubmissionSequence.toString(36)}`;
}

const submissionIdSchema = z.string().min(1).max(128);

const requestSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("projection") }).strict(),
  z.object({ kind: z.literal("workspace.open") }).strict(),
  z.object({ kind: z.literal("realtime.open") }).strict(),
  z.object({ kind: z.literal("chat.new") }).strict(),
  z
    .object({
      kind: z.literal("chat.send"),
      submission_id: submissionIdSchema,
      text: z.string().trim().min(1).max(4_000),
    })
    .strict(),
  z
    .object({
      kind: z.literal("chat.cancel"),
      submission_id: submissionIdSchema,
    })
    .strict(),
  z.object({ kind: z.literal("voice.stop") }).strict(),
  z
    .object({
      kind: z.literal("input.state"),
      open: z.boolean(),
      focused: z.boolean(),
    })
    .strict(),
]);

const submissionSchema = z
  .object({
    submission_id: submissionIdSchema,
    status: z.enum(["accepted", "failed", "cancelled"]),
    failure: z.enum(["offline", "busy", "unavailable"]).nullable(),
  })
  .strict()
  .refine(
    (value) => (value.status === "failed") === (value.failure !== null),
    { message: "Only failed submissions may include a failure category" },
  );

const messageSchema = z.discriminatedUnion("kind", [
  z
    .object({ kind: z.literal("presence.projection"), projection: presenceProjectionStateSchema })
    .strict(),
  z
    .object({ kind: z.literal("presence.request"), request: requestSchema })
    .strict(),
  z
    .object({ kind: z.literal("presence.submission"), update: submissionSchema })
    .strict(),
]);

export function createPresenceChannel(): PresenceChannel {
  const projectionListeners = new Set<(value: PresenceProjectionState) => void>();
  const submissionListeners = new Set<(value: PresenceSubmissionUpdate) => void>();
  const requestListeners = new Set<(value: PresenceRequest) => void>();
  const broadcast =
    typeof window !== "undefined" && "BroadcastChannel" in window
      ? new window.BroadcastChannel("fairy.presence.v2")
      : null;

  if (broadcast !== null) {
    broadcast.onmessage = (event: MessageEvent<unknown>) => {
      const parsed = messageSchema.safeParse(event.data);
      if (!parsed.success) return;
      if (parsed.data.kind === "presence.projection") {
        for (const listener of projectionListeners) listener(parsed.data.projection);
      } else if (parsed.data.kind === "presence.request") {
        for (const listener of requestListeners) listener(parsed.data.request);
      } else {
        for (const listener of submissionListeners) listener(parsed.data.update);
      }
    };
  }

  const postRequest = (request: PresenceRequest) => {
    const parsed = requestSchema.safeParse(request);
    if (parsed.success) {
      broadcast?.postMessage({ kind: "presence.request", request: parsed.data });
    }
  };

  return {
    publishProjection(projection) {
      const parsed = presenceProjectionStateSchema.safeParse(projection);
      if (parsed.success) {
        broadcast?.postMessage({
          kind: "presence.projection",
          projection: parsed.data,
        });
      }
    },
    publishSubmission(update) {
      const parsed = submissionSchema.safeParse(update);
      if (parsed.success) {
        broadcast?.postMessage({
          kind: "presence.submission",
          update: parsed.data,
        });
      }
    },
    requestProjection: () => postRequest({ kind: "projection" }),
    requestWorkspaceOpen: () => postRequest({ kind: "workspace.open" }),
    requestRealtimeOpen: () => postRequest({ kind: "realtime.open" }),
    requestNewChat: () => postRequest({ kind: "chat.new" }),
    requestChatSend: (text, submissionId) =>
      postRequest({ kind: "chat.send", submission_id: submissionId, text }),
    requestChatCancel: (submissionId) =>
      postRequest({ kind: "chat.cancel", submission_id: submissionId }),
    requestVoiceStop: () => postRequest({ kind: "voice.stop" }),
    publishInputState: (open, focused) =>
      postRequest({ kind: "input.state", open, focused }),
    onProjection(listener) {
      projectionListeners.add(listener);
      return () => projectionListeners.delete(listener);
    },
    onSubmission(listener) {
      submissionListeners.add(listener);
      return () => submissionListeners.delete(listener);
    },
    onRequest(listener) {
      requestListeners.add(listener);
      return () => requestListeners.delete(listener);
    },
    close() {
      projectionListeners.clear();
      submissionListeners.clear();
      requestListeners.clear();
      broadcast?.close();
    },
  };
}
