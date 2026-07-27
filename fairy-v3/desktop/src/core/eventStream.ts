import type {
  CoreCallOptions,
  EventBatch,
  EventEnvelope,
  EventStreamState,
  EventSubscriptionOptions,
} from "./client";

export interface EventCheckpoint {
  source_id: string;
  ledger_id: string;
  cursor: number;
}

export interface EventDeliveryClient {
  readonly sourceId: string;
  state(options?: CoreCallOptions): Promise<EventStreamState>;
  list(cursor: number, limit: number, options?: CoreCallOptions): Promise<EventBatch>;
  subscribe(cursor: number, options?: EventSubscriptionOptions): AsyncIterable<EventEnvelope>;
}

export interface EventDeliveryOptions {
  checkpoint: EventCheckpoint | null;
  signal?: AbortSignal;
  pageSize?: number;
  onEvent(event: EventEnvelope): void | Promise<void>;
  onCheckpoint(checkpoint: EventCheckpoint): void | Promise<void>;
  onReady?(): void | Promise<void>;
}

export function reconcileEventCheckpoint(
  sourceId: string,
  state: EventStreamState,
  checkpoint: EventCheckpoint | null,
): number {
  const replayCursor = state.oldest_cursor > 0 ? state.oldest_cursor - 1 : 0;
  if (
    checkpoint === null ||
    checkpoint.source_id !== sourceId ||
    checkpoint.ledger_id !== state.ledger_id ||
    checkpoint.cursor < replayCursor ||
    checkpoint.cursor > state.latest_cursor
  ) {
    return replayCursor;
  }
  return checkpoint.cursor;
}

export async function runEventDelivery(
  client: EventDeliveryClient,
  options: EventDeliveryOptions,
): Promise<void> {
  const pageSize = options.pageSize ?? 500;
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 2_000) {
    throw new RangeError("event page size must be between 1 and 2000");
  }

  options.signal?.throwIfAborted();
  const state = await client.state({ signal: options.signal });
  let cursor = reconcileEventCheckpoint(client.sourceId, state, options.checkpoint);
  await options.onCheckpoint(checkpointFor(client.sourceId, state.ledger_id, cursor));

  while (cursor < state.latest_cursor) {
    options.signal?.throwIfAborted();
    const page = await client.list(cursor, pageSize, { signal: options.signal });
    const previousCursor = cursor;

    for (const event of page.items) {
      options.signal?.throwIfAborted();
      if (event.cursor <= cursor) continue;
      if (event.cursor > state.latest_cursor) break;
      await options.onEvent(event);
      cursor = event.cursor;
      await options.onCheckpoint(checkpointFor(client.sourceId, state.ledger_id, cursor));
    }

    if (cursor === previousCursor) {
      throw new Error("Event history did not advance before the advertised watermark");
    }
  }

  await options.onReady?.();
  for await (const event of client.subscribe(cursor, { signal: options.signal })) {
    options.signal?.throwIfAborted();
    if (event.cursor <= cursor) continue;
    await options.onEvent(event);
    cursor = event.cursor;
    await options.onCheckpoint(checkpointFor(client.sourceId, state.ledger_id, cursor));
  }
}

function checkpointFor(sourceId: string, ledgerId: string, cursor: number): EventCheckpoint {
  return { source_id: sourceId, ledger_id: ledgerId, cursor };
}

export interface ResilientDeliveryOptions extends EventDeliveryOptions {
  onError?(error: unknown, attempt: number): void | Promise<void>;
  onRecovered?(): void | Promise<void>;
  reconnectDelayMs?(attempt: number): number;
}

const MAX_RECONNECT_DELAY_MS = 10_000;

export function defaultReconnectDelay(attempt: number): number {
  const exponential = 500 * 2 ** Math.max(0, attempt - 1);
  return Math.min(exponential, MAX_RECONNECT_DELAY_MS);
}

/**
 * Runs {@link runEventDelivery} and automatically reconnects after a stream
 * error or a clean completion, resuming from the most recent checkpoint. It
 * only returns when the abort signal fires, so a single transient transport
 * failure can never permanently stop live event delivery.
 */
export async function runResilientEventDelivery(
  client: EventDeliveryClient,
  options: ResilientDeliveryOptions,
): Promise<void> {
  const reconnectDelay = options.reconnectDelayMs ?? defaultReconnectDelay;
  let checkpoint = options.checkpoint;
  let attempt = 0;
  let recovering = false;

  const onCheckpoint = async (next: EventCheckpoint) => {
    checkpoint = next;
    await options.onCheckpoint(next);
  };
  const onEvent = async (event: EventEnvelopeLike) => {
    attempt = 0;
    await options.onEvent(event);
  };
  const onReady = async () => {
    await options.onReady?.();
    if (!recovering) return;
    recovering = false;
    attempt = 0;
    await options.onRecovered?.();
  };

  while (!options.signal?.aborted) {
    try {
      await runEventDelivery(client, {
        ...options,
        checkpoint,
        onEvent,
        onCheckpoint,
        onReady,
      });
    } catch (error) {
      if (options.signal?.aborted) return;
      recovering = true;
      await options.onError?.(error, attempt + 1);
    }
    if (options.signal?.aborted) return;
    attempt += 1;
    await abortableDelay(reconnectDelay(attempt), options.signal);
  }
}

type EventEnvelopeLike = Parameters<EventDeliveryOptions["onEvent"]>[0];

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted || milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = setTimeout(finish, milliseconds);
    signal?.addEventListener("abort", finish, { once: true });

    function finish() {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
  });
}
