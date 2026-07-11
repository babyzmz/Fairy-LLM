import { describe, expect, it, vi } from "vitest";

import { CoreRpcError, TauriCoreTransport } from "./tauriTransport";

describe("TauriCoreTransport", () => {
  it("routes every local call through the single core_rpc command", async () => {
    const invoke = vi.fn().mockResolvedValue({
      jsonrpc: "2.0",
      id: 1,
      result: { status: "ok" },
    });
    const transport = new TauriCoreTransport(invoke);

    await expect(transport.call("health", {})).resolves.toEqual({ status: "ok" });
    expect(invoke).toHaveBeenCalledWith("core_rpc", {
      request: {
        jsonrpc: "2.0",
        id: 1,
        method: "health",
        params: {},
      },
    });
  });

  it("surfaces typed errors returned by Core", async () => {
    const invoke = vi.fn().mockResolvedValue({
      jsonrpc: "2.0",
      id: 1,
      error: {
        code: -32000,
        message: "Sandbox is not available",
        data: { error_code: "SANDBOX_UNAVAILABLE" },
      },
    });
    const transport = new TauriCoreTransport(invoke);

    const request = transport.call("tasks.create", {
      conversation_id: "0198f4de-0114-7000-8000-000000000001",
      execution_target: "local",
      idempotency_key: "task-1",
      operation_mode: "answer",
      user_request: "Answer",
    });

    await expect(request).rejects.toBeInstanceOf(CoreRpcError);
    await expect(request).rejects.toMatchObject({
      name: "CoreRpcError",
      errorCode: "SANDBOX_UNAVAILABLE",
    });
  });

  it("rejects malformed local Preview envelopes", async () => {
    const id = "0198f4de-0114-7000-8000-000000000001";
    const invoke = vi.fn().mockResolvedValue({
      jsonrpc: "2.0",
      id: 1,
      result: {
        task: { id, conversation_id: id, status: "previewing" },
        runtime: { id, status: "running" },
        preview: { id, status: "ready", url: "http://localhost:43125/" },
      },
    });
    const transport = new TauriCoreTransport(invoke);

    await expect(transport.call("previews.get", { preview_id: id })).rejects.toMatchObject({
      name: "ZodError",
    });
  });
});
