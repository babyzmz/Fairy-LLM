import { z } from "zod";

const CHANNEL_NAME = "fairy.presence.input-presentation.v1";

export interface PresenceInputPresentation {
  schema_version: 1;
  sequence: number;
  layout: "core" | "compact" | "expanded";
  capsule_visible: boolean;
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
  schema_version: z.literal(1),
  sequence: z.number().int().nonnegative(),
  layout: z.enum(["core", "compact", "expanded"]),
  capsule_visible: z.boolean(),
}).strict();

const messageSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("input-presentation.request") }).strict(),
  z.object({
    kind: z.literal("input-presentation.snapshot"),
    presentation: presentationSchema,
  }).strict(),
]);

export const DEFAULT_INPUT_PRESENTATION: PresenceInputPresentation = Object.freeze({
  schema_version: 1,
  sequence: 0,
  layout: "core",
  capsule_visible: false,
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

function defaultBroadcastFactory(name: string): BroadcastPort | null {
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(name);
}
