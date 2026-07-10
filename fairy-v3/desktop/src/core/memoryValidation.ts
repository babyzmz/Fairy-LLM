import { z } from "zod";

import type { CoreMethodName } from "./contracts";

const memoryNamespaceSchema = z.enum([
  "project_canonical",
  "conversation_draft",
  "user_profile",
  "device_local",
  "task_episode",
]);

const jsonValueSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.string(),
    z.number().finite(),
    z.boolean(),
    z.null(),
    z.array(jsonValueSchema),
    z.record(z.string(), jsonValueSchema),
  ]),
);

const memoryObservationSchema = z
  .object({
    id: z.uuid(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    version_id: z.uuid().nullable(),
    scope_digest: z.string().regex(/^[0-9a-f]{64}$/),
    source_event_id: z.uuid(),
    source_cursor: z.number().int().positive(),
    source_type: z.enum([
      "user_message",
      "core_event",
      "command_result",
      "accepted_artifact",
      "explicit_user_action",
      "model_suggestion",
    ]),
    content: z.string(),
    content_hash: z.string().regex(/^[0-9a-f]{64}$/),
    proposed_namespace: memoryNamespaceSchema,
    authority: z.enum([
      "deterministic_core",
      "explicit_user",
      "accepted_version",
      "model_suggestion",
    ]),
    confidence: z.number().min(0).max(1),
    sensitivity: z.enum(["public", "private", "secret"]),
    scan_result: z.enum([
      "unchecked",
      "clean",
      "injection_blocked",
      "secret_blocked",
    ]),
    status: z.enum(["pending", "accepted", "rejected", "promoted", "forgotten"]),
    actor: z.string().min(1),
    created_at: z.string().min(1),
  })
  .strict();

const memoryClaimSchema = z
  .object({
    id: z.uuid(),
    namespace: memoryNamespaceSchema,
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid().nullable(),
    task_id: z.uuid().nullable(),
    version_id: z.uuid().nullable(),
    device_id: z.string().nullable(),
    subject: z.string().min(1),
    predicate: z.string().min(1),
    current_revision: z.number().int().nonnegative(),
    conflict_set_id: z.uuid().nullable(),
    status: z.enum([
      "candidate",
      "active",
      "conflicted",
      "superseded",
      "expired",
      "rejected",
      "forgotten",
    ]),
    created_at: z.string().min(1),
    updated_at: z.string().min(1),
  })
  .strict();

const memoryClaimRevisionSchema = z
  .object({
    claim_id: z.uuid(),
    revision: z.number().int().positive(),
    value: jsonValueSchema,
    normalized_text: z.string().min(1),
    source_observation_ids: z.array(z.uuid()),
    source_event_ids: z.array(z.uuid()),
    authority: z.enum([
      "deterministic_core",
      "explicit_user",
      "accepted_version",
      "model_suggestion",
    ]),
    confidence: z.number().min(0).max(1),
    valid_from: z.string().nullable(),
    valid_to: z.string().nullable(),
    recorded_at: z.string().min(1),
    actor: z.string().min(1),
    supersedes_revision: z.number().int().positive().nullable(),
    resolved_claim_ids: z.array(z.uuid()),
  })
  .strict();

const memoryClaimContextSchema = z
  .object({
    claim: memoryClaimSchema,
    current_revision: memoryClaimRevisionSchema,
  })
  .strict();

const memoryTombstoneSchema = z
  .object({
    id: z.uuid(),
    target_kind: z.enum(["observation", "claim", "episode", "snapshot"]),
    target_id: z.uuid(),
    reason: z.string().min(1),
    actor: z.string().min(1),
    source_event_id: z.uuid(),
    created_at: z.string().min(1),
  })
  .strict();

const memoryResultSchemas: Partial<Record<CoreMethodName, z.ZodType<unknown>>> = {
  "memory.observations.create": memoryObservationSchema,
  "memory.observations.list": z.object({ items: z.array(memoryObservationSchema) }).strict(),
  "memory.claims.promote": memoryClaimContextSchema,
  "memory.claims.get": memoryClaimContextSchema,
  "memory.claims.list": z.object({ items: z.array(memoryClaimContextSchema) }).strict(),
  "memory.claims.supersede": memoryClaimContextSchema,
  "memory.claims.resolve_conflict": memoryClaimContextSchema,
  "memory.forget": memoryTombstoneSchema,
};

export function parseMemoryResult(method: CoreMethodName, payload: unknown): unknown {
  const schema = memoryResultSchemas[method];
  return schema ? schema.parse(payload) : payload;
}
