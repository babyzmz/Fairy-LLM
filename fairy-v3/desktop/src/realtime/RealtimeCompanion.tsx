import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Brain, CheckCircle2, LoaderCircle, MessageSquareText, Mic, MicOff, Monitor, Pause, Play, RotateCcw, Search, ShieldCheck, SlidersHorizontal, Square, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  CoreClient,
  RealtimeBackendResolution,
  RealtimeRetryableMediaChannel,
  RealtimeSession,
  RealtimeSessionStatus,
  RealtimeWorkerStatus,
} from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";
import type { DesktopPreferences } from "../settings/client";
import { startRealtimeVoice } from "../voice/nativeVoice";
import {
  isPresenceProjection,
  isRealtimeAssistanceProjection,
  applyRealtimeAssistanceStateUpdate,
  isRealtimeAssistanceStateUpdate,
  mergeRealtimeAssistanceProjection,
  type RealtimeAssistanceProjection,
  type RealtimePresenceProjection,
  type RealtimePresenceState,
} from "./realtimePresence";
import { useTranscriptPersistence } from "./useTranscriptPersistence";
import { RealtimeSpeechPipeline } from "./realtimeSpeech";
import {
  createCompanionMemoryNotice,
  loadCompanionMemoryNotice,
  type CompanionMemoryNotice,
} from "./realtimeMemoryState";
import {
  activityLabel,
  assistanceStatusLabel,
  assistanceTerminal,
  backendLabel,
  cooldownHelp,
  coreErrorCode,
  deviceId,
  formatUsageMinutes,
  isTerminal,
  mergeCaptionDelta,
  messageOf,
  presenceLabel,
  realtimeProviderErrorMessage,
  resolutionGuidance,
  standbyMessage,
  voiceOutputLabel,
} from "./realtimeCompanionSupport";
import {
  EMPTY_SIDECAR,
  EMPTY_USAGE,
  type CaptureSurface,
  type RealtimeStartupStage,
  type RealtimeUsage,
  type RealtimeWorkerIdentity,
  type WorkerEvent,
} from "./realtimeCompanionModel";
import { RealtimeCaptions, RealtimeMediaStatus } from "./RealtimeMediaStatus";
export {
  credentialProviderFor,
  mergeCaptionDelta,
  realtimeProviderErrorMessage,
} from "./realtimeCompanionSupport";
import "./realtime-companion.css";

const REALTIME_STARTUP_STAGE_ORDER: Record<RealtimeStartupStage, number> = {
  resolving_backend: 0,
  creating_session: 1,
  preparing_fairy_voice: 2,
  loading_persona: 3,
  starting_backend_runtime: 4,
  acquiring_microphone: 5,
  acquiring_observed_window: 6,
  acquiring_application_audio: 7,
  active: 8,
};

