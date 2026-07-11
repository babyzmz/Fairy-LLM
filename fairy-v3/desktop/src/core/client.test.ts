import { describe, expect, it } from "vitest";

import {
  CoreClient,
  DEFAULT_EVENT_POLL_MS,
  type CoreMethodMap,
  type CoreMethodName,
  type CoreTransport,
  type EventEnvelope,
  type EventSubscriptionOptions,
} from "./client";

class RecordingTransport implements CoreTransport {
  requests: Array<{ method: CoreMethodName; params: unknown }> = [];
  subscriptions: Array<{ cursor: number; signal?: AbortSignal }> = [];

  async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
  ): Promise<CoreMethodMap[M]["result"]> {
    this.requests.push({ method, params });
    return { status: "ok" } as CoreMethodMap[M]["result"];
  }

  async *subscribeEvents(
    cursor: number,
    options: EventSubscriptionOptions = {},
  ): AsyncIterable<EventEnvelope> {
    this.subscriptions.push({ cursor, signal: options.signal });
    yield {
      id: "0198f4de-0114-7000-8000-000000000001",
      cursor: cursor + 1,
      run_id: null,
      project_id: null,
      conversation_id: "0198f4de-0114-7000-8000-000000000002",
      task_id: "0198f4de-0114-7000-8000-000000000003",
      version_id: null,
      task_sequence: 1,
      event_type: "task.created",
      visibility: "user",
      message: "Task created",
      payload: {},
      schema_version: 1,
      created_at: "2026-07-10T00:00:00Z",
    };
  }
}

