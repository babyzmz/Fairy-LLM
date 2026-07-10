import type { CoreTransport } from "./client";

export type InvokeFunction = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

interface JsonRpcSuccess<T> {
  jsonrpc: "2.0";
  id: number;
  result: T;
}

interface JsonRpcFailure {
  jsonrpc: "2.0";
  id: number;
  error: {
    code: number;
    message: string;
    data?: {
      error_code?: string;
      [key: string]: unknown;
    };
  };
}

type JsonRpcResponse<T> = JsonRpcSuccess<T> | JsonRpcFailure;

export class CoreRpcError extends Error {
  readonly rpcCode: number;
  readonly errorCode: string;
  readonly data: Record<string, unknown>;

  constructor(failure: JsonRpcFailure["error"]) {
    super(failure.message);
    this.name = "CoreRpcError";
    this.rpcCode = failure.code;
    this.errorCode = failure.data?.error_code ?? "CORE_RPC_ERROR";
    this.data = failure.data ?? {};
  }
}

export class TauriCoreTransport implements CoreTransport {
  private requestId = 0;

  constructor(private readonly invoke: InvokeFunction) {}

  async call<T>(method: string, params: unknown): Promise<T> {
    const response = await this.invoke<JsonRpcResponse<T>>("core_rpc", {
      request: {
        jsonrpc: "2.0",
        id: ++this.requestId,
        method,
        params,
      },
    });
    if ("error" in response) {
      throw new CoreRpcError(response.error);
    }
    return response.result;
  }
}
