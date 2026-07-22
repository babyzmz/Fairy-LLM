import { z } from "zod";

import {
  DEFAULT_FAIRY_MOTION_SNAPSHOT,
  fairyMotionSnapshotSchema,
  type FairyMotionSnapshot,
} from "../domain/motionState";

const CHANNEL_NAME = "fairy.presence.input-presentation.v5";

export interface PresenceInputPresentation {
  schema_version: 5;
  session_id: number;
  sequence: number;
  layout: "core" | "compact" | "expanded";
  capsule_visible: boolean;
  capsule_width: number;
  capsule_height: number;
  motion: FairyMotionSnapshot;
}

export interface PresenceInputPresentationChannel {
  publish(presentation: PresenceInputPresentation): void;
  request(): void;
  onPresentation(
    listener: (presentation: PresenceInputPresentation) => void,
  ): () => void;
  onRequest(listener: () => void): () => void;
  close(): void;
}

interface BroadcastPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: unknown): void;
  close(): void;
}

type BroadcastFactory = (name: string) => BroadcastPort | null;

const presentationSchema = z.object({
  schema_version: z.literal(5),
  session_id: z.number().int().nonnegative(),
  sequence: z.number().int().nonnegative(),
  layout: z.enum(["core", "compact", "expanded"]),
  capsule_visible: z.boolean(),
  capsule_width: z.number().int().min(220).max(360),
  capsule_height: z.number().int().min(64).max(104),
  motion: fairyMotionSnapshotSchema,
}).strict();

const messageSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("input-presentation.request") }).strict(),
  z.object({
    kind: z.literal("input-presentation.snapshot"),
    presentation: presentationSchema,
  }).strict(),
]);

export const DEFAULT_INPUT_PRESENTATION: PresenceInputPresentation = Object.freeze({
  schema_version: 5,
  session_id: 0,
  sequence: 0,
  layout: "core",
  capsule_visible: false,
  capsule_width: 220,
  capsule_height: 64,
  motion: DEFAULT_FAIRY_MOTION_SNAPSHOT,
});

export function createPresenceInputPresentationChannel(
  factory: BroadcastFactory = defaultBroadcastFactory,
): PresenceInputPresentationChannel {
  const presentationListeners = new Set<
    (presentation: PresenceInputPresentation) => void
  >();
  const requestListeners = new Set<() => void>();
  const broadcast = factory(CHANNEL_NAME);

  if (broadcast !== null) {
    broadcast.onmessage = (event) => {
      const parsed = messageSchema.safeParse(event.data);
      if (!parsed.success) return;
      if (parsed.data.kind === "input-presentation.request") {
        for (const listener of requestListeners) listener();
        return;
      }
      for (const listener of presentationListeners) {
        listener(parsed.data.presentation);
      }
    };
  }

  return {
    publish(presentation) {
      const parsed = presentationSchema.safeParse(presentation);
      if (!parsed.success) return;
      broadcast?.postMessage({
        kind: "input-presentation.snapshot",
        presentation: parsed.data,
      });
    },
    request() {
      broadcast?.postMessage({ kind: "input-presentation.request" });
    },
    onPresentation(listener) {
      presentationListeners.add(listener);
      return () => presentationListeners.delete(listener);
    },
    onRequest(listener) {
      requestListeners.add(listener);
      return () => requestListeners.delete(listener);
    },
    close() {
      presentationListeners.clear();
      requestListeners.clear();
      broadcast?.close();
    },
  };
}

export function isNewerInputPresentation(
  candidate: PresenceInputPresentation,
  current: PresenceInputPresentation,
): boolean {
  return candidate.session_id > current.session_id || (
    candidate.session_id === current.session_id && candidate.sequence > current.sequence
  );
}

function defaultBroadcastFactory(name: string): BroadcastPort | null {
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(name);
}
