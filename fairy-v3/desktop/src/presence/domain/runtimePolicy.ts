import { z } from "zod";

export const presenceRuntimePolicySchema = z.object({
  schema_version: z.literal(1),
  frame_rate_limit: z.union([
    z.literal(15),
    z.literal(30),
    z.literal(60),
    z.literal(144),
  ]),
  power_saver: z.boolean(),
  foreground_fullscreen: z.boolean(),
}).strict();

export type PresenceRuntimePolicy = z.infer<typeof presenceRuntimePolicySchema>;

export const DEFAULT_PRESENCE_RUNTIME_POLICY: PresenceRuntimePolicy = Object.freeze({
  schema_version: 1,
  frame_rate_limit: 144,
  power_saver: false,
  foreground_fullscreen: false,
});
