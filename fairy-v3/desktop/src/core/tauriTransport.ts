import type {
  CoreCallOptions,
  CoreMethodMap,
  CoreMethodName,
  CoreTransport,
} from "./client";
import type {
  OpenRouterConfigurationInput,
  OpenRouterConfigurationStatus,
  RealtimeCredentialProvider,
  RealtimeBackendResolution,
  RealtimeBackendResolutionInput,
  RealtimeProviderCredentialInput,
  RealtimeProviderCredentialStatus,
  RealtimeWorkerSetInputInput,
  RealtimeWorkerStartInput,
  RealtimeWorkerStatus,
  RealtimeWorkerToolResultInput,
  ObsidianVaultSelection,
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

interface CoreStartupPolicy {
  timeoutMs?: number;
  retryDelayMs?: number;
  rpcCommand?: "core_rpc" | "companion_rpc";
}

const defaultCoreStartupTimeoutMs = 30_000;
const defaultCoreStartupRetryDelayMs = 100;

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
  private readonly startupTimeoutMs: number;
  private readonly startupRetryDelayMs: number;
  private readonly rpcCommand: "core_rpc" | "companion_rpc";

  constructor(
    private readonly invoke: InvokeFunction,
    startupPolicy: CoreStartupPolicy = {},
  ) {
    this.startupTimeoutMs = startupPolicy.timeoutMs ?? defaultCoreStartupTimeoutMs;
    this.startupRetryDelayMs = startupPolicy.retryDelayMs ?? defaultCoreStartupRetryDelayMs;
    this.rpcCommand = startupPolicy.rpcCommand ?? "core_rpc";
  }

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

  providerRealtimeStatus(
    provider: RealtimeCredentialProvider,
  ): Promise<RealtimeProviderCredentialStatus> {
    return this.invoke("provider_realtime_status", { input: { provider } });
  }

  providerRealtimeConfigure(
    input: RealtimeProviderCredentialInput,
  ): Promise<RealtimeProviderCredentialStatus> {
    return this.invoke("provider_realtime_configure", { input });
  }

  providerRealtimeDelete(
    provider: RealtimeCredentialProvider,
  ): Promise<RealtimeProviderCredentialStatus> {
    return this.invoke("provider_realtime_delete", { input: { provider } });
  }

  realtimeWorkerStatus(): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_status");
  }

  realtimeBackendResolutionPreview(
    input: RealtimeBackendResolutionInput,
  ): Promise<RealtimeBackendResolution> {
    return this.invoke("realtime_backend_resolution_preview", { input });
  }

  realtimeWorkerStart(input: RealtimeWorkerStartInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_start", { input });
  }

  realtimeWorkerStop(sessionId: string): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_stop", { input: { session_id: sessionId } });
  }

  realtimeWorkerToolResult(input: RealtimeWorkerToolResultInput): Promise<void> {
    return this.invoke("realtime_worker_tool_result", { input });
  }

  realtimeWorkerSetInput(input: RealtimeWorkerSetInputInput): Promise<void> {
    return this.invoke("realtime_worker_set_input", { input });
  }

  selectProjectFolder(): Promise<string | null> {
    return this.invoke("select_project_folder");
  }

  selectObsidianVault(): Promise<ObsidianVaultSelection | null> {
    return this.invoke("select_obsidian_vault");
  }

  openSettingsWindow(category?: import("./client").SettingsCategoryId): Promise<void> {
    return this.invoke("open_settings_window", { category });
  }

  async call<M extends CoreMethodName>(
    method: M,
    params: CoreMethodMap[M]["params"],
    options: CoreCallOptions = {},
  ): Promise<CoreMethodMap[M]["result"]> {
    options.signal?.throwIfAborted();
    const request = {
      jsonrpc: "2.0" as const,
      id: ++this.requestId,
      method,
      params,
    };
    const response = await this.invokeCore(
      request,
      method === "health",
      options.signal,
    );
    if ("error" in response) {
      throw new CoreRpcError(response.error);
    }
    return parseRuntimeResult(
      method,
      parseMemoryResult(method, response.result),
    ) as CoreMethodMap[M]["result"];
  }

  private async invokeCore<T>(
    request: Record<string, unknown>,
    waitForStartup: boolean,
    signal?: AbortSignal,
  ): Promise<JsonRpcResponse<T>> {
    const deadline = Date.now() + this.startupTimeoutMs;
    while (true) {
      signal?.throwIfAborted();
      const response = await this.invoke<JsonRpcResponse<T>>(this.rpcCommand, { request });
      if (
        !waitForStartup ||
        !("error" in response) ||
        response.error.data?.error_code !== "WORKER_INTERRUPTED" ||
        Date.now() >= deadline
      ) {
        return response;
      }
      await delayUntilCoreRetry(
        Math.min(this.startupRetryDelayMs, Math.max(0, deadline - Date.now())),
        signal,
      );
    }
  }
}

function delayUntilCoreRetry(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (delayMs <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(finish, delayMs);
    signal?.addEventListener("abort", abort, { once: true });

    function finish() {
      signal?.removeEventListener("abort", abort);
      resolve();
    }

    function abort() {
      globalThis.clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      try {
        signal?.throwIfAborted();
      } catch (error) {
        reject(error);
        return;
      }
      reject(new DOMException("Core startup wait was aborted", "AbortError"));
    }
  });
}
