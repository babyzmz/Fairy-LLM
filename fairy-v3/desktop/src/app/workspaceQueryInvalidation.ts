import type { EventEnvelope } from "../core/client";

export type WorkspaceInvalidationDomain =
  | "approvals"
  | "browser"
  | "conversations"
  | "extensions"
  | "knowledge"
  | "media"
  | "messages"
  | "permissions"
  | "previews"
  | "projects"
  | "providers"
  | "tasks"
  | "traces"
  | "voice"
  | "workspace";

export interface WorkspaceInvalidationBatch {
  readonly domains: Set<WorkspaceInvalidationDomain>;
  readonly conversationIds: Set<string>;
  readonly projectIds: Set<string>;
  readonly taskIds: Set<string>;
  readonly turnIds: Set<string>;
}

export function createWorkspaceInvalidationBatch(): WorkspaceInvalidationBatch {
  return {
    domains: new Set(),
    conversationIds: new Set(),
    projectIds: new Set(),
    taskIds: new Set(),
    turnIds: new Set(),
  };
}

export function addWorkspaceInvalidation(
  batch: WorkspaceInvalidationBatch,
  domains: readonly WorkspaceInvalidationDomain[],
  scope: {
    conversationId?: string | null;
    projectId?: string | null;
    taskId?: string | null;
    turnId?: string | null;
  } = {},
): void {
  for (const domain of domains) batch.domains.add(domain);
  addScopeValue(batch.conversationIds, scope.conversationId);
  addScopeValue(batch.projectIds, scope.projectId);
  addScopeValue(batch.taskIds, scope.taskId);
  addScopeValue(batch.turnIds, scope.turnId);
}

export function addWorkspaceEventInvalidation(
  batch: WorkspaceInvalidationBatch,
  event: EventEnvelope,
): void {
  if (event.event_type === "assistant.message.delta") return;
  const scope = {
    conversationId: event.conversation_id,
    projectId: event.project_id,
    taskId: event.task_id,
    turnId: payloadString(event.payload, "turn_id"),
  };
  const type = event.event_type;

  if (type === "message.created") {
    addWorkspaceInvalidation(batch, ["messages", "tasks", "traces", "conversations"], scope);
    return;
  }
  if (type.startsWith("turn.trace.")) {
    addWorkspaceInvalidation(batch, ["traces"], scope);
    return;
  }
  if (type.startsWith("assistant.turn.")) {
    const terminal = type.endsWith(".completed") || type.endsWith(".cancelled") || type.endsWith(".failed");
    addWorkspaceInvalidation(
      batch,
      terminal
        ? ["messages", "tasks", "traces", "approvals", "workspace", "previews"]
        : ["tasks", "traces"],
      scope,
    );
    return;
  }
  if (type.startsWith("assistant.")) {
    addWorkspaceInvalidation(
      batch,
      type.includes("approval") ? ["traces", "approvals", "tasks"] : ["traces"],
      scope,
    );
    return;
  }
  if (type.startsWith("command.")) {
    const terminal = [
      "command.cancelled",
      "command.failed",
      "command.failure",
      "command.interrupted",
      "command.succeeded",
    ].includes(type);
    addWorkspaceInvalidation(
      batch,
      terminal ? ["traces", "tasks", "workspace"] : ["traces"],
      scope,
    );
    return;
  }
  if (type.startsWith("approval.")) {
    addWorkspaceInvalidation(batch, ["approvals", "tasks", "traces"], scope);
    return;
  }
  if (type.startsWith("preview.") || type.startsWith("runtime.")) {
    addWorkspaceInvalidation(batch, ["previews"], scope);
    return;
  }
  if (type.startsWith("browser.")) {
    addWorkspaceInvalidation(batch, ["browser"], scope);
    return;
  }
  if (
    type.startsWith("workspace.") ||
    type.startsWith("version.") ||
    type.startsWith("changeset.") ||
    type.startsWith("artifact.")
  ) {
    addWorkspaceInvalidation(batch, ["workspace", "tasks"], scope);
    return;
  }
  if (type.startsWith("media.")) {
    addWorkspaceInvalidation(batch, ["media"], scope);
    return;
  }
  if (type.startsWith("project.")) {
    addWorkspaceInvalidation(batch, ["projects"], scope);
    return;
  }
  if (type.startsWith("conversation.")) {
    addWorkspaceInvalidation(batch, ["conversations"], scope);
    return;
  }
  if (
    type.startsWith("knowledge.") ||
    type.startsWith("memory.") ||
    type.startsWith("obsidian.") ||
    type.startsWith("document.")
  ) {
    addWorkspaceInvalidation(batch, ["knowledge"], scope);
    return;
  }
  if (type.startsWith("provider.") || type.startsWith("model.")) {
    addWorkspaceInvalidation(batch, ["providers"], scope);
    return;
  }
  if (type.startsWith("skill.") || type.startsWith("mcp.")) {
    addWorkspaceInvalidation(batch, ["extensions"], scope);
    return;
  }
  if (type.startsWith("capability.") || type.startsWith("permission.")) {
    addWorkspaceInvalidation(batch, ["permissions"], scope);
    return;
  }
  if (type.startsWith("voice.") || type.startsWith("realtime.")) {
    addWorkspaceInvalidation(batch, ["voice"], scope);
    return;
  }
  if (event.task_id !== null) {
    addWorkspaceInvalidation(batch, ["tasks", "traces"], scope);
  }
}

