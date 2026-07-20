import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Gamepad2, LoaderCircle, Mic, Monitor, Save, ShieldCheck, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CoreClient,
  RealtimeCredentialProvider,
  RealtimeProviderCredentialStatus,
  RealtimeSession,
  RealtimeSessionStatus,
  RealtimeWorkerProvider,
} from "../core/client";
import type { DesktopPreferences } from "../settings/client";
import { startRealtimeVoice } from "../voice/nativeVoice";
import "./realtime-companion.css";

interface CaptureSurface {
  kind: "display" | "window";
  source_id: string;
  label: string;
  width: number;
  height: number;
}

type WorkerEvent =
  | { type: "session_state"; session_id: string; status: string; error_code?: string | null }
  | { type: "public_caption"; session_id: string; text: string; stable: boolean; speaker: "user" | "assistant" }
  | { type: "presence"; session_id: string; state: string; level?: number | null }
  | { type: "tool_request"; session_id: string; call_id: string; tool_name: string; public_intent: string }
  | { type: "usage"; session_id: string; audio_input_ms: number; audio_output_ms: number; video_frame_count: number; interruption_count: number; tool_call_count: number }
  | { type: "worker_interrupted"; error_code: string }
  | { type: "ready" | "pong" };

interface MemoryDraft {
  gameTitle: string;
  progress: string;
  nextGoal: string;
}

interface RealtimeUsage {
  audio_input_ms: number;
  audio_output_ms: number;
  video_frame_count: number;
  interruption_count: number;
  tool_call_count: number;
}

const EMPTY_USAGE: RealtimeUsage = {
  audio_input_ms: 0,
  audio_output_ms: 0,
  video_frame_count: 0,
  interruption_count: 0,
  tool_call_count: 0,
};

export type RealtimePresenceState =
  | "idle"
  | "starting"
  | "connecting"
  | "active"
  | "listening"
  | "analyzing"
  | "speaking"
  | "stopping"
  | "completed"
  | "error";

