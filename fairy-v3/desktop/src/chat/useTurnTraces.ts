import { useQueries } from "@tanstack/react-query";

import type {
  AssistantTurn,
  CoreClient,
  Message,
  TurnTrace,
} from "../core/client";

interface TurnTraceClient {
  assistant: {
    turns: Pick<CoreClient["assistant"]["turns"], "trace">;
  };
}

interface UseTurnTracesOptions {
  enabled: boolean;
  chatMessages: Message[];
  projectMessages: Message[];
  chatTurn: AssistantTurn | null;
  projectTurn: AssistantTurn | null;
  selectedTaskId: string | null;
}

interface TurnTraceQueries {
  turnTraces: Record<string, TurnTrace>;
  projectTrace: TurnTrace | null;
}

export function useTurnTraces(
  client: TurnTraceClient,
  options: UseTurnTracesOptions,
): TurnTraceQueries {
  const projectTurnId = currentProjectTurnId(options);
  const turnIds = uniqueTurnIds([
    ...options.chatMessages.map((message) => message.turn_id),
    options.chatTurn?.id ?? null,
    projectTurnId,
  ]).slice(-50);
  const queries = useQueries({
    queries: turnIds.map((turnId) => ({
      queryKey: ["workspace", "turn-trace", turnId] as const,
      queryFn: () => client.assistant.turns.trace(turnId),
      enabled: options.enabled,
      retry: false,
      staleTime: 1_000,
    })),
  });
  const turnTraces: Record<string, TurnTrace> = {};
  for (const [index, query] of queries.entries()) {
    const turnId = turnIds[index];
    if (turnId !== undefined && query.data !== undefined) turnTraces[turnId] = query.data;
  }
  return {
    turnTraces,
    projectTrace: projectTurnId === null ? null : (turnTraces[projectTurnId] ?? null),
  };
}

function currentProjectTurnId(options: UseTurnTracesOptions): string | null {
  if (options.projectTurn !== null && options.projectTurn.task_id === options.selectedTaskId) {
    return options.projectTurn.id;
  }
  if (options.selectedTaskId === null) return null;
  return options.projectMessages
    .filter(
      (message) =>
        message.task_id === options.selectedTaskId && message.turn_id !== null,
    )
    .sort((left, right) => left.sequence - right.sequence)
    .at(-1)?.turn_id ?? null;
}

function uniqueTurnIds(values: Array<string | null>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    if (value === null || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}
