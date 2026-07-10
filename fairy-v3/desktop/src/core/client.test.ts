import { describe, expect, it } from "vitest";

import { CoreClient, type CoreTransport } from "./client";

class RecordingTransport implements CoreTransport {
  requests: Array<{ method: string; params: unknown }> = [];

  async call<T>(method: string, params: unknown): Promise<T> {
    this.requests.push({ method, params });
    if (method === "health") {
      return { status: "ok" } as T;
    }
    return { id: "project-1", name: "Example" } as T;
  }
}

describe("CoreClient", () => {
  it("uses one transport contract for local and cloud calls", async () => {
    const transport = new RecordingTransport();
    const client = new CoreClient(transport);

    await client.health();
    await client.projects.create({ name: "Example", residency: "local_only" });

    expect(transport.requests).toEqual([
      { method: "health", params: {} },
      {
        method: "projects.create",
        params: { name: "Example", residency: "local_only" },
      },
    ]);
  });
});
