import { z } from "zod";

import type { CoreMethodName } from "./contracts";

const memoryNamespaceSchema = z.enum([
  "project_canonical",
  "conversation_draft",
  "user_profile",
  "device_local",
  "task_episode",
]);

const sha256Schema = z.string().regex(/^[0-9a-f]{64}$/);
const memoryAuthoritySchema = z.enum([
  "deterministic_core",
  "explicit_user",
  "accepted_version",
  "model_suggestion",
]);
const memorySourceKindSchema = z.enum([
  "claim_revision",
  "observation",
  "domain_event",
]);
const projectionStateSchema = z.enum(["ready", "stale", "unavailable", "failed"]);

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
    authority: memoryAuthoritySchema,
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
    authority: memoryAuthoritySchema,
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

const memorySearchDocumentSchema = z
  .object({
    id: z.uuid(),
    source_kind: memorySourceKindSchema,
    source_id: z.uuid(),
    source_revision: z.number().int().positive().nullable(),
    namespace: memoryNamespaceSchema.nullable(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid().nullable(),
    task_id: z.uuid().nullable(),
    version_id: z.uuid().nullable(),
    language: z.string().min(1).max(32),
    normalized_text: z.string().min(1),
    content_hash: sha256Schema,
    source_cursor: z.number().int().positive(),
    projection_generation: z.number().int().positive(),
    updated_at: z.string().min(1),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.source_kind === "claim_revision" && value.source_revision === null) {
      context.addIssue({
        code: "custom",
        message: "Claim revision document requires source_revision",
      });
    }
    if (value.namespace === "project_canonical" && value.project_id === null) {
      context.addIssue({ code: "custom", message: "Project memory requires project_id" });
    }
    if (
      value.namespace === "conversation_draft" &&
      value.conversation_id === null
    ) {
      context.addIssue({
        code: "custom",
        message: "Conversation memory requires conversation_id",
      });
    }
  });

const memorySearchHitSchema = z
  .object({
    document: memorySearchDocumentSchema,
    lexical_score: z.number().finite().min(0).max(1),
    exact_match: z.boolean(),
  })
  .strict();

const scoreComponentsSchema = z
  .record(z.string().min(1), z.number().finite().min(0).max(1));

const memorySnapshotItemSchema = z
  .object({
    ordinal: z.number().int().nonnegative(),
    source_kind: memorySourceKindSchema,
    source_id: z.uuid(),
    source_revision: z.number().int().positive().nullable(),
    namespace: memoryNamespaceSchema.nullable(),
    selection_reason: z.enum([
      "exact_canonical",
      "canonical",
      "exact_profile",
      "user_profile",
      "conversation_draft",
      "lexical_history",
      "relational_fallback",
      "conflict_disclosure",
    ]),
    authority: memoryAuthoritySchema,
    score_components: scoreComponentsSchema,
    rendered_text: z.string().min(1),
    rendered_text_hash: sha256Schema,
    token_count: z.number().int().positive().max(3000),
  })
  .strict();

const memorySnapshotSchema = z
  .object({
    id: z.uuid(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    base_version_id: z.uuid().nullable(),
    target_version_id: z.uuid().nullable(),
    snapshot_version: z.number().int().positive(),
    policy_version: z.string().min(1).max(128),
    source_watermark_cursor: z.number().int().nonnegative(),
    projection_generation: z.number().int().positive(),
    projection_watermark_cursor: z.number().int().nonnegative(),
    projection_state: projectionStateSchema,
    status: z.enum(["ready", "degraded"]),
    degraded_reason: z.string().min(1).max(128).nullable(),
    content_hash: sha256Schema,
    token_count: z.number().int().nonnegative().max(3000),
    items: z.array(memorySnapshotItemSchema),
    created_at: z.string().min(1),
  })
  .strict()
  .superRefine((value, context) => {
    const ordinalsAreContiguous = value.items.every(
      (item, index) => item.ordinal === index,
    );
    if (!ordinalsAreContiguous) {
      context.addIssue({ code: "custom", message: "Snapshot ordinals are not contiguous" });
    }
    const tokenCount = value.items.reduce((total, item) => total + item.token_count, 0);
    if (tokenCount !== value.token_count) {
      context.addIssue({ code: "custom", message: "Snapshot token count is inconsistent" });
    }
    if (
      value.status === "ready" &&
      (value.projection_state !== "ready" ||
        value.projection_watermark_cursor < value.source_watermark_cursor ||
        value.degraded_reason !== null)
    ) {
      context.addIssue({ code: "custom", message: "READY Snapshot is inconsistent" });
    }
    if (
      value.status === "degraded" &&
      (value.projection_state === "ready" || value.degraded_reason === null)
    ) {
      context.addIssue({ code: "custom", message: "DEGRADED Snapshot is inconsistent" });
    }
  });

const memoryProjectionHealthSchema = z
  .object({
    generation: z.number().int().positive(),
    state: projectionStateSchema,
    source_watermark_cursor: z.number().int().nonnegative(),
    projected_watermark_cursor: z.number().int().nonnegative(),
    lag: z.number().int().nonnegative(),
    last_error_code: z.string().min(1).max(128).nullable(),
    updated_at: z.string().min(1),
  })
  .strict()
  .superRefine((value, context) => {
    const expectedLag = Math.max(
      0,
      value.source_watermark_cursor - value.projected_watermark_cursor,
    );
    if (value.lag !== expectedLag) {
      context.addIssue({ code: "custom", message: "Projection lag is inconsistent" });
    }
    if (
      value.state === "ready" &&
      (value.lag !== 0 || value.last_error_code !== null)
    ) {
      context.addIssue({ code: "custom", message: "READY projection is inconsistent" });
    }
  });

const memoryResultSchemas: Partial<Record<CoreMethodName, z.ZodType<unknown>>> = {
  "memory.observations.create": memoryObservationSchema,
  "memory.observations.list": z.object({ items: z.array(memoryObservationSchema) }).strict(),
  "memory.claims.promote": memoryClaimContextSchema,
  "memory.claims.get": memoryClaimContextSchema,
  "memory.claims.list": z.object({ items: z.array(memoryClaimContextSchema) }).strict(),
  "memory.claims.supersede": memoryClaimContextSchema,
  "memory.claims.resolve_conflict": memoryClaimContextSchema,
  "memory.forget": memoryTombstoneSchema,
  "memory.search": z.object({ items: z.array(memorySearchHitSchema) }).strict(),
  "memory.snapshots.get": memorySnapshotSchema,
  "memory.projection.health": memoryProjectionHealthSchema,
};

export function parseMemoryResult(method: CoreMethodName, payload: unknown): unknown {
  const schema = memoryResultSchemas[method];
  return schema ? schema.parse(payload) : payload;
}
