import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { LoaderCircle, Mic, MicOff, Monitor, Pause, Play, RotateCcw, Save, ShieldCheck, SlidersHorizontal, Square, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  CoreClient,
  RealtimeBackendResolution,
  RealtimeCredentialProvider,
  RealtimeSession,
  RealtimeSessionStatus,
} from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";
import type { DesktopPreferences } from "../settings/client";
import { startRealtimeVoice } from "../voice/nativeVoice";
import {
  isPresenceProjection,
  type RealtimePresenceProjection,
  type RealtimePresenceState,
} from "./realtimePresence";
import { useTranscriptPersistence } from "./useTranscriptPersistence";
import { RealtimeSpeechPipeline } from "./realtimeSpeech";
import "./realtime-companion.css";

interface CaptureSurface {
  kind: "display" | "window";
  source_id: string;
  label: string;
  width: number;
  height: number;
}

type WorkerEvent =
  | { type: "session_state"; session_id: string; segment_id: string; context_epoch: number; status: string; backend: "local_mini_cpm_o45" | "cloud_live"; cloud_provider?: string | null; error_code?: string | null }
  | { type: "public_caption"; session_id: string; segment_id: string; context_epoch: number; sequence: number; text: string; stable: boolean; speaker: "user" | "assistant"; speech_output?: "fairy_voice" | "provider_native_voice" | "text_only"; speech_generation?: number; persona_digest?: string }
  | ({ type: "presence_projection" } & RealtimePresenceProjection)
  | { type: "barge_in"; session_id: string; segment_id: string; context_epoch: number; speech_generation: number }
  | { type: "tool_request"; session_id: string; segment_id: string; context_epoch: number; call_id: string; tool_name: string; public_intent: string }
  | { type: "usage"; session_id: string; segment_id: string; context_epoch: number; audio_input_ms: number; audio_output_ms: number; video_frame_count: number; interruption_count: number; tool_call_count: number }
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

