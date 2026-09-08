import type {
  CoreCallOptions,
  CoreMethodMap,
  CoreMethodName,
  CoreTransport,
  EventEnvelope,
  EventSubscriptionOptions,
} from "./client";
import { pollCoreEvents } from "./client";
import type {
  OpenRouterConfigurationInput,
  OpenRouterConfigurationStatus,
  RealtimeCredentialProvider,
  RealtimeBackendResolution,
  RealtimeBackendResolutionInput,
  RealtimeProviderCredentialInput,
  RealtimeProviderCredentialStatus,
  RealtimeWorkerSetInputInput,
  RealtimeWorkerSetPolicyInput,
  RealtimeWorkerRetryMediaInput,
  RealtimeWorkerReplaceSourceInput,
  RealtimeWorkerWakeInput,
  RealtimeWorkerExtendInput,
  RealtimeWorkerSpeechStateInput,
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

  async *subscribeEvents(cursor: number, options: EventSubscriptionOptions = {}): AsyncIterable<EventEnvelope> {
    if (this.rpcCommand !== "core_rpc") {
      yield* pollCoreEvents(this, cursor, options);
      return;
    }
    if (options.signal?.aborted) return;
    const subscriptionId = crypto.randomUUID();
    let closing: Promise<unknown> | undefined;
    const close = () => closing ??= this.invoke("core_events_close", { subscriptionId }).catch(() => undefined);
    const abort = () => { void close(); };
    options.signal?.addEventListener("abort", abort, { once: true });
    let current = cursor;
    let ledgerId: string | undefined;
    try {
      const opened = await this.invoke<{ supported: boolean }>("core_events_open", { subscriptionId, cursor })
        .catch((error: unknown) => {
          if (String(error) === "Command core_events_open not found") return { supported: false };
          throw error;
        });
      if (options.signal?.aborted) {
        // An earlier close may have arrived before the host finished opening.
        await this.invoke("core_events_close", { subscriptionId }).catch(() => undefined);
        return;
      }
      if (!opened.supported) {
        yield* pollCoreEvents(this, current, options);
        return;
      }
      while (!options.signal?.aborted) {
        const result = await this.invoke<{
          kind: "batch" | "idle" | "closed" | "resync";
          batch?: { source: string; ledger_id: string; items: EventEnvelope[] };
        }>("core_events_next", { subscriptionId });
        if (options.signal?.aborted) return;
        if (result.kind === "idle") continue;
        if (result.kind === "resync") throw eventSubscriptionError("RPC_EVENT_RESYNC_REQUIRED");
        if (result.kind === "closed") throw eventSubscriptionError("RPC_EVENT_SUBSCRIPTION_CLOSED");
        const batch = result.batch;
        if (!batch || batch.source !== this.eventSourceId || !batch.ledger_id || !Array.isArray(batch.items)) {
          throw eventSubscriptionError("RPC_EVENT_INVALID_BATCH");
        }
        if (ledgerId !== undefined && ledgerId !== batch.ledger_id) throw eventSubscriptionError("RPC_EVENT_RESYNC_REQUIRED");
        ledgerId = batch.ledger_id;
        for (const event of batch.items) {
          if (options.signal?.aborted) return;
          if (!Number.isSafeInteger(event.cursor) || event.cursor < 0) throw eventSubscriptionError("RPC_EVENT_INVALID_BATCH");
          if (event.cursor <= current) continue;
          current = event.cursor;
          yield event;
        }
      }
    } catch (error) {
      if (options.signal?.aborted) return;
      throw typeof error === "string" ? eventSubscriptionError(error) : error;
    } finally {
      options.signal?.removeEventListener("abort", abort);
      await close();
    }
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

  realtimeWorkerContinue(input: RealtimeWorkerStartInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_continue", { input });
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

  realtimeWorkerSetPolicy(input: RealtimeWorkerSetPolicyInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_set_policy", { input });
  }

  realtimeWorkerRetryMedia(input: RealtimeWorkerRetryMediaInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_retry_media", { input });
  }

  realtimeWorkerReplaceSource(
    input: RealtimeWorkerReplaceSourceInput,
  ): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_replace_source", { input });
  }

  realtimeWorkerWake(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_wake", { input });
  }

  realtimeWorkerPausePrivacy(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_pause_privacy", { input });
  }

  realtimeWorkerResumePrivacy(input: RealtimeWorkerWakeInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_resume_privacy", { input });
  }

  realtimeWorkerExtend(input: RealtimeWorkerExtendInput): Promise<RealtimeWorkerStatus> {
    return this.invoke("realtime_worker_extend", { input });
  }

  realtimeWorkerSpeechState(input: RealtimeWorkerSpeechStateInput): Promise<void> {
    return this.invoke("realtime_worker_speech_state", { input });
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

function eventSubscriptionError(errorCode: string): CoreRpcError {
  return new CoreRpcError({ code: -32051, message: "Local event subscription needs recovery.", data: { error_code: errorCode } });
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