export function workspaceQueryMatchesInvalidation(
  queryKey: readonly unknown[],
  batch: WorkspaceInvalidationBatch,
): boolean {
  const root = keyPart(queryKey, 0);
  const kind = keyPart(queryKey, 1);

  if (root === "settings") {
    const category = keyPart(queryKey, 2);
    return (
      (batch.domains.has("projects") && category === "general") ||
      (batch.domains.has("conversations") && category === "general") ||
      (batch.domains.has("providers") && category === "models") ||
      (batch.domains.has("permissions") && category === "permissions") ||
      (batch.domains.has("extensions") && category === "extensions") ||
      (batch.domains.has("knowledge") && category === "knowledge") ||
      (batch.domains.has("voice") && category === "voice")
    );
  }
  if (root === "models") {
    return batch.domains.has("providers");
  }
  if (root !== "workspace") return false;

  if (batch.domains.has("messages") && ["messages", "project-messages"].includes(kind)) {
    return scopeMatches(keyPart(queryKey, 2), batch.conversationIds);
  }
  if (batch.domains.has("tasks") && kind === "tasks") return true;
  if (batch.domains.has("traces") && kind === "turn-trace") {
    return scopeMatches(keyPart(queryKey, 2), batch.turnIds);
  }
  if (batch.domains.has("approvals") && ["approvals", "chat-approvals"].includes(kind)) {
    return scopeMatches(keyPart(queryKey, 2), batch.taskIds);
  }
  if (batch.domains.has("projects") && kind === "projects") return true;
  if (batch.domains.has("conversations") && kind === "conversations") return true;
  if (
    batch.domains.has("providers") &&
    ["providers", "provider-health", "openrouter-status"].includes(kind)
  ) {
    return true;
  }
  if (batch.domains.has("permissions") && ["permissions", "capabilities"].includes(kind)) {
    return true;
  }
  if (batch.domains.has("extensions") && ["skills", "mcp-servers"].includes(kind)) {
    return true;
  }
  if (batch.domains.has("knowledge") && ["knowledge", "obsidian"].includes(kind)) {
    return knowledgeScopeMatches(queryKey, batch.projectIds);
  }
  if (batch.domains.has("media") && kind === "media-jobs") {
    return executionScopeMatches(queryKey, batch);
  }
  if (batch.domains.has("media") && kind === "asset-sets") {
    return scopeMatches(keyPart(queryKey, 2), batch.conversationIds);
  }
  if (
    batch.domains.has("previews") &&
    ["preview", "runtime-health"].includes(kind)
  ) {
    return executionScopeMatches(queryKey, batch);
  }
  if (
    batch.domains.has("workspace") &&
    ["workspace", "files", "asset-sets", "versions"].includes(kind)
  ) {
    if (kind === "versions") return scopeMatches(keyPart(queryKey, 2), batch.projectIds);
    return scopeMatches(keyPart(queryKey, 2), batch.conversationIds);
  }
  if (batch.domains.has("browser") && kind === "browser-health") return true;
  if (batch.domains.has("browser") && kind === "browser-sessions") {
    return executionScopeMatches(queryKey, batch);
  }
  if (batch.domains.has("browser") && kind === "browser-snapshot") return true;
  return false;
}

function executionScopeMatches(
  queryKey: readonly unknown[],
  batch: WorkspaceInvalidationBatch,
): boolean {
  const conversationMatches = scopeMatches(keyPart(queryKey, 2), batch.conversationIds);
  const taskMatches = scopeMatches(keyPart(queryKey, 3), batch.taskIds);
  return conversationMatches && taskMatches;
}

function knowledgeScopeMatches(
  queryKey: readonly unknown[],
  projectIds: ReadonlySet<string>,
): boolean {
  const projectId = keyPart(queryKey, 3);
  return scopeMatches(projectId, projectIds);
}

function scopeMatches(value: string, values: ReadonlySet<string>): boolean {
  return values.size === 0 || value.length === 0 || values.has(value);
}

function addScopeValue(values: Set<string>, value: string | null | undefined): void {
  if (value !== null && value !== undefined && value.length > 0) values.add(value);
}

function keyPart(queryKey: readonly unknown[], index: number): string {
  const value = queryKey[index];
  return typeof value === "string" ? value : "";
}

function payloadString(payload: Record<string, unknown>, name: string): string | null {
  const value = payload[name];
  return typeof value === "string" && value.length > 0 ? value : null;
}
