import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";

export const PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT =
  "presence-native-renderer-lifecycle";

export const nativeRendererLifecycleSignalSchema = z.object({
  schema_version: z.literal(1),
  reason: z.enum([
    "resume",
    "surface_changed",
    "shutdown",
    "drag_ended",
  ]),
}).strict();

export type NativeRendererLifecycleSignal = z.infer<
  typeof nativeRendererLifecycleSignalSchema
>;

export interface NativeRendererLifecycleSource {
  subscribe(
    listener: (signal: NativeRendererLifecycleSignal) => void,
  ): Promise<() => void>;
}

export function createNativeRendererLifecycleSource(): NativeRendererLifecycleSource {
  if (!isTauri()) {
    return { async subscribe() { return () => undefined; } };
  }
  return {
    async subscribe(listener) {
      return listen<unknown>(PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT, (event) => {
        const signal = nativeRendererLifecycleSignalSchema.safeParse(event.payload);
        if (signal.success) listener(signal.data);
      });
    },
  };
}