export function RealtimeCompanion({
  client,
  onPresenceChange,
  openRequest = 0,
}: {
  client: CoreClient["realtime"];
  onPresenceChange?(state: RealtimePresenceState): void;
  openRequest?: number;
}) {
  const [open, setOpen] = useState(false);
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [credentialReady, setCredentialReady] = useState<boolean | null>(null);
  const [surfaces, setSurfaces] = useState<CaptureSurface[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [microphoneConsent, setMicrophoneConsent] = useState(false);
  const [screenConsent, setScreenConsent] = useState(false);
  const [gameAudio, setGameAudio] = useState(false);
  const [session, setSession] = useState<RealtimeSession | null>(null);
  const sessionRef = useRef<RealtimeSession | null>(null);
  const [presence, setPresence] = useState<RealtimePresenceState>("idle");
  const [captions, setCaptions] = useState<string[]>([]);
  const [draftCaption, setDraftCaption] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memory, setMemory] = useState<MemoryDraft | null>(null);
  const startedAt = useRef(0);
  const voiceQueue = useRef(Promise.resolve());
  const usage = useRef<RealtimeUsage>({ ...EMPTY_USAGE });

  const updateSession = useCallback((value: RealtimeSession | null) => {
    sessionRef.current = value;
    setSession(value);
  }, []);

  useEffect(() => {
    onPresenceChange?.(presence);
    return () => onPresenceChange?.("idle");
  }, [onPresenceChange, presence]);

  useEffect(() => {
    if (openRequest > 0) setOpen(true);
  }, [openRequest]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextPreferences, captureSurfaces, recentSessions, workerStatus] = await Promise.all([
        invoke<DesktopPreferences>("desktop_preferences_get"),
        invoke<CaptureSurface[]>("list_capture_surfaces"),
        client.sessions.list(20),
        client.worker.status(),
      ]);
      const credentialProvider = credentialProviderFor(
        nextPreferences.realtime_provider,
        navigator.language || "zh-CN",
      );
      const credential = await invoke<RealtimeProviderCredentialStatus>(
        "provider_realtime_status",
        { input: { provider: credentialProvider } },
      );
      const localDeviceId = deviceId();
      for (const stale of recentSessions.items) {
        if (
          stale.device_id === localDeviceId &&
          ["starting", "active", "stopping"].includes(stale.status) &&
          workerStatus.session_id !== stale.id
        ) {
          await client.sessions.report({
            session_id: stale.id,
            status: "interrupted",
            expected_revision: stale.revision,
            audio_input_ms: stale.audio_input_ms,
            audio_output_ms: stale.audio_output_ms,
            video_frame_count: stale.video_frame_count,
            interruption_count: stale.interruption_count,
            tool_call_count: stale.tool_call_count,
            error_code: null,
          }).catch(() => undefined);
        }
      }
      const windows = captureSurfaces.filter((item) => item.kind === "window");
      setPreferences(nextPreferences);
      setCredentialReady(credential.configured);
      setSurfaces(windows);
      setSourceId((current) => current || windows[0]?.source_id || "");
      setGameAudio(nextPreferences.realtime_game_audio_default);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [client.sessions, client.worker]);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const report = useCallback(async (
    status: RealtimeSessionStatus,
    errorCode: string | null = null,
  ) => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status)) return;
    try {
      const updated = await client.sessions.report({
        session_id: current.id,
        status,
        expected_revision: current.revision,
        ...usage.current,
        error_code: errorCode,
      });
      updateSession(updated);
    } catch {
      // A newer host event may already have advanced the revision.
    }
  }, [client.sessions, updateSession]);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<WorkerEvent>("fairy-realtime-event", (event) => {
      if (disposed) return;
      const payload = event.payload;
      const current = sessionRef.current;
      if (payload.type === "worker_interrupted") {
        if (current !== null) void report("interrupted", payload.error_code);
        setPresence("error");
        setError(realtimeProviderErrorMessage(payload.error_code));
        return;
      }
      if (!("session_id" in payload) || payload.session_id !== current?.id) return;
      if (payload.type === "session_state") {
        setPresence(normalizePresence(payload.status));
        if (payload.status === "active") void report("active");
        if (payload.status === "interrupted") void report("interrupted", payload.error_code ?? "WORKER_INTERRUPTED");
        if (payload.status === "failed") {
          setError(realtimeProviderErrorMessage(payload.error_code));
          void (async () => {
            await report("failed", payload.error_code ?? "REALTIME_SESSION_FAILED");
            await client.worker.stop(payload.session_id).catch(() => undefined);
          })();
        }
      } else if (payload.type === "presence") {
        setPresence(normalizePresence(payload.state));
      } else if (payload.type === "public_caption") {
        if (payload.stable) {
          setDraftCaption((draft) => {
            const completed = mergeCaptionDelta(draft, payload.text);
            if (completed.trim()) setCaptions((items) => [...items.slice(-7), completed]);
            return "";
          });
        } else {
          setDraftCaption((draft) => mergeCaptionDelta(draft, payload.text));
        }
        if (
          payload.stable && payload.speaker === "assistant"
          && preferences?.realtime_voice_mode === "fairy"
        ) {
          voiceQueue.current = voiceQueue.current
            .catch(() => undefined)
            .then(async () => {
              const playback = await startRealtimeVoice(payload.text);
              await playback.finished;
            });
        }
      } else if (payload.type === "usage") {
        usage.current = {
          audio_input_ms: payload.audio_input_ms,
          audio_output_ms: payload.audio_output_ms,
          video_frame_count: payload.video_frame_count,
          interruption_count: payload.interruption_count,
          tool_call_count: payload.tool_call_count,
        };
      } else if (payload.type === "tool_request") {
        void client.worker.toolResult({
          session_id: payload.session_id,
          call_id: payload.call_id,
          public_summary: "This realtime tool requires confirmation in the main Fairy workspace.",
          succeeded: false,
        });
      }
    }).then((stop) => { unlisten = stop; });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [client.worker, preferences?.realtime_voice_mode, report]);

  useEffect(() => {
    if (session?.status !== "active" || preferences === null) return;
    const timeout = window.setTimeout(() => void stop(), preferences.realtime_max_session_minutes * 60_000);
    return () => window.clearTimeout(timeout);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.status, preferences?.realtime_max_session_minutes]);

  const start = async () => {
    if (
      preferences === null || credentialReady !== true || !microphoneConsent
      || !screenConsent || sourceId === ""
    ) return;
    setBusy(true);
    setError(null);
    setCaptions([]);
    setDraftCaption("");
    usage.current = { ...EMPTY_USAGE };
    setMemory(null);
    try {
      const created = await client.sessions.start({
        device_id: deviceId(),
        conversation_id: null,
        provider: preferences.realtime_provider,
        locale: navigator.language || "zh-CN",
        voice_mode: preferences.realtime_voice_mode,
        memory_mode: preferences.realtime_memory_enabled ? "progress_digest" : "none",
        microphone_consent: true,
        screen_consent: true,
        game_audio_consent: gameAudio,
        idempotency_key: crypto.randomUUID(),
      });
      updateSession(created);
      startedAt.current = Date.now();
      await client.worker.start({
        session_id: created.id,
        provider: created.provider as RealtimeWorkerProvider,
        voice_mode: created.voice_mode,
        source_id: Number(sourceId),
        screen_enabled: true,
        game_audio_enabled: gameAudio,
      });
      setPresence("connecting");
    } catch (caught) {
      setError(realtimeProviderErrorMessage(messageOf(caught)));
      if (sessionRef.current !== null) await report("failed", "REALTIME_START_FAILED");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status)) return;
    setBusy(true);
    setError(null);
    try {
      const stopping = current.status === "stopping"
        ? current
        : await client.sessions.stop({ session_id: current.id, expected_revision: current.revision });
      updateSession(stopping);
      const workerStatus = await client.worker.stop(current.id);
      usage.current = {
        audio_input_ms: workerStatus.audio_input_ms,
        audio_output_ms: workerStatus.audio_output_ms,
        video_frame_count: workerStatus.video_frame_count,
        interruption_count: workerStatus.interruption_count,
        tool_call_count: workerStatus.tool_call_count,
      };
      const completed = await client.sessions.report({
        session_id: current.id,
        status: "completed",
        expected_revision: stopping.revision,
        ...usage.current,
        error_code: null,
      });
      updateSession(completed);
      setPresence("completed");
      const surface = surfaces.find((item) => item.source_id === sourceId);
      if (preferences?.realtime_memory_enabled) {
        setMemory({ gameTitle: surface?.label ?? "Game session", progress: "", nextGoal: "" });
      }
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const saveMemory = async () => {
    if (memory === null || session === null || memory.progress.trim() === "") return;
    setBusy(true);
    try {
      await client.memories.save({
        session_id: session.id,
        game_title: memory.gameTitle.trim(),
        played_at: new Date(startedAt.current).toISOString(),
        duration_seconds: Math.max(0, Math.round((Date.now() - startedAt.current) / 1_000)),
        activities: [],
        progress_summary: memory.progress.trim(),
        next_goal: memory.nextGoal.trim() || null,
        notable_outcome: null,
      });
      setMemory(null);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const active = session !== null && !isTerminal(session.status);
  return (
    <>
      <button className="realtime-launch" type="button" onClick={() => setOpen(true)} title="Game companion">
        <Gamepad2 size={17} /><span>Companion</span>
      </button>
      {open ? <div className="realtime-backdrop" role="presentation">
        <section className="realtime-panel" role="dialog" aria-modal="true" aria-label="Game companion">
          <header><div><span>Realtime</span><h2>Game companion</h2></div><button type="button" aria-label="Close" onClick={() => { if (!active) { setOpen(false); setCaptions([]); setDraftCaption(""); } }}><X size={17} /></button></header>
          <div className="realtime-privacy"><ShieldCheck size={16} /><span>Audio, frames and captions stay in transient worker memory. Only a session audit and a summary you confirm can be saved.</span></div>
          {!active ? <div className="realtime-config">
            <label><span><Monitor size={15} /> Game window</span><select value={sourceId} onChange={(event) => setSourceId(event.target.value)} disabled={busy}>{surfaces.map((surface) => <option key={surface.source_id} value={surface.source_id}>{surface.label} · {surface.width}×{surface.height}</option>)}</select></label>
            <div className="realtime-policy"><span>Provider</span><strong>{providerLabel(preferences?.realtime_provider)}</strong><span>Voice</span><strong>{preferences?.realtime_voice_mode === "fairy" ? "Fairy local voice" : "Provider voice"}</strong></div>
            {credentialReady === false ? <div className="realtime-error" role="alert">Configure the selected realtime provider in Settings before starting.</div> : null}
            <label className="realtime-consent"><input type="checkbox" checked={microphoneConsent} onChange={(event) => setMicrophoneConsent(event.target.checked)} /><Mic size={15} /><span>Share microphone for this session</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={screenConsent} onChange={(event) => setScreenConsent(event.target.checked)} /><Monitor size={15} /><span>Share only the selected game window</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={gameAudio} onChange={(event) => setGameAudio(event.target.checked)} /><Gamepad2 size={15} /><span>Share selected game audio</span></label>
            <button className="realtime-primary" type="button" disabled={busy || credentialReady !== true || !microphoneConsent || !screenConsent || sourceId === ""} onClick={() => void start()}>{busy ? <LoaderCircle className="spin" size={15} /> : <Mic size={15} />} Start companion</button>
          </div> : <div className="realtime-live"><div className="realtime-live-status"><span className={`realtime-pulse ${presence}`} /><div><strong>{presenceLabel(presence)}</strong><small>{session.provider.replaceAll("_", " ")}</small></div><button type="button" disabled={busy} onClick={() => void stop()}><Square size={14} /> Stop</button></div><div className="realtime-captions" aria-live="polite">{captions.length === 0 && draftCaption === "" ? <span>Listening for the conversation and game context…</span> : <>{captions.map((text, index) => <p key={`${index}-${text.slice(0, 16)}`}>{text}</p>)}{draftCaption ? <p className="is-streaming">{draftCaption}</p> : null}</>}</div></div>}
          {memory ? <div className="realtime-memory"><h3>Save game progress</h3><label>Game<input value={memory.gameTitle} maxLength={160} onChange={(event) => setMemory({ ...memory, gameTitle: event.target.value })} /></label><label>Progress<textarea value={memory.progress} maxLength={800} onChange={(event) => setMemory({ ...memory, progress: event.target.value })} /></label><label>Next goal<input value={memory.nextGoal} maxLength={300} onChange={(event) => setMemory({ ...memory, nextGoal: event.target.value })} /></label><button type="button" disabled={busy || !memory.gameTitle.trim() || !memory.progress.trim()} onClick={() => void saveMemory()}><Save size={14} /> Save summary</button></div> : null}
          {error ? <div className="realtime-error" role="alert">{error}</div> : null}
        </section>
      </div> : null}
    </>
  );
}

function isTerminal(status: RealtimeSessionStatus): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(status);
}

function deviceId(): string {
  const key = "fairy.realtime.device-id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(key, created);
  return created;
}

function providerLabel(provider?: DesktopPreferences["realtime_provider"]): string {
  if (provider === "gemini_live") return "Gemini Live";
  if (provider === "glm_realtime_air") return "GLM Realtime Air";
  if (provider === "glm_realtime_flash") return "GLM Realtime Flash";
  return "Auto · Chinese uses GLM Flash";
}

export function credentialProviderFor(
  provider: DesktopPreferences["realtime_provider"],
  locale: string,
): RealtimeCredentialProvider {
  if (provider === "gemini_live") return "gemini";
  if (provider === "glm_realtime_air" || provider === "glm_realtime_flash") return "zhipu";
  return locale.toLowerCase().startsWith("zh") ? "zhipu" : "gemini";
}

export function realtimeProviderErrorMessage(code?: string | null): string {
  switch (code) {
    case "REALTIME_PROVIDER_AUTHENTICATION_FAILED":
      return "The realtime provider rejected the API key. Update it in Settings.";
    case "REALTIME_PROVIDER_QUOTA_EXHAUSTED":
      return "The realtime provider account has no available balance or quota.";
    case "REALTIME_PROVIDER_RATE_LIMITED":
      return "The realtime provider is busy or rate-limited. Try again shortly.";
    case "REALTIME_PROVIDER_TIMEOUT":
      return "The realtime provider did not respond in time.";
    case "REALTIME_PROVIDER_REQUEST_REJECTED":
    case "REALTIME_PROVIDER_PROTOCOL_ERROR":
      return "The realtime provider rejected the session configuration.";
    case "REALTIME_PROVIDER_UNAVAILABLE":
    case "REALTIME_PROVIDER_INTERRUPTED":
    case "WORKER_INTERRUPTED":
      return "The realtime provider is temporarily unavailable.";
    case "REALTIME_CREDENTIAL_MISSING":
      return "Configure the realtime provider API key in Settings before starting.";
    default:
      return "Realtime session failed.";
  }
}

function presenceLabel(value: string): string {
  if (value === "listening") return "Listening";
  if (value === "analyzing") return "Understanding the game";
  if (value === "speaking") return "Fairy is speaking";
  if (value === "connecting" || value === "starting") return "Connecting";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function normalizePresence(value: string): RealtimePresenceState {
  if (["starting", "connecting", "active", "listening", "analyzing", "speaking", "stopping", "completed", "error"].includes(value)) {
    return value as RealtimePresenceState;
  }
  if (value === "failed" || value === "interrupted") return "error";
  return "idle";
}

function messageOf(value: unknown): string {
  return value instanceof Error ? value.message : String(value || "Realtime companion unavailable");
}

export function mergeCaptionDelta(current: string, incoming: string): string {
  if (!incoming) return current;
  if (!current || incoming.startsWith(current)) return incoming;
  if (current.endsWith(incoming)) return current;
  return `${current}${incoming}`.slice(-4_000);
}
