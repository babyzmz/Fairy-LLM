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
