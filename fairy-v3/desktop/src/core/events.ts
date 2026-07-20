export type EventVisibility = "user" | "developer" | "internal";

export interface CoreEvent {
  id: string;
  cursor: number;
  run_id: string | null;
  project_id: string | null;
  conversation_id: string | null;
  task_id: string | null;
  version_id: string | null;
  task_sequence: number | null;
  event_type: string;
  visibility: EventVisibility;
  message: string;
  payload: Record<string, unknown>;
  schema_version: number;
  created_at: string;
}

export interface EventState {
  lastCursor: number;
  timeline: CoreEvent[];
  taskStatus: Record<string, string>;
}

export function createEventState(): EventState {
  return { lastCursor: 0, timeline: [], taskStatus: {} };
}

export function applyEvent(state: EventState, event: CoreEvent): EventState {
  if (event.cursor <= state.lastCursor) {
    return state;
  }
  const status = typeof event.payload.status === "string" ? event.payload.status : undefined;
  return {
    lastCursor: event.cursor,
    timeline: event.visibility === "internal" ? state.timeline : [...state.timeline, event],
    taskStatus: status && event.task_id !== null
      ? { ...state.taskStatus, [event.task_id]: status }
      : state.taskStatus,
  };
}
