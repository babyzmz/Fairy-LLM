import { describe, expect, it, vi } from "vitest";

import { CoreRpcError, TauriCoreTransport } from "./tauriTransport";

describe("TauriCoreTransport", () => {
  it("identifies the local stdio event source", () => {
    const transport = new TauriCoreTransport(async () => undefined as never);

    expect(transport.eventSourceId).toBe("local:stdio");
  });

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

  it("waits through the native Core startup race before returning health", async () => {
    const invoke = vi
      .fn()
      .mockResolvedValueOnce({
        jsonrpc: "2.0",
        id: 1,
        error: {
          code: -32050,
          message: "Fairy Core process was interrupted",
          data: { error_code: "WORKER_INTERRUPTED" },
        },
      })
      .mockResolvedValue({
        jsonrpc: "2.0",
        id: 1,
        result: { status: "ok" },
      });
    const transport = new TauriCoreTransport(invoke, {
      retryDelayMs: 0,
      timeoutMs: 100,
    });

    await expect(transport.call("health", {})).resolves.toEqual({ status: "ok" });
    expect(invoke).toHaveBeenCalledTimes(2);
    expect(invoke.mock.calls[1]).toEqual(invoke.mock.calls[0]);
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

  it("keeps OpenRouter credentials on host-only commands", async () => {
    const invoke = vi.fn().mockResolvedValue({
      configured: true,
      account_id: "openrouter-default",
    });
    const transport = new TauriCoreTransport(invoke);

    await expect(
      transport.providerOpenRouterConfigure({
        api_key: "private-test-key",
      }),
    ).resolves.toEqual({ configured: true, account_id: "openrouter-default" });

    expect(invoke).toHaveBeenCalledWith("provider_openrouter_configure", {
      input: {
        api_key: "private-test-key",
      },
    });
    expect(invoke).not.toHaveBeenCalledWith("core_rpc", expect.anything());
  });

  it("selects project folders through the restricted host command", async () => {
    const invoke = vi.fn().mockResolvedValue("C:\\Projects\\selected");
    const transport = new TauriCoreTransport(invoke);

    await expect(transport.selectProjectFolder()).resolves.toBe("C:\\Projects\\selected");
    expect(invoke).toHaveBeenCalledWith("select_project_folder");
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