function realtimeStartupStageLabel(stage: RealtimeStartupStage): string {
  switch (stage) {
    case "resolving_backend":
      return "Resolving backend";
    case "creating_session":
      return "Creating governed session";
    case "preparing_fairy_voice":
      return "Preparing Fairy voice";
    case "loading_persona":
      return "Loading Fairy Persona";
    case "starting_backend_runtime":
      return "Starting backend runtime";
    case "acquiring_microphone":
      return "Acquiring microphone";
    case "acquiring_observed_window":
      return "Acquiring observed window";
    case "acquiring_application_audio":
      return "Acquiring application audio";
    case "active":
      return "Realtime active";
  }
}

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
  const [captureMode, setCaptureMode] =
    useState<DesktopPreferences["realtime_capture_mode"]>("selected_window");
  const [session, setSession] = useState<RealtimeSession | null>(null);
  const sessionRef = useRef<RealtimeSession | null>(null);
  const mountedRef = useRef(true);
  const [presenceProjection, setPresenceProjection] =
    useState<RealtimePresenceProjection | null>(null);
  const presenceProjectionRef = useRef<RealtimePresenceProjection | null>(null);
  const workerIdentityRef = useRef<RealtimeWorkerIdentity | null>(null);
  const presence: RealtimePresenceState = presenceProjection?.state ?? "idle";
  const [captions, setCaptions] = useState<string[]>([]);
  const [draftCaption, setDraftCaption] = useState("");
  const draftCaptionRef = useRef("");
  const [busy, setBusy] = useState(false);
  const [startupStage, setStartupStage] = useState<RealtimeStartupStage | null>(null);
  const startupStageRef = useRef<RealtimeStartupStage | null>(null);
  const [startupFailureStage, setStartupFailureStage] =
    useState<RealtimeStartupStage | null>(null);
  const startupInFlight = useRef(false);
  const startupAttemptRef = useRef(0);
  const startupSessionRef =
    useRef<{ attempt: number; session: RealtimeSession } | null>(null);
  const startupCleanupRef =
    useRef(new Map<number, Promise<RealtimeSession | null>>());
  const [error, setError] = useState<string | null>(null);
  const [voiceWarning, setVoiceWarning] = useState<string | null>(null);
  const [memoryNotice, setMemoryNotice] = useState<CompanionMemoryNotice | null>(null);
  const [memoryRetrySessionId, setMemoryRetrySessionId] = useState<string | null>(null);
  const [sidecar, setSidecar] = useState<RealtimeWorkerStatus["sidecar"]>(EMPTY_SIDECAR);
  const [captureScope, setCaptureScope] =
    useState<RealtimeWorkerStatus["capture_scope"]>(null);
  const [mediaChannels, setMediaChannels] =
    useState<RealtimeWorkerStatus["media_channels"]>([]);
  const usage = useRef<RealtimeUsage>({ ...EMPTY_USAGE });
  const [liveUsage, setLiveUsage] = useState<RealtimeUsage>({ ...EMPTY_USAGE });
  const [muted, setMuted] = useState(false);
  const [paused, setPaused] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [assistance, setAssistance] = useState<RealtimeAssistanceProjection[]>([]);
  const [assistanceBusy, setAssistanceBusy] = useState<string | null>(null);
  const {
    enqueue: enqueueTranscript,
    reset: resetTranscriptPersistence,
    flush: flushTranscriptPersistence,
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

  const applyWorkerStatus = useCallback((status: RealtimeWorkerStatus) => {
    setActiveBackend(status.running ? status.backend : null);
    workerIdentityRef.current = status.running
      && status.session_id !== null
      && status.segment_id !== null
      && status.context_epoch !== null
      ? {
          sessionId: status.session_id,
          segmentId: status.segment_id,
          contextEpoch: status.context_epoch,
        }
      : null;
    setAssistance(
      Array.isArray(status.assistance)
        ? status.assistance.filter(isRealtimeAssistanceProjection).slice(-6)
        : [],
    );
    setSidecar(status.sidecar ?? EMPTY_SIDECAR);
    setCaptureScope(status.capture_scope ?? null);
    setMediaChannels(status.media_channels ?? []);
    if (!isPresenceProjection(status.presence_projection)) return;
    presenceProjectionRef.current = status.presence_projection;
    setPresenceProjection(status.presence_projection);
    setPaused(status.presence_projection.state === "privacy_paused");
  }, []);

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
        applyWorkerStatus(await client.worker.resumePrivacy({ session_id: current.id }));
        applyInput(muted, false);
      } else {
        applyWorkerStatus(await client.worker.pausePrivacy({ session_id: current.id }));
      }
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyInput, applyWorkerStatus, busy, client.worker, muted, paused]);

  const updateSession = useCallback((value: RealtimeSession | null) => {
    if (sessionRef.current?.id !== value?.id) {
      setAssistance([]);
      setAssistanceBusy(null);
    }
    sessionRef.current = value;
    setSession(value);
  }, []);

  const advanceStartupStage = useCallback((stage: RealtimeStartupStage) => {
    if (!startupInFlight.current) return;
    const current = startupStageRef.current;
    if (
      current !== null
      && REALTIME_STARTUP_STAGE_ORDER[stage] < REALTIME_STARTUP_STAGE_ORDER[current]
    ) return;
    startupStageRef.current = stage;
    setStartupStage(stage);
  }, []);

  const completeStartupAttempt = useCallback((attempt: number) => {
    if (startupAttemptRef.current !== attempt) return;
    startupInFlight.current = false;
    startupSessionRef.current = null;
    startupStageRef.current = null;
    setStartupStage(null);
    setBusy(false);
  }, []);

  const cleanupStartupAttempt = useCallback((
    attempt: number,
    created: RealtimeSession,
    status: "failed" | "cancelled" | "interrupted",
    errorCode: string,
  ): Promise<RealtimeSession | null> => {
    const existing = startupCleanupRef.current.get(attempt);
    if (existing !== undefined) return existing;
    const cleanup = (async () => {
      const stop = typeof client.worker.stop === "function"
        ? client.worker.stop(created.id)
        : Promise.resolve(null);
      if (status === "cancelled" && typeof client.sessions.stop === "function") {
        const requestCoreStop = async () => {
          try {
            return await client.sessions.stop({
              session_id: created.id,
              expected_revision: created.revision,
            });
          } catch (caught) {
            if (
              coreErrorCode(caught) !== "VERSION_CONFLICT"
              || typeof client.sessions.get !== "function"
            ) throw caught;
            const latest = await client.sessions.get(created.id);
            if (latest.status === "stopping" || isTerminal(latest.status)) return latest;
            return client.sessions.stop({
              session_id: latest.id,
              expected_revision: latest.revision,
            });
          }
        };
        const [workerStopped, coreStopped] = await Promise.allSettled([
          stop,
          requestCoreStop(),
        ]);
        if (coreStopped.status !== "fulfilled") return null;
        if (
          coreStopped.value.status !== "stopping"
          || workerStopped.status !== "fulfilled"
          || workerStopped.value === null
          || typeof client.sessions.report !== "function"
        ) return coreStopped.value;
        const worker = workerStopped.value;
        return client.sessions.report({
          session_id: coreStopped.value.id,
          status: "completed",
          expected_revision: coreStopped.value.revision,
          audio_input_ms: worker.audio_input_ms,
          audio_output_ms: worker.audio_output_ms,
          video_frame_count: worker.video_frame_count,
          interruption_count: worker.interruption_count,
          tool_call_count: worker.tool_call_count,
          error_code: null,
        });
      }
      const terminal = typeof client.sessions.report === "function"
        ? client.sessions.report({
            session_id: created.id,
            status,
            expected_revision: created.revision,
            audio_input_ms: created.audio_input_ms,
            audio_output_ms: created.audio_output_ms,
            video_frame_count: created.video_frame_count,
            interruption_count: created.interruption_count,
            tool_call_count: created.tool_call_count,
            error_code: errorCode,
          })
        : Promise.resolve(null);
      const [, reported] = await Promise.allSettled([stop, terminal]);
      return reported.status === "fulfilled" ? reported.value : null;
    })();
    startupCleanupRef.current.set(attempt, cleanup);
    if (startupCleanupRef.current.size > 16) {
      const oldest = startupCleanupRef.current.keys().next().value;
      if (oldest !== undefined) startupCleanupRef.current.delete(oldest);
    }
    return cleanup;
  }, [client.sessions, client.worker]);

  const failStartupAttempt = useCallback(async (
    attempt: number,
    errorCode: string,
    terminalStatus: "failed" | "interrupted" = "failed",
  ) => {
    if (startupAttemptRef.current !== attempt) return;
    const failedStage = startupStageRef.current;
    const pending = startupSessionRef.current?.attempt === attempt
      ? startupSessionRef.current.session
      : null;
    startupAttemptRef.current += 1;
    startupInFlight.current = false;
    startupSessionRef.current = null;
    startupStageRef.current = null;
    setStartupStage(null);
    setStartupFailureStage(failedStage);
    setBusy(false);
    setError(realtimeProviderErrorMessage(errorCode));
    if (pending === null) return;
    const terminal = await cleanupStartupAttempt(
      attempt,
      pending,
      terminalStatus,
      errorCode,
    );
    if (
      mountedRef.current
      && terminal !== null
      && sessionRef.current?.id === terminal.id
    ) {
      updateSession(terminal);
    }
  }, [cleanupStartupAttempt, updateSession]);

  const cancelStartupAttempt = useCallback((): boolean => {
    if (!startupInFlight.current) return false;
    const attempt = startupAttemptRef.current;
    const pending = startupSessionRef.current?.attempt === attempt
      ? startupSessionRef.current.session
      : null;
    startupAttemptRef.current += 1;
    startupInFlight.current = false;
    startupSessionRef.current = null;
    startupStageRef.current = null;
    setStartupStage(null);
    setBusy(pending !== null);
    if (pending !== null) {
      void cleanupStartupAttempt(
        attempt,
        pending,
        "cancelled",
        "REALTIME_START_CANCELLED",
      ).then((terminal) => {
        if (
          mountedRef.current
          && terminal !== null
          && sessionRef.current?.id === terminal.id
        ) {
          updateSession(terminal);
        }
        if (mountedRef.current) setBusy(false);
      });
    }
    return true;
  }, [cleanupStartupAttempt, updateSession]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => () => {
    const attempt = startupAttemptRef.current;
    const pending = startupSessionRef.current?.attempt === attempt
      ? startupSessionRef.current.session
      : null;
    startupAttemptRef.current += 1;
    startupInFlight.current = false;
    startupSessionRef.current = null;
    if (pending !== null) {
      void cleanupStartupAttempt(
        attempt,
        pending,
        "cancelled",
        "REALTIME_START_CANCELLED",
      );
    }
  }, [cleanupStartupAttempt]);

  const updateLivePolicy = useCallback(async (
    activityProfile: "auto" | "game" | "focus",
    interactionIntensity: "quiet" | "standard" | "active",
  ) => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status) || busy) return;
    setBusy(true);
    setError(null);
    try {
      applyWorkerStatus(await client.worker.setPolicy({
        session_id: current.id,
        activity_profile: activityProfile,
        interaction_intensity: interactionIntensity,
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyWorkerStatus, busy, client.worker]);

  const wakeStandby = useCallback(async () => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status) || busy) return;
    setBusy(true);
    setError(null);
    try {
      applyWorkerStatus(await client.worker.wake({ session_id: current.id }));
    } catch (caught) {
      const message = messageOf(caught);
      const errorCode = coreErrorCode(caught) ?? message;
      setError(errorCode.startsWith("LOCAL_") || errorCode.startsWith("REALTIME_")
        ? realtimeProviderErrorMessage(errorCode)
        : message);
    } finally {
      setBusy(false);
    }
  }, [applyWorkerStatus, busy, client.worker]);

  const retryMediaChannel = useCallback(async (
    channel: RealtimeRetryableMediaChannel,
  ) => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status) || busy) return;
    setBusy(true);
    setError(null);
    try {
      applyWorkerStatus(await client.worker.retryMedia({
        session_id: current.id,
        channel,
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyWorkerStatus, busy, client.worker]);

  const replaceObservedWindow = useCallback(async () => {
    const current = sessionRef.current;
    if (
      current === null || isTerminal(current.status) || busy || sourceId === ""
    ) return;
    setBusy(true);
    setError(null);
    try {
      applyWorkerStatus(await client.worker.replaceSource({
        session_id: current.id,
        source_id: Number(sourceId),
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyWorkerStatus, busy, client.worker, sourceId]);

  const extendPresence = useCallback(async () => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status) || busy) return;
    setBusy(true);
    setError(null);
    try {
      applyWorkerStatus(await client.worker.extend({
        session_id: current.id,
        additional_minutes: 30,
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }, [applyWorkerStatus, busy, client.worker]);

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
    setMemoryNotice(null);
    setMemoryRetrySessionId(null);
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
      setSidecar(workerStatus.sidecar ?? EMPTY_SIDECAR);
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
        applyWorkerStatus(workerStatus);
      } else {
        const latestCompleted = recentSessions.items.find((item) => isTerminal(item.status));
        if (latestCompleted !== undefined) {
          const notice = await loadCompanionMemoryNotice(client, latestCompleted.id)
            .catch(() => null);
          if (notice !== null) {
            updateSession(latestCompleted);
            setMemoryNotice(notice);
          }
        }
      }
      const windows = captureSurfaces.filter((item) => item.kind === "window");
      setPreferences(nextPreferences);
      setCaptureMode(nextPreferences.realtime_capture_mode);
      if (!workerStatus.running) {
        setApplicationAudioConsent(nextPreferences.realtime_game_audio_default);
      }
      if (!workerStatus.running) setActiveBackend(null);
      setSurfaces(windows);
      setSourceId((current) => current || windows[0]?.source_id || "");
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [applyWorkerStatus, client, hostInvoke, updateSession]);

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

  useEffect(() => {
    if (resolution?.backend === "cloud_live") {
      setCaptureMode("selected_window");
      setApplicationAudioConsent(false);
    }
  }, [resolution?.backend]);

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
        const pending = startupSessionRef.current;
        if (
          startupInFlight.current
          && pending !== null
          && pending.session.id === current.id
        ) {
          void failStartupAttempt(
            pending.attempt,
            payload.error_code,
            "interrupted",
          );
          return;
        }
        void report("interrupted", payload.error_code);
        setError(realtimeProviderErrorMessage(payload.error_code));
        return;
      }
      if (payload.type === "sidecar_recovery") {
        const identity = workerIdentityRef.current;
        if (
          payload.segment_id !== null
          && identity !== null
          && payload.segment_id !== identity.segmentId
        ) return;
        setSidecar({
          restart_used: payload.restart_used,
          quarantined: payload.quarantined,
          context_interrupted: payload.context_interrupted,
          failure_count: payload.failure_count,
          error_code: payload.error_code,
        });
        return;
      }
      if (payload.type === "startup_stage") {
        const pending = startupSessionRef.current;
        if (
          !startupInFlight.current
          || pending === null
          || pending.attempt !== startupAttemptRef.current
          || pending.session.id !== payload.session_id
        ) return;
        advanceStartupStage(payload.stage);
        if (payload.stage === "active") {
          completeStartupAttempt(pending.attempt);
        }
        return;
      }
      if (!("session_id" in payload) || payload.session_id !== current?.id) return;
      if (isTerminal(current.status)) return;
      const identity = workerIdentityRef.current;
      if (
        payload.type !== "session_state"
        && payload.type !== "presence_projection"
        && "segment_id" in payload
        && "context_epoch" in payload
        && (
          identity === null
          || payload.segment_id !== identity.segmentId
          || payload.context_epoch !== identity.contextEpoch
        )
      ) return;
      if (payload.type === "session_state") {
        setActiveBackend(payload.backend);
        if (payload.status === "active") {
          const pending = startupSessionRef.current;
          if (pending !== null && pending.session.id === payload.session_id) {
            completeStartupAttempt(pending.attempt);
          }
          void report("active");
        }
        if (payload.status === "interrupted") void report("interrupted", payload.error_code ?? "WORKER_INTERRUPTED");
        if (payload.status === "failed") {
          const pending = startupSessionRef.current;
          if (
            startupInFlight.current
            && pending !== null
            && pending.session.id === payload.session_id
          ) {
            void failStartupAttempt(
              pending.attempt,
              payload.error_code ?? "REALTIME_SESSION_FAILED",
            );
            return;
          }
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
        workerIdentityRef.current = {
          sessionId: payload.session_id,
          segmentId: payload.segment_id,
          contextEpoch: payload.context_epoch,
        };
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
      } else if (payload.type === "media_channel_state") {
        setMediaChannels((current) => {
          const existing = current.find((item) => item.channel === payload.channel);
          if (existing !== undefined && existing.sequence >= payload.sequence) return current;
          return [
            ...current.filter((item) => item.channel !== payload.channel),
            {
              channel: payload.channel,
              sequence: payload.sequence,
              status: payload.status,
              error_code: payload.error_code,
            },
          ];
        });
      } else if (payload.type === "capture_scope_state") {
        setCaptureScope((currentScope) => {
          if (
            currentScope !== null
            && currentScope.source_sequence >= payload.source_sequence
          ) return currentScope;
          return {
            mode: payload.mode,
            source_sequence: payload.source_sequence,
            source_available: payload.source_available,
            privacy_paused: payload.privacy_paused,
            sensitive_category: payload.sensitive_category,
            error_code: payload.error_code,
          };
        });
      } else if (payload.type === "capture_source_changed") {
        setSourceId(String(payload.source_id));
      } else if (
        payload.type === "assistance_state"
        && isRealtimeAssistanceStateUpdate(payload)
      ) {
        setAssistance((current) => applyRealtimeAssistanceStateUpdate(current, payload));
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
    advanceStartupStage,
    completeStartupAttempt,
    enqueueTranscript,
    failStartupAttempt,
    fairySpeech,
    report,
  ]);

  const start = async () => {
    if (
      startupInFlight.current
      ||
      preferences === null
      || resolution?.available !== true || !microphoneConsent
      || !screenConsent || sourceId === ""
    ) return;
    const attempt = startupAttemptRef.current + 1;
    startupAttemptRef.current = attempt;
    startupInFlight.current = true;
    startupSessionRef.current = null;
    setBusy(true);
    startupStageRef.current = null;
    advanceStartupStage("resolving_backend");
    setStartupFailureStage(null);
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
    setAssistance([]);
    setAssistanceBusy(null);
    setCaptureScope(null);
    setMediaChannels([]);
    setMemoryNotice(null);
    setMemoryRetrySessionId(null);
    workerIdentityRef.current = null;
    presenceProjectionRef.current = null;
    setPresenceProjection(null);
    let created: RealtimeSession | null = null;
    try {
      const resolution = await client.worker.preview({
        activity_profile: preferences.realtime_activity_profile,
        voice_output: preferences.realtime_voice_output,
        cloud_microphone_upload_consent: microphoneConsent,
        cloud_screen_upload_consent: screenConsent,
      });
      if (startupAttemptRef.current !== attempt) return;
      if (!resolution.available || resolution.backend === null) {
        throw new Error(resolution.reason ?? "REALTIME_BACKEND_UNAVAILABLE");
      }
      const selectedApplicationAudio =
        resolution.backend === "local_mini_cpm_o45" && applicationAudioConsent;
      advanceStartupStage("creating_session");
      created = await client.sessions.start({
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
        game_audio_consent: selectedApplicationAudio,
        idempotency_key: crypto.randomUUID(),
      });
      startupSessionRef.current = { attempt, session: created };
      if (startupAttemptRef.current !== attempt) {
        await cleanupStartupAttempt(
          attempt,
          created,
          "cancelled",
          "REALTIME_START_CANCELLED",
        );
        return;
      }
      updateSession(created);
      if (preferences.realtime_voice_output === "fairy_voice") {
        advanceStartupStage("preparing_fairy_voice");
        await hostInvoke("voice_worker_prepare");
        if (startupAttemptRef.current !== attempt) {
          await cleanupStartupAttempt(
            attempt,
            created,
            "cancelled",
            "REALTIME_START_CANCELLED",
          );
          return;
        }
      }
      advanceStartupStage("loading_persona");
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
        application_audio_enabled: selectedApplicationAudio,
        online_assistance_enabled: preferences.realtime_online_assistance_enabled,
        cloud_microphone_upload_consent: microphoneConsent,
        cloud_screen_upload_consent: screenConsent,
        capture_mode: resolution.backend === "local_mini_cpm_o45"
          ? captureMode
          : "selected_window",
        excluded_applications: preferences.realtime_excluded_applications,
      });
      if (startupAttemptRef.current !== attempt) {
        await cleanupStartupAttempt(
          attempt,
          created,
          "cancelled",
          "REALTIME_START_CANCELLED",
        );
        return;
      }
      applyWorkerStatus(worker);
    } catch (caught) {
      const errorCode = coreErrorCode(caught) ?? messageOf(caught);
      if (startupAttemptRef.current !== attempt) {
        if (created !== null) {
          await cleanupStartupAttempt(
            attempt,
            created,
            "cancelled",
            "REALTIME_START_CANCELLED",
          );
        }
        return;
      }
      await failStartupAttempt(attempt, errorCode);
    }
  };

  const openAssistanceChat = useCallback(async () => {
    const current = sessionRef.current;
    if (current === null) return;
    setAssistanceBusy("open");
    try {
      await hostInvoke("open_realtime_main_chat", {
        input: { session_id: current.id },
      });
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setAssistanceBusy(null);
    }
  }, [hostInvoke]);

  const cancelAssistance = useCallback(async (
    projection: RealtimeAssistanceProjection,
  ) => {
    const current = sessionRef.current;
    if (current === null || projection.session_id !== current.id) return;
    setAssistanceBusy(projection.request_id);
    try {
      const latest = await client.assistance.get({
        session_id: current.id,
        request_id: projection.request_id,
      });
      if (assistanceTerminal(latest.status)) return;
      const cancelled = await client.assistance.cancel({
        session_id: current.id,
        request_id: projection.request_id,
        expected_revision: latest.revision,
      });
      setAssistance((items) => mergeRealtimeAssistanceProjection(items, {
        ...projection,
        status: cancelled.status === "cancelled" ? "cancelled" : projection.status,
        error_code: cancelled.error_code,
        public_summary: cancelled.spoken_summary ?? projection.public_summary,
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setAssistanceBusy(null);
    }
  }, [client.assistance]);

  const createSessionDigest = useCallback(async (sessionId: string) => {
    if (preferences?.realtime_memory_enabled !== true) {
      setMemoryRetrySessionId(null);
      return;
    }
    const transcriptSaved = await flushTranscriptPersistence();
    if (!transcriptSaved) {
      setMemoryRetrySessionId(sessionId);
      setError("Session ended, but some stable captions still need saving before the summary can be created.");
      return;
    }
    try {
      const activity = presenceProjectionRef.current?.requested_activity_profile
        ?? preferences.realtime_activity_profile;
      const surface = surfaces.find((item) => item.source_id === sourceId);
      const notice = await createCompanionMemoryNotice(client, {
        sessionId,
        activity,
        subjectTitle: surface?.label ?? null,
      });
      if (sessionRef.current?.id === sessionId) {
        setMemoryNotice(notice);
        setMemoryRetrySessionId(null);
      }
    } catch (caught) {
      setMemoryRetrySessionId(sessionId);
      setError(`Session ended, but its summary was deferred. ${messageOf(caught)}`);
    }
  }, [
    client,
    flushTranscriptPersistence,
    preferences,
    sourceId,
    surfaces,
  ]);
  const retryTranscriptAndDigest = useCallback(() => {
    retryUnsaved();
    const sessionId = memoryRetrySessionId;
    if (sessionId === null) return;
    void createSessionDigest(sessionId);
  }, [createSessionDigest, memoryRetrySessionId, retryUnsaved]);

  const stop = async () => {
    const current = sessionRef.current;
    if (current === null || isTerminal(current.status)) return;
    if (startupInFlight.current) {
      cancelStartupAttempt();
      return;
    }
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
      await createSessionDigest(current.id);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  };

  const active = session !== null && !isTerminal(session.status);
  const requestedProfile = presenceProjection?.requested_activity_profile
    ?? preferences?.realtime_activity_profile
    ?? "auto";
  const effectiveActivity = presenceProjection?.effective_activity
    ?? (requestedProfile === "game" ? "game" : "focus");
  const interactionIntensity = presenceProjection?.interaction_intensity
    ?? preferences?.realtime_interaction_intensity
    ?? "standard";
  const authoritativeBackend = presenceProjection?.backend
    ?? activeBackend
    ?? (session?.provider === "local_mini_cpm_o45" ? "local_mini_cpm_o45" : "cloud_live");
  const cloudPrivacy = resolution?.requires_cloud_upload_consent
    ?? preferences?.realtime_backend !== "local_mini_cpm_o45";
  const resolvedLocal = resolution?.backend === "local_mini_cpm_o45";
  const applicationAudioAvailable = resolvedLocal;
  const close = () => {
    const cancelledStartup = cancelStartupAttempt();
    if (windowMode) {
      onClose?.();
      return;
    }
    if (!active || cancelledStartup) {
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
          <div className={`realtime-privacy ${cloudPrivacy ? "is-cloud" : "is-local"}`}><ShieldCheck size={16} /><span>{cloudPrivacy ? "Cloud Live sends only this session’s enabled microphone and explicitly selected-window frames to the configured provider. Application audio is unavailable because the provider transport cannot preserve a separate track. Raw media is transient and never stored by Fairy." : "Local MiniCPM processes enabled microphone, observed-window frames, and optional selected-application audio on this device. Raw media stays in transient local memory."} Spoken captions remain on this device in the linked conversation.</span></div>
          {startupStage !== null ? (
            <div className="realtime-note" role="status" aria-label="Realtime startup">
              {realtimeStartupStageLabel(startupStage)}
            </div>
          ) : null}
          {!active ? <div className="realtime-config">
            <label><span><Monitor size={15} /> {captureMode === "follow_foreground" && resolvedLocal ? "Starting observed window" : "Observed window"}</span><select value={sourceId} onChange={(event) => setSourceId(event.target.value)} disabled={busy}>{surfaces.map((surface) => <option key={surface.source_id} value={surface.source_id}>{surface.label} · {surface.width}×{surface.height}</option>)}</select></label>
            {resolvedLocal ? (
              <label>
                <span><SlidersHorizontal size={15} /> Observation scope</span>
                <select
                  value={captureMode}
                  onChange={(event) => setCaptureMode(
                    event.target.value as DesktopPreferences["realtime_capture_mode"],
                  )}
                  disabled={busy}
                >
                  <option value="selected_window">Keep selected window</option>
                  <option value="follow_foreground">Follow foreground locally</option>
                </select>
              </label>
            ) : null}
            <div className="realtime-policy realtime-backend-card"><span>Backend</span><strong>{backendLabel(resolution?.backend, resolution?.requires_cloud_upload_consent, preferences?.realtime_cloud_provider)}</strong><span>Voice</span><strong>{voiceOutputLabel(preferences?.realtime_voice_output)}</strong></div>
            {resolution?.available === false ? <div className="realtime-error" role="alert">{resolutionGuidance(resolution.reason)}</div> : null}
            {resolution === null ? <div className="realtime-note" role="status">Checking backend readiness…</div> : null}
            <label className="realtime-consent"><input type="checkbox" checked={microphoneConsent} onChange={(event) => setMicrophoneConsent(event.target.checked)} /><Mic size={15} /><span>{cloudPrivacy ? "Upload microphone for this Cloud session" : "Use microphone for this Local session"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={screenConsent} onChange={(event) => setScreenConsent(event.target.checked)} /><Monitor size={15} /><span>{cloudPrivacy ? "Upload only the explicitly selected window" : captureMode === "follow_foreground" ? "Observe the foreground window locally, excluding protected apps" : "Process only the selected window locally"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={applicationAudioConsent} disabled={!applicationAudioAvailable} onChange={(event) => setApplicationAudioConsent(event.target.checked)} /><Volume2 size={15} /><span>{applicationAudioAvailable ? "Process selected application audio locally" : "Application audio unavailable for this Cloud provider"}</span></label>
            <p className="realtime-note">System-wide audio is never captured. Each enabled source remains scoped to this session.</p>
            <button className="realtime-primary" type="button" disabled={busy || resolution?.available !== true || !microphoneConsent || !screenConsent || sourceId === ""} onClick={() => void start()}>{busy ? <LoaderCircle className="spin" size={15} /> : <Mic size={15} />} Start Realtime</button>
          </div> : (
            <div className="realtime-live">
              <div className="realtime-live-status">
                <span className={`realtime-pulse ${presence}`} />
                <div>
                  <strong>{presenceLabel(presence)}</strong>
                  <small>{backendLabel(
                    authoritativeBackend,
                    false,
                    presenceProjection?.cloud_provider ?? preferences?.realtime_cloud_provider,
                  )}</small>
                </div>
                <button
                  type="button"
                  disabled={busy && !startupInFlight.current}
                  onClick={() => void stop()}
                >
                  <Square size={14} /> Stop
                </button>
              </div>
              <div className="realtime-session-policy" aria-label="Realtime activity policy">
                <label>
                  <span>Profile</span>
                  <select
                    value={requestedProfile}
                    disabled={busy || presence === "privacy_paused"}
                    onChange={(event) => void updateLivePolicy(
                      event.target.value as "auto" | "game" | "focus",
                      interactionIntensity,
                    )}
                  >
                    <option value="auto">Auto</option>
                    <option value="game">Game</option>
                    <option value="focus">Focus</option>
                  </select>
                </label>
                <label>
                  <span>Intensity</span>
                  <select
                    value={interactionIntensity}
                    disabled={busy || presence === "privacy_paused"}
                    onChange={(event) => void updateLivePolicy(
                      requestedProfile,
                      event.target.value as "quiet" | "standard" | "active",
                    )}
                  >
                    <option value="quiet">Quiet</option>
                    <option value="standard">Standard</option>
                    <option value="active">Active</option>
                  </select>
                </label>
                <div>
                  <span>Effective</span>
                  <strong>{activityLabel(effectiveActivity)}</strong>
                </div>
                <div>
                  <span>Backend</span>
                  <strong>{authoritativeBackend === "local_mini_cpm_o45" ? "Local" : "Cloud"}</strong>
                </div>
                {captureScope !== null ? (
                  <div>
                    <span>Observation</span>
                    <strong>{captureScope.mode === "follow_foreground"
                      ? "Follow foreground"
                      : captureScope.source_available
                        ? "Selected window"
                        : "Source unavailable"}</strong>
                  </div>
                ) : null}
                {captureScope?.privacy_paused ? (
                  <div>
                    <span>Privacy</span>
                    <strong>Observation paused</strong>
                  </div>
                ) : null}
              </div>
              <p className="realtime-policy-help">
                {cooldownHelp(effectiveActivity, interactionIntensity)}
              </p>
              <RealtimeMediaStatus
                busy={busy}
                captureScope={captureScope}
                channels={mediaChannels}
                onReplaceSource={() => void replaceObservedWindow()}
                onRetry={(channel) => void retryMediaChannel(channel)}
                onSourceChange={setSourceId}
                sourceId={sourceId}
                surfaces={surfaces}
              />
              {assistance.length > 0 ? (
                <section className="realtime-assistance" aria-label="Core assistance">
                  {assistance.slice(-3).map((item) => {
                    const terminal = assistanceTerminal(item.status);
                    return (
                      <article key={item.request_id} data-status={item.status}>
                        <div className="realtime-assistance-heading">
                          <Search size={14} aria-hidden="true" />
                          <strong>Core assistance</strong>
                          <span>{assistanceStatusLabel(item.status)}</span>
                        </div>
                        <p>{item.public_intent}</p>
                        {item.public_summary ? (
                          <small>{item.public_summary}</small>
                        ) : item.status === "awaiting_approval" ? (
                          <small>Approval is waiting in the main Fairy workspace.</small>
                        ) : null}
                        <div className="realtime-assistance-actions">
                          <button
                            type="button"
                            disabled={assistanceBusy !== null}
                            onClick={() => void openAssistanceChat()}
                          >
                            <MessageSquareText size={13} /> Open main chat
                          </button>
                          {!terminal ? (
                            <button
                              type="button"
                              disabled={assistanceBusy !== null}
                              onClick={() => void cancelAssistance(item)}
                            >
                              Cancel
                            </button>
                          ) : null}
                        </div>
                      </article>
                    );
                  })}
                </section>
              ) : null}
              {presence === "standby" ? (
                <div className="realtime-standby" role="status">
                  <span>{standbyMessage(presenceProjection?.standby_reason ?? null)}</span>
                  {presenceProjection?.duration_extension_required ? (
                    <button type="button" disabled={busy} onClick={() => void extendPresence()}>
                      Extend 30 min
                    </button>
                  ) : presenceProjection?.wake_available ? (
                    <button type="button" disabled={busy} onClick={() => void wakeStandby()}>
                      Wake
                    </button>
                  ) : null}
                </div>
              ) : null}
              <div className="realtime-usage" aria-label="Session usage">
                <span>Voice {formatUsageMinutes(
                  liveUsage.audio_input_ms + liveUsage.audio_output_ms,
                )}</span>
                <span>Frames {liveUsage.video_frame_count}</span>
              </div>
              <RealtimeCaptions captions={captions} draftCaption={draftCaption} />
            </div>
          )}
          {sidecar.restart_used ? (
            <div
              className={`realtime-sidecar-state${sidecar.quarantined ? " is-quarantined" : ""}`}
              role={sidecar.quarantined ? "alert" : "status"}
            >
              <RotateCcw size={14} aria-hidden="true" />
              <span>
                <strong>{sidecar.quarantined
                  ? "Local runtime needs verification"
                  : "Local runtime recovered"}</strong>
                <small>{sidecar.quarantined
                  ? "Automatic recovery is disabled until Verify succeeds in main Settings."
                  : sidecar.context_interrupted
                    ? "The Sidecar restarted once in a new segment; prior context was interrupted."
                    : "The Sidecar restarted once in a new segment."}</small>
              </span>
            </div>
          ) : null}
          {memoryNotice !== null && memoryNotice.sessionId === session?.id ? (
            <div className="realtime-memory-notice" role="status">
              {memoryNotice.pendingCount > 0
                ? <Brain size={15} aria-hidden="true" />
                : <CheckCircle2 size={15} aria-hidden="true" />}
              <span>
                <strong>{memoryNotice.pendingCount > 0
                  ? "Memory review available"
                  : "Session summary saved"}</strong>
                <small>{memoryNotice.pendingCount > 0
                  ? `${memoryNotice.pendingCount} suggestion${memoryNotice.pendingCount === 1 ? "" : "s"} require review in the main window.`
                  : memoryNotice.savedCount > 0
                    ? `${memoryNotice.savedCount} explicit low-risk fact${memoryNotice.savedCount === 1 ? "" : "s"} saved.`
                    : "No durable facts were saved."}</small>
              </span>
              <button
                type="button"
                disabled={assistanceBusy !== null}
                onClick={() => void openAssistanceChat()}
              >
                <MessageSquareText size={13} /> Open main chat
              </button>
            </div>
          ) : null}
          {unsavedCount > 0 ? <div className="realtime-transcript-warning" role="status"><span>{unsavedCount} {unsavedCount === 1 ? "caption" : "captions"} unsaved</span><button type="button" onClick={retryTranscriptAndDigest}><RotateCcw size={13} /> Retry saving</button></div> : null}
          {memoryRetrySessionId !== null && unsavedCount === 0 ? (
            <div className="realtime-transcript-warning" role="status">
              <span>Session summary deferred</span>
              <button type="button" disabled={busy} onClick={() => void createSessionDigest(memoryRetrySessionId)}>
                <RotateCcw size={13} /> Retry summary
              </button>
            </div>
          ) : null}
          {voiceWarning ? <div className="realtime-warning" role="status">{voiceWarning}</div> : null}
          {error ? (
            <div className="realtime-error" role="alert">
              <span>{error}</span>
              {startupFailureStage !== null ? (
                <small>
                  Failed while {realtimeStartupStageLabel(startupFailureStage).toLocaleLowerCase()}.
                </small>
              ) : null}
            </div>
          ) : null}
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
