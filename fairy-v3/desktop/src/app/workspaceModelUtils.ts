import type { EventEnvelope } from "../core/client";
import { CoreRpcError } from "../core/tauriTransport";

export const workspaceKey = ["workspace"] as const;
export const permissionQueryKey = [...workspaceKey, "permissions"] as const;
export const capabilityQueryKey = [...workspaceKey, "capabilities"] as const;
export const terminalAssistantEvents = new Set([
  "assistant.turn.completed",
  "assistant.turn.cancelled",
  "assistant.turn.failed",
]);

export function shouldRetryCoreStartup(failureCount: number, error: Error): boolean {
  return (
    failureCount < 20 &&
    error instanceof CoreRpcError &&
    error.errorCode === "WORKER_INTERRUPTED"
  );
}

export function coreStartupRetryDelay(attemptIndex: number): number {
  return Math.min(250 * (attemptIndex + 1), 1_000);
}

export function selectedItem<T extends { id: string }>(
  items: T[],
  selectedId: string | null,
): T | null {
  return items.find((item) => item.id === selectedId) ?? items.at(0) ?? null;
}

export function requireId(value: string | null | undefined): string {
  if (value === undefined || value === null) {
    throw new Error("Workspace scope is unavailable");
  }
  return value;
}

export function appendEvent(
  events: EventEnvelope[],
  incoming: EventEnvelope,
): EventEnvelope[] {
  if (events.some((event) => event.id === incoming.id || event.cursor === incoming.cursor)) {
    return events;
  }
  return [...events, incoming].sort((left, right) => left.cursor - right.cursor).slice(-500);
}

export function firstError(...errors: (Error | null)[]): Error | null {
  return errors.find((error) => error !== null) ?? null;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Workspace request failed";
}

export function coreErrorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("errorCode" in error)) {
    return null;
  }
  return typeof error.errorCode === "string" ? error.errorCode : null;
}
