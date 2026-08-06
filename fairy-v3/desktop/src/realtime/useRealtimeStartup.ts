import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { CoreClient, RealtimeSession } from "../core/client";
import { coreErrorCode, isTerminal, realtimeProviderErrorMessage } from "./realtimeCompanionSupport";
import type { RealtimeStartupStage } from "./realtimeCompanionModel";

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

export function realtimeStartupStageLabel(stage: RealtimeStartupStage): string {
  switch (stage) {
    case "resolving_backend": return "Resolving backend";
    case "creating_session": return "Creating governed session";
    case "preparing_fairy_voice": return "Preparing Fairy voice";
    case "loading_persona": return "Loading Fairy Persona";
    case "starting_backend_runtime": return "Starting backend runtime";
    case "acquiring_microphone": return "Acquiring microphone";
    case "acquiring_observed_window": return "Acquiring observed window";
    case "acquiring_application_audio": return "Acquiring application audio";
    case "active": return "Realtime active";
  }
}

export function useRealtimeStartup({
  client,
  sessionRef,
  updateSession,
  setBusy,
  setError,
}: {
  client: CoreClient["realtime"];
  sessionRef: MutableRefObject<RealtimeSession | null>;
  updateSession(value: RealtimeSession | null): void;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
}) {
  const mountedRef = useRef(true);
  const [startupStage, setStartupStage] = useState<RealtimeStartupStage | null>(null);
  const startupStageRef = useRef<RealtimeStartupStage | null>(null);
  const [startupFailureStage, setStartupFailureStage] =
    useState<RealtimeStartupStage | null>(null);
  const startupInFlight = useRef(false);
  const startupAttemptRef = useRef(0);
  const startupSessionRef = useRef<{ attempt: number; session: RealtimeSession } | null>(null);
  const startupCleanupRef = useRef(new Map<number, Promise<RealtimeSession | null>>());

  const advanceStartupStage = useCallback((stage: RealtimeStartupStage) => {
    if (!startupInFlight.current) return;
    const current = startupStageRef.current;
    if (current !== null && REALTIME_STARTUP_STAGE_ORDER[stage] < REALTIME_STARTUP_STAGE_ORDER[current]) return;
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
  }, [setBusy]);

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
            return await client.sessions.stop({ session_id: created.id, expected_revision: created.revision });
          } catch (caught) {
            if (coreErrorCode(caught) !== "VERSION_CONFLICT" || typeof client.sessions.get !== "function") throw caught;
            const latest = await client.sessions.get(created.id);
            if (latest.status === "stopping" || isTerminal(latest.status)) return latest;
            return client.sessions.stop({ session_id: latest.id, expected_revision: latest.revision });
          }
        };
        const [workerStopped, coreStopped] = await Promise.allSettled([stop, requestCoreStop()]);
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
    const terminal = await cleanupStartupAttempt(attempt, pending, terminalStatus, errorCode);
    if (mountedRef.current && terminal !== null && sessionRef.current?.id === terminal.id) {
      updateSession(terminal);
    }
  }, [cleanupStartupAttempt, sessionRef, setBusy, setError, updateSession]);

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
      void cleanupStartupAttempt(attempt, pending, "cancelled", "REALTIME_START_CANCELLED")
        .then((terminal) => {
          if (mountedRef.current && terminal !== null && sessionRef.current?.id === terminal.id) {
            updateSession(terminal);
          }
          if (mountedRef.current) setBusy(false);
        });
    }
    return true;
  }, [cleanupStartupAttempt, sessionRef, setBusy, updateSession]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
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
      void cleanupStartupAttempt(attempt, pending, "cancelled", "REALTIME_START_CANCELLED");
    }
  }, [cleanupStartupAttempt]);

  return {
    advanceStartupStage,
    cancelStartupAttempt,
    cleanupStartupAttempt,
    completeStartupAttempt,
    failStartupAttempt,
    startupAttemptRef,
    startupFailureStage,
    setStartupFailureStage,
    startupInFlight,
    startupSessionRef,
    startupStage,
    startupStageRef,
  };
}
