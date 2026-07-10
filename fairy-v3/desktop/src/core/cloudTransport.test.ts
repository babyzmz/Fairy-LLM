import { describe, expect, it } from "vitest";

import { CoreClient } from "./client";
import { CloudCoreTransport } from "./cloudTransport";

const EVENT = {
  id: "0198f4de-0114-7000-8000-000000000001",
  cursor: 5,
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

const CLAIM_CONTEXT = {
  claim: {
    id: "0198f4de-0114-7000-8000-000000000004",
    namespace: "conversation_draft",
    project_id: null,
    conversation_id: "0198f4de-0114-7000-8000-000000000002",
    task_id: "0198f4de-0114-7000-8000-000000000003",
    version_id: null,
    device_id: null,
    subject: "project",
    predicate: "framework",
    current_revision: 1,
    conflict_set_id: null,
    status: "active",
    created_at: "2026-07-10T00:00:00Z",
    updated_at: "2026-07-10T00:00:00Z",
  },
  current_revision: {
    claim_id: "0198f4de-0114-7000-8000-000000000004",
    revision: 1,
    value: "React 19",
    normalized_text: "react 19",
    source_observation_ids: ["0198f4de-0114-7000-8000-000000000005"],
    source_event_ids: ["0198f4de-0114-7000-8000-000000000001"],
    authority: "explicit_user",
    confidence: 1,
    valid_from: null,
    valid_to: null,
    recorded_at: "2026-07-10T00:00:00Z",
    actor: "user:core-client",
    supersedes_revision: null,
    resolved_claim_ids: [],
  },
};

const SNAPSHOT = {
  id: "0198f4de-0114-7000-8000-000000000006",
  project_id: null,
  conversation_id: "0198f4de-0114-7000-8000-000000000002",
  task_id: "0198f4de-0114-7000-8000-000000000003",
  base_version_id: null,
  target_version_id: null,
  snapshot_version: 1,
  policy_version: "hermes-lexical-v1",
  source_watermark_cursor: 5,
  projection_generation: 1,
  projection_watermark_cursor: 4,
  projection_state: "stale",
  status: "degraded",
  degraded_reason: "MEMORY_PROJECTION_STALE",
  content_hash: "a".repeat(64),
  token_count: 0,
  items: [],
  created_at: "2026-07-10T00:00:00Z",
};

const PROJECTION_HEALTH = {
  generation: 1,
  state: "stale",
  source_watermark_cursor: 5,
  projected_watermark_cursor: 4,
  lag: 1,
  last_error_code: "MEMORY_PROJECTION_STALE",
  updated_at: "2026-07-10T00:00:00Z",
};

describe("CloudCoreTransport", () => {
  it("maps typed Core calls to authenticated REST requests", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      if (request.url.includes("/v1/memory/observations?")) {
        return Response.json({ items: [] });
      }
      if (request.url.includes("/v1/memory/search?")) {
        return Response.json({ items: [] });
      }
      if (request.url.includes("/v1/memory/snapshots/")) {
        return Response.json(SNAPSHOT);
      }
      if (request.url.includes("/v1/memory/projection/health?")) {
        return Response.json(PROJECTION_HEALTH);
      }
      if (request.url.includes("/v1/memory/claims/")) {
        return Response.json(CLAIM_CONTEXT);
      }
      return Response.json({ id: "result" });
    };
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test/",
      accessToken: () => "access-token",
      deviceId: "device-1",
      fetch: fetcher,
    });

    await transport.call("projects.get", { project_id: "project/a" });
    await transport.call("approvals.decide", {
      approval_id: "approval-1",
      approved: true,
      decided_by: "user",
    });
    await transport.call("capabilities.get", {
      profile: "standard",
      sandbox_healthy: true,
      overrides: { "network.http": false },
    });
    await transport.call("memory.observations.list", {
      task_id: "task/1",
      namespace: "conversation_draft",
    });
    await transport.call("memory.claims.get", {
      task_id: "task/1",
      claim_id: "claim/1",
    });
    await transport.call("memory.claims.supersede", {
      task_id: "task-1",
      claim_id: "claim/1",
      expected_revision: 1,
      source_observation_ids: ["observation-1"],
      value: "React 19",
      normalized_text: "react 19",
      user_confirmed: true,
      idempotency_key: "memory-supersede-1",
    });
    await transport.call("memory.search", {
      task_id: "task/1",
      query: "compact navigation",
      limit: 10,
    });
    await transport.call("memory.snapshots.get", {
      task_id: "task/1",
      snapshot_id: "snapshot/1",
    });
    await transport.call("memory.projection.health", { task_id: "task/1" });

    expect(requests.map(({ method, url }) => [method, url])).toEqual([
      ["GET", "https://cloud.fairy.test/v1/projects/project%2Fa"],
      ["POST", "https://cloud.fairy.test/v1/approvals/approval-1/decision"],
      ["POST", "https://cloud.fairy.test/v1/capabilities"],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/observations?task_id=task%2F1&namespace=conversation_draft",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/claims/claim%2F1?task_id=task%2F1",
      ],
      [
        "POST",
        "https://cloud.fairy.test/v1/memory/claims/claim%2F1/supersede",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/search?task_id=task%2F1&query=compact%20navigation&limit=10",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/snapshots/snapshot%2F1?task_id=task%2F1",
      ],
      [
        "GET",
        "https://cloud.fairy.test/v1/memory/projection/health?task_id=task%2F1",
      ],
    ]);
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer access-token");
    expect(requests[0]?.headers.get("X-Fairy-Device-ID")).toBe("device-1");
    await expect(requests[2]?.json()).resolves.toEqual({
      profile: "standard",
      sandbox_healthy: true,
      overrides: { "network.http": false },
    });
  });

  it("parses finite and live SSE with cursor deduplication", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      requests.push(new Request(input, init));
      return new Response(`id: 5\nevent: task.created\ndata: ${JSON.stringify(EVENT)}\n\n`, {
        headers: { "Content-Type": "text/event-stream" },
      });
    };
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: fetcher,
    });

    const batch = await transport.call("events.subscribe", { cursor: 4 });
    const controller = new AbortController();
    const iterator = transport
      .subscribeEvents(4, { signal: controller.signal })
      [Symbol.asyncIterator]();
    const live = await iterator.next();
    controller.abort();
    await iterator.return?.();

    expect(batch).toEqual({ items: [EVENT], next_cursor: 5 });
    expect(live.value).toEqual(EVENT);
    expect(requests[0]?.url).toBe(
      "https://cloud.fairy.test/v1/events?cursor=4&follow=false",
    );
    expect(requests[1]?.headers.get("Last-Event-ID")).toBe("4");
  });

  it("preserves stable cloud error codes", async () => {
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () =>
        Response.json(
          { detail: { code: "VERSION_CONFLICT", message: "stale revision" } },
          { status: 409 },
        ),
    });
    const client = new CoreClient(transport);

    await expect(client.projects.get("project-1")).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 409,
      errorCode: "VERSION_CONFLICT",
      message: "stale revision",
    });
  });

  it("rejects malformed memory envelopes before they reach the client", async () => {
    const transport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () => Response.json({ claim: { id: "not-a-uuid" } }),
    });

    await expect(
      transport.call("memory.claims.get", {
        task_id: "0198f4de-0114-7000-8000-000000000003",
        claim_id: "0198f4de-0114-7000-8000-000000000004",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 200,
      errorCode: "INVALID_RESPONSE",
    });

    const invalidSnapshotTransport = new CloudCoreTransport({
      baseUrl: "https://cloud.fairy.test",
      accessToken: () => "token",
      deviceId: "device-1",
      fetch: async () => Response.json({ ...SNAPSHOT, content_hash: "ABC" }),
    });
    await expect(
      invalidSnapshotTransport.call("memory.snapshots.get", {
        task_id: "0198f4de-0114-7000-8000-000000000003",
        snapshot_id: "0198f4de-0114-7000-8000-000000000006",
      }),
    ).rejects.toMatchObject({
      name: "CloudCoreError",
      status: 200,
      errorCode: "INVALID_RESPONSE",
    });
  });
});