export function RealtimeCompanion({
  client,
  hostInvoke = invoke,
  onClose,
  onPresenceChange,
  openRequest = 0,
  windowMode = false,
}: {
  client: CoreClient["realtime"];
  hostInvoke?: InvokeFunction;
  onClose?(): void;
  onPresenceChange?(projection: RealtimePresenceProjection | null): void;
  openRequest?: number;
  windowMode?: boolean;
}) {
  const [open, setOpen] = useState(windowMode);
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [resolution, setResolution] = useState<RealtimeBackendResolution | null>(null);
  const [activeBackend, setActiveBackend] = useState<RealtimeBackendResolution["backend"]>(null);
  const [surfaces, setSurfaces] = useState<CaptureSurface[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [microphoneConsent, setMicrophoneConsent] = useState(false);
  const [screenConsent, setScreenConsent] = useState(false);
  const [applicationAudioConsent, setApplicationAudioConsent] = useState(false);
  const [session, setSession] = useState<RealtimeSession | null>(null);
  const sessionRef = useRef<RealtimeSession | null>(null);
  const [presenceProjection, setPresenceProjection] =
    useState<RealtimePresenceProjection | null>(null);
  const presenceProjectionRef = useRef<RealtimePresenceProjection | null>(null);
  const presence: RealtimePresenceState = presenceProjection?.state ?? "idle";
  const [captions, setCaptions] = useState<string[]>([]);
  const [draftCaption, setDraftCaption] = useState("");
  const draftCaptionRef = useRef("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voiceWarning, setVoiceWarning] = useState<string | null>(null);
  const [memory, setMemory] = useState<MemoryDraft | null>(null);
  const startedAt = useRef(0);
  const usage = useRef<RealtimeUsage>({ ...EMPTY_USAGE });
  const [liveUsage, setLiveUsage] = useState<RealtimeUsage>({ ...EMPTY_USAGE });
  const [muted, setMuted] = useState(false);
  const [paused, setPaused] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const {
    enqueue: enqueueTranscript,
    reset: resetTranscriptPersistence,
    retryUnsaved,
    unsavedCount,
  } = useTranscriptPersistence({
    sessionId: session?.id ?? null,
    append: (request) => client.transcript.append(request),
  });

  const applyInput = useCallback((nextMuted: boolean, nextPaused: boolean) => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status)) return;
    void client.worker.setInput({
      session_id: current.id,
      microphone: !nextMuted && !nextPaused,
      video: !nextPaused,
    }).catch(() => undefined);
  }, [client.worker]);

  const toggleMute = useCallback(() => {
    setMuted((current) => {
      const next = !current;
      if (!paused) applyInput(next, false);
      return next;
    });
  }, [applyInput, paused]);

  const togglePause = useCallback(async () => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status) || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (paused) {
        await client.worker.resumePrivacy({ session_id: current.id });
        setPaused(false);
        applyInput(muted, false);
      } else {
        await client.worker.pausePrivacy({ session_id: current.id });
        setPaused(true);
      }
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyInput, busy, client.worker, muted, paused]);

  const updateSession = useCallback((value: RealtimeSession | null) => {
    sessionRef.current = value;
    setSession(value);
  }, []);

  const fairySpeech = useMemo(
    () => new RealtimeSpeechPipeline(
      startRealtimeVoice,
      async (identity, speaking) => {
        await client.worker.speechState({ ...identity, speaking });
        if (speaking) setVoiceWarning(null);
      },
      () => {
        setVoiceWarning(
          "Fairy voice playback stopped. The realtime session is still active.",
        );
      },
    ),
    [client.worker],
  );
  const stopFairyVoice = useCallback(() => fairySpeech.stop(), [fairySpeech]);

  useEffect(() => () => stopFairyVoice(), [stopFairyVoice]);

  useEffect(() => {
    onPresenceChange?.(presenceProjection);
    return () => onPresenceChange?.(null);
  }, [onPresenceChange, presenceProjection]);

  useEffect(() => {
    if (windowMode || openRequest > 0) setOpen(true);
  }, [openRequest, windowMode]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextPreferences, captureSurfaces, recentSessions, workerStatus] = await Promise.all([
        hostInvoke<DesktopPreferences>("desktop_preferences_get"),
        hostInvoke<CaptureSurface[]>("list_capture_surfaces"),
        client.sessions.list(20),
        client.worker.status(),
      ]);
      const localDeviceId = deviceId();
      for (const stale of recentSessions.items) {
        if (
          stale.device_id === localDeviceId &&
          ["starting", "active", "stopping"].includes(stale.status) &&
          workerStatus.session_id !== stale.id
        ) {
          try {
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
            });
          } catch (caught) {
            const latest = await client.sessions.get(stale.id);
            if (
              coreErrorCode(caught) !== "VERSION_CONFLICT"
              || latest.revision === stale.revision
            ) {
              throw caught;
            }
          }
        }
      }
      if (workerStatus.running && workerStatus.session_id !== null) {
        const restoredSession = recentSessions.items.find(
          (item) => item.id === workerStatus.session_id,
        ) ?? await client.sessions.get(workerStatus.session_id);
        updateSession(restoredSession);
        usage.current = {
          audio_input_ms: workerStatus.audio_input_ms,
          audio_output_ms: workerStatus.audio_output_ms,
          video_frame_count: workerStatus.video_frame_count,
          interruption_count: workerStatus.interruption_count,
          tool_call_count: workerStatus.tool_call_count,
        };
        setLiveUsage(usage.current);
        if (isPresenceProjection(workerStatus.presence_projection)) {
          presenceProjectionRef.current = workerStatus.presence_projection;
          setPresenceProjection(workerStatus.presence_projection);
          setPaused(workerStatus.presence_projection.state === "privacy_paused");
        }
      }
      const windows = captureSurfaces.filter((item) => item.kind === "window");
      setPreferences(nextPreferences);
      setActiveBackend(workerStatus.running ? workerStatus.backend : null);
      setSurfaces(windows);
      setSourceId((current) => current || windows[0]?.source_id || "");
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [client.sessions, client.worker, hostInvoke, updateSession]);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  useEffect(() => {
    if (!open || preferences === null) {
      setResolution(null);
      return;
    }
    let disposed = false;
    setResolution(null);
    void client.worker.preview({
      activity_profile: preferences.realtime_activity_profile,
      voice_output: preferences.realtime_voice_output,
      cloud_microphone_upload_consent: microphoneConsent,
      cloud_screen_upload_consent: screenConsent,
    }).then((next) => {
      if (!disposed) setResolution(next);
    }).catch((caught) => {
      if (!disposed) {
        setResolution(null);
        setError(messageOf(caught));
      }
    });
    return () => {
      disposed = true;
    };
  }, [client.worker, microphoneConsent, open, preferences, screenConsent]);

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
    } catch (caught) {
      try {
        const latest = await client.sessions.get(current.id);
        updateSession(latest);
        if (
          coreErrorCode(caught) !== "VERSION_CONFLICT"
          || latest.revision === current.revision
        ) {
          setError(messageOf(caught));
        }
      } catch (refreshError) {
        setError(`${messageOf(caught)} ${messageOf(refreshError)}`.trim());
      }
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
        if (current === null || isTerminal(current.status)) return;
        void report("interrupted", payload.error_code);
        setError(realtimeProviderErrorMessage(payload.error_code));
        return;
      }
      if (!("session_id" in payload) || payload.session_id !== current?.id) return;
      if (isTerminal(current.status)) return;
      if (payload.type === "session_state") {
        setActiveBackend(payload.backend);
        if (payload.status === "active") void report("active");
        if (payload.status === "interrupted") void report("interrupted", payload.error_code ?? "WORKER_INTERRUPTED");
        if (payload.status === "failed") {
          setError(realtimeProviderErrorMessage(payload.error_code));
          void (async () => {
            await report("failed", payload.error_code ?? "REALTIME_SESSION_FAILED");
            await client.worker.stop(payload.session_id).catch(() => undefined);
          })();
        }
      } else if (payload.type === "presence_projection") {
        if (!isPresenceProjection(payload)) return;
        const previous = presenceProjectionRef.current;
        if (
          previous !== null
          && (
            payload.sequence <= previous.sequence
            || (
              previous.session_id === payload.session_id
              && payload.persona_digest !== previous.persona_digest
            )
          )
        ) return;
        presenceProjectionRef.current = payload;
        setPresenceProjection(payload);
        setPaused(payload.state === "privacy_paused");
      } else if (payload.type === "barge_in") {
        const activePresence = presenceProjectionRef.current;
        if (
          activePresence === null
          || payload.segment_id !== activePresence.segment_id
          || payload.context_epoch !== activePresence.context_epoch
        ) return;
        fairySpeech.interrupt({
          session_id: payload.session_id,
          segment_id: payload.segment_id,
          context_epoch: payload.context_epoch,
          speech_generation: payload.speech_generation,
        });
      } else if (payload.type === "public_caption") {
        if (payload.stable) {
          const completed = mergeCaptionDelta(draftCaptionRef.current, payload.text);
          draftCaptionRef.current = "";
          setDraftCaption("");
          const finished = completed.trim();
          if (finished) {
            setCaptions((items) => [...items.slice(-7), completed]);
            enqueueTranscript({
              session_id: payload.session_id,
              speaker: payload.speaker,
              text: finished,
            });
          }
        } else {
          const merged = mergeCaptionDelta(draftCaptionRef.current, payload.text);
          draftCaptionRef.current = merged;
          setDraftCaption(merged);
        }
        if (
          payload.speaker === "assistant"
          && payload.speech_output === "fairy_voice"
          && typeof payload.speech_generation === "number"
          && typeof payload.persona_digest === "string"
          && payload.persona_digest === presenceProjectionRef.current?.persona_digest
          && payload.segment_id === presenceProjectionRef.current?.segment_id
          && payload.context_epoch === presenceProjectionRef.current?.context_epoch
        ) {
          fairySpeech.push({
            session_id: payload.session_id,
            segment_id: payload.segment_id,
            context_epoch: payload.context_epoch,
            speech_generation: payload.speech_generation,
            text: payload.text,
            stable: payload.stable,
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
        setLiveUsage(usage.current);
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
  }, [
    client.worker,
    enqueueTranscript,
    fairySpeech,
    report,
  ]);

  const start = async () => {
    if (
      preferences === null || !preferences.realtime_beta_enabled
      || resolution?.available !== true || !microphoneConsent
      || !screenConsent || sourceId === ""
    ) return;
    setBusy(true);
    setError(null);
    setVoiceWarning(null);
    stopFairyVoice();
    resetTranscriptPersistence();
    setCaptions([]);
    setDraftCaption("");
    draftCaptionRef.current = "";
    usage.current = { ...EMPTY_USAGE };
    setLiveUsage({ ...EMPTY_USAGE });
    setMuted(false);
    setPaused(false);
    setControlsOpen(false);
    setMemory(null);
    presenceProjectionRef.current = null;
    setPresenceProjection(null);
    try {
      const priorMinutes = await todaysRealtimeMinutes(client);
      const resolution = await client.worker.preview({
        activity_profile: preferences.realtime_activity_profile,
        voice_output: preferences.realtime_voice_output,
        cloud_microphone_upload_consent: microphoneConsent,
        cloud_screen_upload_consent: screenConsent,
      });
      if (!resolution.available || resolution.backend === null) {
        throw new Error(resolution.reason ?? "REALTIME_BACKEND_UNAVAILABLE");
      }
      if (
        resolution.backend === "cloud_live"
        && priorMinutes >= preferences.realtime_cloud_daily_limit_minutes
      ) {
        setError(
          `Daily realtime limit reached (${preferences.realtime_cloud_daily_limit_minutes} min). Start a new session tomorrow to control provider cost.`,
        );
        return;
      }
      const created = await client.sessions.start({
        device_id: deviceId(),
        conversation_id: null,
        provider: resolution.backend === "local_mini_cpm_o45"
          ? "local_mini_cpm_o45"
          : preferences.realtime_cloud_provider,
        locale: navigator.language || "zh-CN",
        voice_mode: preferences.realtime_voice_output === "fairy_voice" ? "fairy" : "native",
        memory_mode: preferences.realtime_memory_enabled ? "progress_digest" : "none",
        microphone_consent: true,
        screen_consent: true,
        game_audio_consent: false,
        idempotency_key: crypto.randomUUID(),
      });
      updateSession(created);
      startedAt.current = Date.now();
      const worker = await client.worker.start({
        session_id: created.id,
        resolution_token: resolution.resolution_token,
        locale: navigator.language || "zh-CN",
        backend: resolution.backend,
        cloud_provider: resolution.cloud_provider,
        activity_profile: preferences.realtime_activity_profile,
        interaction_intensity: preferences.realtime_interaction_intensity,
        voice_output: preferences.realtime_voice_output,
        source_id: Number(sourceId),
        microphone_enabled: true,
        screen_enabled: true,
        application_audio_enabled: applicationAudioConsent,
        online_assistance_enabled: preferences.realtime_online_assistance_enabled,
        cloud_microphone_upload_consent: microphoneConsent,
        cloud_screen_upload_consent: screenConsent,
      });
      setActiveBackend(worker.backend ?? resolution.backend);
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
    stopFairyVoice();
    try {
      const requestCoreStop = async () => {
        try {
          return await client.sessions.stop({
            session_id: current.id,
            expected_revision: current.revision,
          });
        } catch (caught) {
          if (coreErrorCode(caught) !== "VERSION_CONFLICT") throw caught;
          const latest = await client.sessions.get(current.id);
          updateSession(latest);
          if (latest.status === "stopping" || isTerminal(latest.status)) return latest;
          return client.sessions.stop({
            session_id: latest.id,
            expected_revision: latest.revision,
          });
        }
      };
      const [coreStop, workerStop] = await Promise.allSettled([
        requestCoreStop(),
        client.worker.stop(current.id),
      ]);
      if (coreStop.status === "rejected") {
        setError(messageOf(coreStop.reason));
        try {
          updateSession(await client.sessions.get(current.id));
        } catch (refreshError) {
          setError(`${messageOf(coreStop.reason)} ${messageOf(refreshError)}`.trim());
        }
        return;
      }
      const stopping = coreStop.value;
      updateSession(stopping);
      if (workerStop.status === "rejected") {
        setError(messageOf(workerStop.reason));
        if (stopping.status === "stopping") {
          await report("interrupted", "WORKER_INTERRUPTED");
        }
        return;
      }
      const workerStatus = workerStop.value;
      usage.current = {
        audio_input_ms: workerStatus.audio_input_ms,
        audio_output_ms: workerStatus.audio_output_ms,
        video_frame_count: workerStatus.video_frame_count,
        interruption_count: workerStatus.interruption_count,
        tool_call_count: workerStatus.tool_call_count,
      };
      if (stopping.status !== "stopping") {
        return;
      }
      await report("completed");
      if (sessionRef.current?.status !== "completed") return;
      setActiveBackend(null);
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
  const cloudPrivacy = resolution?.requires_cloud_upload_consent
    ?? preferences?.realtime_backend !== "local_mini_cpm_o45";
  const close = () => {
    if (windowMode) {
      onClose?.();
      return;
    }
    if (!active) {
      setOpen(false);
      setCaptions([]);
      setDraftCaption("");
      draftCaptionRef.current = "";
    }
  };
  return (
    <>
      {open ? <div className={`realtime-backdrop${windowMode ? " is-window" : ""}`} role="presentation">
        <section className="realtime-panel" role="dialog" aria-modal={!windowMode} aria-label="Realtime Companion Beta">
          <header><div><span>Fairy</span><h2>Realtime Companion Beta</h2></div><button type="button" aria-label="Close" onClick={close}><X size={17} /></button></header>
          <div className={`realtime-privacy ${cloudPrivacy ? "is-cloud" : "is-local"}`}><ShieldCheck size={16} /><span>{cloudPrivacy ? "Cloud Live sends only this session’s enabled microphone, selected-window frames, and selected application audio to the configured provider. Raw media is transient and never stored by Fairy." : "Local MiniCPM processes enabled microphone, selected-window frames, and selected application audio on this device. Raw media stays in transient local memory."} Spoken captions remain on this device in the linked conversation.</span></div>
          {!active ? <div className="realtime-config">
            <label><span><Monitor size={15} /> Game window</span><select value={sourceId} onChange={(event) => setSourceId(event.target.value)} disabled={busy}>{surfaces.map((surface) => <option key={surface.source_id} value={surface.source_id}>{surface.label} · {surface.width}×{surface.height}</option>)}</select></label>
            <div className="realtime-policy realtime-backend-card"><span>Backend</span><strong>{backendLabel(resolution?.backend, resolution?.requires_cloud_upload_consent, preferences?.realtime_cloud_provider)}</strong><span>Voice</span><strong>{voiceOutputLabel(preferences?.realtime_voice_output)}</strong></div>
            {preferences?.realtime_beta_enabled === false ? <div className="realtime-error" role="alert">Enable Realtime Beta in Settings before starting.</div> : null}
            {resolution?.available === false && resolution.reason !== "REALTIME_BETA_DISABLED" ? <div className="realtime-error" role="alert">{resolutionGuidance(resolution.reason)}</div> : null}
            {resolution === null && preferences?.realtime_beta_enabled ? <div className="realtime-note" role="status">Checking backend readiness…</div> : null}
            <label className="realtime-consent"><input type="checkbox" checked={microphoneConsent} onChange={(event) => setMicrophoneConsent(event.target.checked)} /><Mic size={15} /><span>{cloudPrivacy ? "Upload microphone for this Cloud session" : "Use microphone for this Local session"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={screenConsent} onChange={(event) => setScreenConsent(event.target.checked)} /><Monitor size={15} /><span>{cloudPrivacy ? "Upload only the selected game window" : "Process only the selected game window locally"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={applicationAudioConsent} onChange={(event) => setApplicationAudioConsent(event.target.checked)} /><Volume2 size={15} /><span>{cloudPrivacy ? "Upload selected application audio" : "Process selected application audio locally"}</span></label>
            <p className="realtime-note">System-wide audio is never captured. Each enabled source remains scoped to this session.</p>
            <button className="realtime-primary" type="button" disabled={busy || resolution?.available !== true || !microphoneConsent || !screenConsent || sourceId === ""} onClick={() => void start()}>{busy ? <LoaderCircle className="spin" size={15} /> : <Mic size={15} />} Start Realtime</button>
          </div> : <div className="realtime-live"><div className="realtime-live-status"><span className={`realtime-pulse ${presence}`} /><div><strong>{presenceLabel(presence)}</strong><small>{backendLabel(activeBackend ?? (session.provider === "local_mini_cpm_o45" ? "local_mini_cpm_o45" : "cloud_live"), false, preferences?.realtime_cloud_provider)}</small></div><button type="button" disabled={busy} onClick={() => void stop()}><Square size={14} /> Stop</button></div><div className="realtime-usage" aria-label="Session usage"><span>Voice {formatUsageMinutes(liveUsage.audio_input_ms + liveUsage.audio_output_ms)}</span><span>Frames {liveUsage.video_frame_count}</span></div><div className="realtime-captions" aria-live="polite">{captions.length === 0 && draftCaption === "" ? <span>Listening for the conversation and game context…</span> : <>{captions.map((text, index) => <p key={`${index}-${text.slice(0, 16)}`}>{text}</p>)}{draftCaption ? <p className="is-streaming">{draftCaption}</p> : null}</>}</div></div>}
          {memory ? <div className="realtime-memory"><h3>Save game progress</h3><label>Game<input value={memory.gameTitle} maxLength={160} onChange={(event) => setMemory({ ...memory, gameTitle: event.target.value })} /></label><label>Progress<textarea value={memory.progress} maxLength={800} onChange={(event) => setMemory({ ...memory, progress: event.target.value })} /></label><label>Next goal<input value={memory.nextGoal} maxLength={300} onChange={(event) => setMemory({ ...memory, nextGoal: event.target.value })} /></label><button type="button" disabled={busy || !memory.gameTitle.trim() || !memory.progress.trim()} onClick={() => void saveMemory()}><Save size={14} /> Save summary</button></div> : null}
          {unsavedCount > 0 ? <div className="realtime-transcript-warning" role="status"><span>{unsavedCount} {unsavedCount === 1 ? "caption" : "captions"} unsaved</span><button type="button" onClick={retryUnsaved}><RotateCcw size={13} /> Retry saving</button></div> : null}
          {voiceWarning ? <div className="realtime-warning" role="status">{voiceWarning}</div> : null}
          {error ? <div className="realtime-error" role="alert">{error}</div> : null}
        </section>
        {active ? (
          <div className={`realtime-controls${controlsOpen ? " is-open" : ""}`}>
            {controlsOpen ? (
              <div className="realtime-controls-cluster" role="group" aria-label="Session controls">
                <button type="button" disabled={busy} onClick={() => void togglePause()} aria-pressed={paused} title={paused ? "恢复" : "暂停"}>
                  {paused ? <Play size={16} /> : <Pause size={16} />}<span>{paused ? "恢复" : "暂停"}</span>
                </button>
                <button type="button" className="realtime-controls-stop" disabled={busy} onClick={() => void stop()} title="停止">
                  <Square size={16} /><span>停止</span>
                </button>
                <button type="button" onClick={toggleMute} aria-pressed={muted} title={muted ? "开麦" : "闭麦"}>
                  {muted ? <MicOff size={16} /> : <Mic size={16} />}<span>{muted ? "开麦" : "闭麦"}</span>
                </button>
              </div>
            ) : null}
            <button
              type="button"
              className="realtime-controls-toggle"
              aria-expanded={controlsOpen}
              aria-label={controlsOpen ? "收起控制" : "展开控制"}
              onClick={() => setControlsOpen((state) => !state)}
            >
              {controlsOpen ? <X size={16} /> : <SlidersHorizontal size={16} />}
            </button>
          </div>
        ) : null}
      </div> : null}
    </>
  );
}

function isTerminal(status: RealtimeSessionStatus): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(status);
}

function formatUsageMinutes(totalMs: number): string {
  const totalSeconds = Math.max(0, Math.round(totalMs / 1_000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export async function todaysRealtimeMinutes(client: CoreClient["realtime"]): Promise<number> {
  try {
    const recent = await client.sessions.list(50);
    const today = new Date().toDateString();
    const totalMs = recent.items.reduce((sum, item) => {
      if (new Date(item.started_at).toDateString() !== today) return sum;
      return sum + Math.max(item.audio_input_ms, item.audio_output_ms);
    }, 0);
    return totalMs / 60_000;
  } catch {
    // A usage lookup failure must not block starting a session.
    return 0;
  }
}

function deviceId(): string {
  const key = "fairy.realtime.device-id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(key, created);
  return created;
}

function providerLabel(provider?: DesktopPreferences["realtime_cloud_provider"]): string {
  if (provider === "gemini_live") return "Gemini Live";
  if (provider === "glm_realtime_air") return "GLM Realtime Air";
  return "GLM Realtime Flash";
}

function backendLabel(
  backend?: RealtimeBackendResolution["backend"],
  cloudExpected = false,
  provider?: DesktopPreferences["realtime_cloud_provider"],
): string {
  if (backend === "local_mini_cpm_o45") return "Local · MiniCPM-o 4.5";
  if (backend === "cloud_live" || cloudExpected) return `Cloud Live · ${providerLabel(provider)}`;
  return "Resolving…";
}

function resolutionGuidance(reason: string | null): string {
  switch (reason) {
    case "CLOUD_UPLOAD_CONSENT_REQUIRED":
      return "Review and enable microphone and selected-window upload for this Cloud session.";
    case "REALTIME_CREDENTIAL_MISSING":
      return "Configure the selected Cloud Live provider in Settings.";
    case "AUTO_CLOUD_FALLBACK_DISABLED":
      return "Local is not ready and Cloud fallback is disabled. Review Realtime settings.";
    case "LOCAL_VOICE_OUTPUT_INCOMPATIBLE":
      return "Local MiniCPM uses Fairy voice or text-only output. Change the voice setting.";
    case "LOCAL_MODEL_MISSING":
      return "Install and verify the Local MiniCPM model in Settings.";
    case "LOCAL_RUNTIME_MISSING":
      return "The Local Omni runtime is not installed.";
    case "LOCAL_SELF_TEST_FAILED":
    case "LOCAL_RUNTIME_QUARANTINED":
      return "Verify the Local Omni runtime in Settings before starting.";
    case "LOCAL_CUDA_UNAVAILABLE":
    case "LOCAL_DRIVER_INCOMPATIBLE":
    case "LOCAL_ADAPTER_MISMATCH":
      return "Local CUDA readiness is unavailable on the active GPU configuration.";
    case "LOCAL_VRAM_INSUFFICIENT":
    case "LOCAL_FREE_VRAM_INSUFFICIENT":
      return "Local MiniCPM does not have enough available VRAM for this activity profile.";
    default:
      return "The selected realtime backend is not ready. Review Realtime settings.";
  }
}

function voiceOutputLabel(output?: DesktopPreferences["realtime_voice_output"]): string {
  if (output === "fairy_voice") return "Fairy voice";
  if (output === "text_only") return "Text only";
  return "Provider voice";
}

export function credentialProviderFor(
  provider: DesktopPreferences["realtime_cloud_provider"],
): RealtimeCredentialProvider {
  if (provider === "gemini_live") return "gemini";
  return "zhipu";
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
  if (value === "observing") return "Observing";
  if (value === "thinking") return "Thinking";
  if (value === "searching") return "Searching";
  if (value === "speaking") return "Fairy is speaking";
  if (value === "preparing") return "Preparing";
  if (value === "loading_model") return "Loading local model";
  if (value === "connecting") return "Connecting";
  if (value === "standby") return "Standing by";
  if (value === "privacy_paused") return "Privacy paused";
  if (value === "resource_limited") return "Resources limited";
  return value === "idle" ? "Idle" : "Needs attention";
}

function messageOf(value: unknown): string {
  return value instanceof Error ? value.message : String(value || "Realtime companion unavailable");
}

function coreErrorCode(value: unknown): string | null {
  if (typeof value !== "object" || value === null || !("errorCode" in value)) return null;
  return typeof value.errorCode === "string" ? value.errorCode : null;
}

export function mergeCaptionDelta(current: string, incoming: string): string {
  if (!incoming) return current;
  if (!current || incoming.startsWith(current)) return incoming;
  if (current.endsWith(incoming)) return current;
  return `${current}${incoming}`.slice(-4_000);
}
