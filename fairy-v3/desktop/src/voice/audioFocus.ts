import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface AudioFocusSnapshot {
  sequence: number;
  realtime_active: boolean;
}

// Native authority is shared by all WebViews. No BroadcastChannel message grants priority.
export function subscribeAudioFocus(callback: (value: AudioFocusSnapshot) => void): () => void {
  if (!isTauri()) return () => undefined;
  let disposed = false;
  let sequence = -1;
  let unlisten: (() => void) | undefined;
  const accept = (value: AudioFocusSnapshot) => {
    if (disposed || !Number.isSafeInteger(value?.sequence)
      || value.sequence < sequence || typeof value.realtime_active !== "boolean") return;
    sequence = value.sequence;
    callback(value);
  };
  void listen<AudioFocusSnapshot>("fairy-audio-focus", (event) => accept(event.payload))
    .then(async (close) => {
      if (disposed) { close(); return; }
      unlisten = close;
      accept(await invoke<AudioFocusSnapshot>("audio_focus_status"));
    }).catch(() => {
      // A missing host snapshot must not make a reloaded WebView assume it owns audio.
      if (!disposed) callback({ sequence: Math.max(sequence, 0), realtime_active: true });
    });
  return () => { disposed = true; unlisten?.(); };
}
