import type {
  CoreCallOptions,
  CoreMethodMap,
  CoreMethodName,
  CoreTransport,
} from "./client";
import type {
  OpenRouterConfigurationInput,
  OpenRouterConfigurationStatus,
} from "./client";
import { parseMemoryResult } from "./memoryValidation";
import { parseRuntimeResult } from "./runtimeValidation";

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
  readonly eventSourceId = "local:stdio";

  private requestId = 0;

  constructor(private readonly invoke: InvokeFunction) {}

  providerOpenRouterStatus(): Promise<OpenRouterConfigurationStatus> {
    return this.invoke("provider_openrouter_status");
  }

  providerOpenRouterConfigure(
    input: OpenRouterConfigurationInput,
  ): Promise<OpenRouterConfigurationStatus> {
    return this.invoke("provider_openrouter_configure", { input });
  }

  providerOpenRouterDelete(): Promise<OpenRouterConfigurationStatus> {
    return this.invoke("provider_openrouter_delete");
  }

  selectProjectFolder(): Promise<string | null> {
    return this.invoke("select_project_folder");
  }

  openSettingsWindow(): Promise<void> {
    return this.invoke("open_settings_window");
  }

  async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    options: CoreCallOptions = {},
  ): Promise<CoreMethodMap[M]["result"]> {
    options.signal?.throwIfAborted();
    const response = await this.invoke<JsonRpcResponse<CoreMethodMap[M]["result"]>>(
      "core_rpc",
      {
        request: {
          jsonrpc: "2.0",
          id: ++this.requestId,
          method,
          params,
        },
      },
    );
    if ("error" in response) {
      throw new CoreRpcError(response.error);
    }
    return parseRuntimeResult(
      method,
      parseMemoryResult(method, response.result),
    ) as CoreMethodMap[M]["result"];
  }
}
