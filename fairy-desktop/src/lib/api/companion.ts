import { API_BASE_URL } from "../config/env";
import { apiRequest } from "./client";

export interface QuipPayload {
  text: string;
  category: string;
  source: string;
  emitted_at: number;
}

export interface BubbleSnapshot {
  quip: string;
  category: string;
  source: string;
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
  muted: boolean;
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

export function subscribeQuipStream(onQuip: QuipStreamCallback): () => void {
  const url = `${API_BASE_URL}/companion/quip-stream`;
  const source = new EventSource(url);
  source.addEventListener("quip", (event) => {
    try {
      const payload = JSON.parse((event as MessageEvent).data) as QuipPayload;
      onQuip(payload);
    } catch {
      /* ignore malformed event */
    }
  });
  return () => source.close();
}
