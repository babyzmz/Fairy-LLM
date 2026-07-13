import { z } from "zod";

const physicalPointSchema = z.object({
  x: z.number().int(),
  y: z.number().int(),
}).strict();

const physicalFrameSchema = z.object({
  x: z.number().int(),
  y: z.number().int(),
  width: z.number().int().nonnegative(),
  height: z.number().int().nonnegative(),
}).strict();

export const presenceInteractionSnapshotSchema = z.object({
  schema_version: z.literal(1),
  sequence: z.number().int().nonnegative(),
  sampled_at_ms: z.number().int().nonnegative(),
  phase: z.enum([
    "idle",
    "aware",
    "droplet",
    "stretching",
    "input_reveal",
    "interactive",
    "returning",
    "suspended",
    "repositioning",
  ]),
  phase_started_at_ms: z.number().int().nonnegative(),
  reduced_motion: z.boolean(),
  cursor: z.object({
    point: physicalPointSchema,
    direction: z.object({
      x: z.number().min(-1).max(1),
      y: z.number().min(-1).max(1),
    }).strict(),
    distance_px: z.number().nonnegative(),
    speed_px_s: z.number().nonnegative(),
    dwell_ms: z.number().int().nonnegative(),
    band: z.enum(["outside", "aware", "active"]),
  }).strict(),
  placement: z.object({
    anchor: physicalPointSchema,
    render_frame: physicalFrameSchema,
    input_compact_frame: physicalFrameSchema,
    input_expanded_frame: physicalFrameSchema,
    monitor_work_area: physicalFrameSchema,
    scale_factor: z.number().min(0.5).max(4),
    expansion_direction: z.enum(["left", "right"]),
  }).strict(),
}).strict();

export type PresenceInteractionSnapshot = z.infer<
  typeof presenceInteractionSnapshotSchema
>;
