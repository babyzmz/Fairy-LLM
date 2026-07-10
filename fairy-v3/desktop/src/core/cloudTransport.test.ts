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

describe("CloudCoreTransport", () => {
  it("maps typed Core calls to authenticated REST requests", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
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

    expect(requests.map(({ method, url }) => [method, url])).toEqual([
      ["GET", "https://cloud.fairy.test/v1/projects/project%2Fa"],
      ["POST", "https://cloud.fairy.test/v1/approvals/approval-1/decision"],
      ["POST", "https://cloud.fairy.test/v1/capabilities"],
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
});
