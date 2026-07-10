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

    const request = transport.call("tasks.create", {});

    await expect(request).rejects.toBeInstanceOf(CoreRpcError);
    await expect(request).rejects.toMatchObject({
      name: "CoreRpcError",
      errorCode: "SANDBOX_UNAVAILABLE",
    });
  });
});
