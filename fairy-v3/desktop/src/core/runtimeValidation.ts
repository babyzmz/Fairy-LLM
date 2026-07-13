import { z } from "zod";

import type { CoreMethodName } from "./contracts";

const executionTargetSchema = z.enum(["local", "cloud"]);
const runtimeStatusSchema = z.enum([
  "created",
  "starting",
  "running",
  "stopping",
  "stopped",
  "failed",
  "interrupted",
]);
const previewStatusSchema = z.enum([
  "created",
  "starting",
  "ready",
  "stopping",
  "stopped",
  "failed",
  "interrupted",
]);
const timestampSchema = z.string().min(1);

const runtimeSchema = z
  .object({
    id: z.uuid(),
    project_id: z.uuid().nullable(),
    workspace_id: z.uuid(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    version_id: z.uuid(),
    project_root: z.string().min(1),
    execution_target: executionTargetSchema,
    kind: z.enum(["static_site", "wsl_project", "cloud_oci"]),
    executor: z.string().min(1),
    executor_handle: z.string().min(1).nullable(),
    port: z.number().int().min(1).max(65_535).nullable(),
    status: runtimeStatusSchema,
    health: z.enum([
      "unknown",
      "starting",
      "healthy",
      "stopping",
      "stopped",
      "unhealthy",
      "interrupted",
    ]),
    error_code: z.string().min(1).nullable(),
    idempotency_key: z.string().min(1),
    revision: z.number().int().nonnegative(),
    created_at: timestampSchema,
    updated_at: timestampSchema,
  })
  .strict()
  .superRefine((value, context) => {
    const endpointPaired = value.executor_handle !== null && value.port !== null;
    const endpointMismatched = (value.executor_handle === null) !== (value.port === null);
    const active = value.status === "running" || value.status === "stopping";
    const inactive = ["created", "starting", "stopped"].includes(value.status);
    if (endpointMismatched || (active && !endpointPaired) || (inactive && endpointPaired)) {
      context.addIssue({ code: "custom", message: "Runtime endpoint state is inconsistent" });
    }
    if (
      value.kind === "static_site" &&
      (value.execution_target !== "local" ||
        value.project_id === null ||
        value.version_id === null)
    ) {
      context.addIssue({ code: "custom", message: "Static Runtime scope is inconsistent" });
    }
  });

const previewSchema = z
  .object({
    id: z.uuid(),
    project_id: z.uuid().nullable(),
    workspace_id: z.uuid(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    version_id: z.uuid(),
    runtime_id: z.uuid(),
    project_root: z.string().min(1),
    execution_target: executionTargetSchema,
    url: z.string().min(1).nullable(),
    visibility: z.enum(["chat_draft", "project_active", "private"]),
    status: previewStatusSchema,
    health: z.enum([
      "unknown",
      "starting",
      "healthy",
      "stopping",
      "stopped",
      "unhealthy",
      "interrupted",
    ]),
    error_code: z.string().min(1).nullable(),
    idempotency_key: z.string().min(1),
    revision: z.number().int().nonnegative(),
    created_at: timestampSchema,
    updated_at: timestampSchema,
  })
  .strict()
  .superRefine((value, context) => {
    const requiresUrl = value.status === "ready" || value.status === "stopping";
    const forbidsUrl = ["created", "starting", "stopped", "failed"].includes(
      value.status,
    );
    if ((requiresUrl && value.url === null) || (forbidsUrl && value.url !== null)) {
      context.addIssue({ code: "custom", message: "Preview URL state is inconsistent" });
      return;
    }
    if (value.url === null) return;
    let parsed: URL;
    try {
      parsed = new URL(value.url);
    } catch {
      context.addIssue({ code: "custom", message: "Preview URL is invalid" });
      return;
    }
    const hasCredentials = parsed.username !== "" || parsed.password !== "";
    const hasUnscopedSuffix = parsed.search !== "" || parsed.hash !== "";
    const invalidLocal =
      value.execution_target === "local" &&
      (parsed.protocol !== "http:" ||
        parsed.hostname !== "127.0.0.1" ||
        parsed.port === "");
    const invalidCloud =
      value.execution_target === "cloud" && parsed.protocol !== "https:";
    if (hasCredentials || hasUnscopedSuffix || invalidLocal || invalidCloud) {
      context.addIssue({ code: "custom", message: "Preview URL is outside Runtime scope" });
    }
  });

const taskSchema = z
  .object({
    id: z.uuid(),
    conversation_id: z.uuid(),
    status: z.string().min(1),
  })
  .loose();

const previewContextSchema = z
  .object({ task: taskSchema, runtime: runtimeSchema, preview: previewSchema })
  .strict();

const runtimeExecutorHealthSchema = z
  .object({
    available: z.boolean(),
    executor: z.string().min(1),
    version: z.string().nullable(),
    error_code: z.string().min(1).nullable(),
    diagnostics: z.array(z.string()),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.available === (value.error_code !== null)) {
      context.addIssue({ code: "custom", message: "Executor health is inconsistent" });
    }
  });

const runtimeHealthSchema = z
  .object({
    executor: runtimeExecutorHealthSchema,
    runtime: runtimeSchema.nullable(),
    preview: previewSchema.nullable(),
  })
  .strict();

const artifactSchema = z
  .object({
    id: z.uuid(),
    project_id: z.uuid().nullable(),
    conversation_id: z.uuid(),
    task_id: z.uuid(),
    version_id: z.uuid().nullable(),
    artifact_type: z.enum([
      "document",
      "prompt",
      "patch",
      "component",
      "full_project",
      "preview_manifest",
      "preview_snapshot",
      "report",
      "log",
      "screenshot",
    ]),
    visibility: z.enum(["conversation", "project", "private"]),
    storage_location: z.string().min(1),
    media_type: z.string().includes("/"),
    byte_length: z.number().int().nonnegative(),
    content_hash: z.string().regex(/^[0-9a-f]{64}$/),
    metadata: z.record(z.string(), z.unknown()),
    created_at: timestampSchema,
  })
  .strict();

const runtimeResultSchemas: Partial<Record<CoreMethodName, z.ZodType<unknown>>> = {
  "runtimes.get": runtimeSchema,
  "runtimes.health": runtimeHealthSchema,
  "previews.start": previewContextSchema,
  "previews.get": previewContextSchema,
  "previews.resolve": previewContextSchema.nullable(),
  "previews.stop": previewSchema,
  "artifacts.list": z.object({ items: z.array(artifactSchema) }).strict(),
  "artifacts.read": artifactSchema,
};

export function parseRuntimeResult(method: CoreMethodName, payload: unknown): unknown {
  const schema = runtimeResultSchemas[method];
  return schema ? schema.parse(payload) : payload;
}
