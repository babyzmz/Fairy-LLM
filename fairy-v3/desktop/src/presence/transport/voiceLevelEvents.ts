import { z } from "zod";

export const PRESENCE_VOICE_LEVEL_CHANNEL = "fairy.presence.voice-level.v1";

const voiceLevelMessageSchema = z.object({
  kind: z.literal("presence.voice-level"),
  level: z.number().min(0).max(1),
  sampled_at_ms: z.number().int().nonnegative(),
}).strict();

export interface PresenceVoiceLevel {
  level: number;
  sampled_at_ms: number;
}

export interface PresenceVoiceLevelSource {
  subscribe(listener: (sample: PresenceVoiceLevel) => void): () => void;
}

let publisher: BroadcastChannel | null = null;

export function publishPresenceVoiceLevel(level: number): void {
  if (typeof BroadcastChannel === "undefined") return;
  publisher ??= new BroadcastChannel(PRESENCE_VOICE_LEVEL_CHANNEL);
  publisher.postMessage({
    kind: "presence.voice-level",
    level: Math.max(0, Math.min(1, Number.isFinite(level) ? level : 0)),
    sampled_at_ms: Date.now(),
  });
}

export function createPresenceVoiceLevelSource(): PresenceVoiceLevelSource {
  return {
    subscribe(listener) {
      if (typeof BroadcastChannel === "undefined") return () => undefined;
      const channel = new BroadcastChannel(PRESENCE_VOICE_LEVEL_CHANNEL);
      const onMessage = (event: MessageEvent<unknown>) => {
        const parsed = voiceLevelMessageSchema.safeParse(event.data);
        if (parsed.success) {
          listener({
            level: parsed.data.level,
            sampled_at_ms: parsed.data.sampled_at_ms,
          });
        }
      };
      channel.addEventListener("message", onMessage);
      return () => {
        channel.removeEventListener("message", onMessage);
        channel.close();
      };
    },
  };
}