describe("CoreClient", () => {
  it("keeps local ledger polling inside the event latency budget", () => {
    expect(DEFAULT_EVENT_POLL_MS).toBeLessThanOrEqual(50);
  });

  it("exposes one typed method surface for local and cloud transports", async () => {
    const transport = new RecordingTransport();
    const client = new CoreClient(transport);
    const id = "0198f4de-0114-7000-8000-000000000001";

    await client.health();
    await client.projects.create({ name: "Example", residency: "local_only" });
    await client.projects.import({ name: "Imported", residency: "synced", source_path: "C:/src" });
    await client.projects.get(id);
    await client.projects.list({ limit: 20 });
    await client.conversations.create({ project_id: id, workspace_type: "project_chat" });
    await client.conversations.get(id);
    await client.conversations.list({ project_id: id });
    await client.tasks.create({
      conversation_id: id,
      user_request: "Build it",
      operation_mode: "create_new_version",
      execution_target: "local",
      idempotency_key: "task-1",
    });
    await client.tasks.get(id);
    await client.tasks.list({ conversation_id: id });
    await client.tasks.review(id);
    await client.changesets.propose({
      task_id: id,
      files: [{ path: "README.md", content: "draft" }],
      reason: "Update copy",
      idempotency_key: "changeset-1",
    });
    await client.approvals.decide({ approval_id: id, approved: true, decided_by: "user" });
    await client.approvals.list({ task_id: id });
    await client.versions.get(id);
    await client.versions.list({ project_id: id });
    await client.versions.accept({
      task_id: id,
      expected_project_revision: 0,
      user_confirmed: true,
    });
    await client.versions.discard(id);
    await client.runtimes.get(id);
    await client.runtimes.health(id);
    await client.systemActions.execute({
      task_id: id,
      action: { type: "open_url", url: "https://example.com" },
      idempotency_key: "system-action-1",
      user_confirmed: true,
    });
    await client.previews.start({ task_id: id, idempotency_key: "preview-start-1" });
    await client.previews.get(id);
    await client.previews.resolve({ conversation_id: id });
    await client.previews.stop({ preview_id: id, idempotency_key: "preview-stop-1" });
    await client.artifacts.list(id);
    await client.artifacts.read(id);
    await client.documents.import({
      task_id: id,
      filename: "evidence.txt",
      media_type: "text/plain",
      content_base64: "RmFpcnk=",
      visibility: "conversation",
      idempotency_key: "document-import-1",
      user_confirmed: true,
    });
    await client.documents.list({ task_id: id, limit: 25 });
    await client.documents.get(id, id);
    await client.documents.search({ task_id: id, query: "evidence", limit: 10 });
    await client.documents.delete({
      task_id: id,
      document_id: id,
      idempotency_key: "document-delete-1",
      user_confirmed: true,
    });
    await client.capabilities.get();
    await client.permissions.get();
    await client.permissions.update({
      profile: "standard",
      capability_overrides: { "web.search": false },
      expected_revision: 0,
      idempotency_key: "permissions:client:standard",
    });
    await client.providers.list();
    await client.providers.health("openrouter-free");
    await client.memory.observations.create({
      task_id: id,
      content: "Use compact navigation.",
      idempotency_key: "memory-observe-1",
    });
    await client.memory.observations.list(id, "conversation_draft");
    await client.memory.claims.promote({
      task_id: id,
      observation_id: id,
      subject: "project",
      predicate: "framework",
      value: "React",
      normalized_text: "react",
      user_confirmed: true,
      idempotency_key: "memory-promote-1",
    });
    await client.memory.claims.get(id, id);
    await client.memory.claims.list(id, "project_canonical");
    await client.memory.claims.supersede({
      task_id: id,
      claim_id: id,
      expected_revision: 1,
      source_observation_ids: [id],
      value: "React 19",
      normalized_text: "react 19",
      user_confirmed: true,
      idempotency_key: "memory-supersede-1",
    });
    await client.memory.claims.resolveConflict({
      task_id: id,
      claim_id: id,
      expected_revision: 2,
      source_observation_ids: [id],
      resolved_claim_ids: [id],
      value: "React 19.2",
      normalized_text: "react 19.2",
      user_confirmed: true,
      idempotency_key: "memory-resolve-1",
    });
    await client.memory.forget({
      task_id: id,
      target_kind: "claim",
      target_id: id,
      reason: "No longer relevant",
      user_confirmed: true,
      idempotency_key: "memory-forget-1",
    });
    await client.memory.search({ task_id: id, query: "memory", limit: 10 });
    await client.memory.snapshots.get(id, id);
    await client.memory.projection.health(id);
    await client.assistant.turns.create({
      task_id: id,
      profile_id: "local-default",
      idempotency_key: "turn-1",
    });
    await client.assistant.turns.get(id);
    await client.assistant.turns.cancel({
      turn_id: id,
      expected_cancellation_revision: 0,
    });
    await client.assistant.turns.run(id);
    await client.assistant.turns.retry({
      turn_id: id,
      idempotency_key: "turn-1-retry",
    });
    await client.messages.list({ conversation_id: id, limit: 20 });

    expect(transport.requests.map(({ method }) => method)).toEqual([
      "health",
      "projects.create",
      "projects.import",
      "projects.get",
      "projects.list",
      "conversations.create",
      "conversations.get",
      "conversations.list",
      "tasks.create",
      "tasks.get",
      "tasks.list",
      "tasks.review",
      "changesets.propose",
      "approvals.decide",
      "approvals.list",
      "versions.get",
      "versions.list",
      "versions.accept",
      "versions.discard",
      "runtimes.get",
      "runtimes.health",
      "system.actions.execute",
      "previews.start",
      "previews.get",
      "previews.resolve",
      "previews.stop",
      "artifacts.list",
      "artifacts.read",
      "documents.import",
      "documents.list",
      "documents.get",
      "documents.search",
      "documents.delete",
      "capabilities.get",
      "permissions.get",
      "permissions.update",
      "providers.list",
      "providers.health",
      "memory.observations.create",
      "memory.observations.list",
      "memory.claims.promote",
      "memory.claims.get",
      "memory.claims.list",
      "memory.claims.supersede",
      "memory.claims.resolve_conflict",
      "memory.forget",
      "memory.search",
      "memory.snapshots.get",
      "memory.projection.health",
      "assistant.turns.create",
      "assistant.turns.get",
      "assistant.turns.cancel",
      "assistant.turns.run",
      "assistant.turns.retry",
      "messages.list",
    ]);
    expect(transport.requests[3]?.params).toEqual({ project_id: id });
    expect(transport.requests[11]?.params).toEqual({ task_id: id });
    expect(transport.requests[20]?.params).toEqual({ task_id: id });
  });

  it("uses the transport-native resumable event subscription", async () => {
    const transport = new RecordingTransport();
    const client = new CoreClient(transport);
    const controller = new AbortController();

    const events: EventEnvelope[] = [];
    for await (const event of client.events.subscribe(7, { signal: controller.signal })) {
      events.push(event);
    }

    expect(events.map(({ cursor }) => cursor)).toEqual([8]);
    expect(transport.subscriptions).toEqual([{ cursor: 7, signal: controller.signal }]);
  });
});
