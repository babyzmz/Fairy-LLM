import { API_BASE_URL } from "../config/env";
import { apiRequest } from "./client";

export interface QuipPayload {
  text: string;
  category: string;
  scene: string;
  repetition: boolean;
  source: string;
  emitted_at: number;
}

export interface SceneTransitionPayload {
  previous: string;
  current: string;
  reason: string;
  at: number;
}

export interface BubbleSnapshot {
  quip: string;
  category: string;
  source: string;
  scene: string;
  repetition: boolean;
  quip_started_at: number;
  fade_at: number;
  expires_at: number;
  pet_started_at: number | null;
  pet_expires_at: number | null;
  muted: boolean;
  now: number;
}

export interface WatcherSnapshot {
  active_game: string | null;
  last_window_title: string;
  last_process_name: string;
  last_polled_at: number;
}

export interface CompanionStateResponse {
  bubble: BubbleSnapshot | null;
  watcher: WatcherSnapshot;
  scene: string;
  current_game: string | null;
  muted: boolean;
  persistent: {
    games_played: Record<string, number>;
    consecutive_victories: number;
    consecutive_defeats: number;
  };
}

export function getCompanionState(): Promise<CompanionStateResponse> {
  return apiRequest<CompanionStateResponse>("/companion/state");
}

export function petCompanion(): Promise<{ pet_started_at: number; now: number }> {
  return apiRequest("/companion/pet", { method: "POST" });
}

export function muteCompanion(muted: boolean): Promise<{ muted: boolean }> {
  return apiRequest("/companion/mute", {
    method: "POST",
    body: JSON.stringify({ muted }),
  });
}

export function startWatcher(): Promise<{ running: boolean }> {
  return apiRequest("/companion/watcher/start", { method: "POST" });
}

export function stopWatcher(): Promise<{ running: boolean }> {
  return apiRequest("/companion/watcher/stop", { method: "POST" });
}

export type QuipStreamCallback = (event: QuipPayload) => void;
export type SceneStreamCallback = (event: SceneTransitionPayload) => void;

export interface QuipStreamHandlers {
  onQuip?: QuipStreamCallback;
  onScene?: SceneStreamCallback;
}

export function subscribeQuipStream(onQuipOrHandlers: QuipStreamCallback | QuipStreamHandlers): () => void {
  const handlers: QuipStreamHandlers =
    typeof onQuipOrHandlers === "function" ? { onQuip: onQuipOrHandlers } : onQuipOrHandlers;
  const url = `${API_BASE_URL}/companion/quip-stream`;
  const source = new EventSource(url);
  if (handlers.onQuip) {
    source.addEventListener("quip", (event) => {
      try {
        handlers.onQuip!(JSON.parse((event as MessageEvent).data) as QuipPayload);
      } catch {
        /* ignore malformed event */
      }
    });
  }
  if (handlers.onScene) {
    source.addEventListener("scene", (event) => {
      try {
        handlers.onScene!(JSON.parse((event as MessageEvent).data) as SceneTransitionPayload);
      } catch {
        /* ignore malformed event */
      }
    });
  }
  return () => source.close();
}
