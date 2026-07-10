import { describe, expect, it } from "vitest";

import {
  CoreClient,
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
  it("exposes one typed method surface for local and cloud transports", async () => {
    const transport = new RecordingTransport();
    const client = new CoreClient(transport);
    const id = "0198f4de-0114-7000-8000-000000000001";

    await client.health();
    await client.projects.create({ name: "Example", residency: "local_only" });
    await client.projects.import({ name: "Imported", residency: "synced", source_path: "C:/src" });
    await client.projects.get(id);
    await client.conversations.create({ project_id: id, workspace_type: "project_chat" });
    await client.tasks.create({
      conversation_id: id,
      user_request: "Build it",
      operation_mode: "create_new_version",
      execution_target: "local",
      idempotency_key: "task-1",
    });
    await client.tasks.get(id);
    await client.tasks.review(id);
    await client.changesets.propose({
      task_id: id,
      files: [{ path: "README.md", content: "draft" }],
      reason: "Update copy",
      idempotency_key: "changeset-1",
    });
    await client.approvals.decide({ approval_id: id, approved: true, decided_by: "user" });
    await client.versions.get(id);
    await client.versions.accept({
      task_id: id,
      expected_project_revision: 0,
      user_confirmed: true,
    });
    await client.versions.discard(id);
    await client.capabilities.get({
      profile: "standard",
      sandbox_healthy: true,
      overrides: { "network.http": false },
    });

    expect(transport.requests.map(({ method }) => method)).toEqual([
      "health",
      "projects.create",
      "projects.import",
      "projects.get",
      "conversations.create",
      "tasks.create",
      "tasks.get",
      "tasks.review",
      "changesets.propose",
      "approvals.decide",
      "versions.get",
      "versions.accept",
      "versions.discard",
      "capabilities.get",
    ]);
    expect(transport.requests[3]?.params).toEqual({ project_id: id });
    expect(transport.requests[7]?.params).toEqual({ task_id: id });
    expect(transport.requests[12]?.params).toEqual({ task_id: id });
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
