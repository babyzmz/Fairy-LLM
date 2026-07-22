import { useCallback, useEffect, useRef, useState } from "react";

import {
  resolvePresenceExperimentMode,
  resolvePresenceTargetFpsOverride,
} from "../diagnostics/experimentMode";

import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "../domain/projection";
import type { PresenceInteractionSnapshot } from "../domain/interaction";
import {
  createPresenceInteractionSource,
  type PresenceInteractionSource,
} from "../transport/interactionEvents";
import {
  createPresenceInputPresentationChannel,
  DEFAULT_INPUT_PRESENTATION,
  isNewerInputPresentation,
  type PresenceInputPresentationChannel,
} from "../transport/inputPresentation";
import {
  createPresenceChannel,
  type PresenceChannel,
} from "../transport/presenceChannel";
import {
  createPresenceVoiceLevelSource,
  type PresenceVoiceLevelSource,
} from "../transport/voiceLevelEvents";
import {
  createPresenceRenderSettingsChannel,
  DEFAULT_PRESENCE_RENDER_SETTINGS,
  loadNativePresenceRenderSettings,
  type PresenceRenderSettings,
  type PresenceRenderSettingsChannel,
} from "../transport/renderSettings";
import {
  createPresenceRuntimePolicySource,
  type PresenceRuntimePolicySource,
} from "../transport/runtimePolicyEvents";
import {
  createNativeRendererLifecycleSource,
  type NativeRendererLifecycleSource,
} from "../transport/nativeRendererLifecycle";
import { useDeferredChannelClose } from "../transport/useDeferredChannelClose";
import {
  DEFAULT_PRESENCE_RUNTIME_POLICY,
  type PresenceRuntimePolicy,
} from "../domain/runtimePolicy";
import {
  createPresenceRendererHealthHost,
  type PresenceRendererHealthHost,
} from "../host/rendererHealthHost";
import { usePresenceAccessibilityPreferences } from "../host/usePresenceAccessibility";
import { PresenceRendererCanvas } from "./PresenceRendererCanvas";
import {
  NativePresenceRendererHost,
  type NativePresenceRendererHostOptions,
  type NativeRendererState,
} from "./NativePresenceRendererHost";
import type {
  PresenceRendererHealth,
  PresenceRenderSnapshot,
} from "./presenceRenderer";
import "../presence.css";
import "./presence-render.css";

interface PresenceRenderAppProps {
  channel?: PresenceChannel;
  interactionSource?: PresenceInteractionSource;
  inputPresentationChannel?: PresenceInputPresentationChannel;
  renderSettingsChannel?: PresenceRenderSettingsChannel;
  voiceLevelSource?: PresenceVoiceLevelSource;
  runtimePolicySource?: PresenceRuntimePolicySource;
  nativeLifecycleSource?: NativeRendererLifecycleSource;
  rendererHealthHost?: PresenceRendererHealthHost;
  nativeRendererHostFactory?: (
    options: NativePresenceRendererHostOptions,
  ) => NativePresenceRendererHost;
  now?: () => number;
}

export function PresenceRenderApp({
  channel: suppliedChannel,
  interactionSource: suppliedInteractionSource,
  inputPresentationChannel: suppliedInputPresentationChannel,
  renderSettingsChannel: suppliedRenderSettingsChannel,
  voiceLevelSource: suppliedVoiceLevelSource,
  runtimePolicySource: suppliedRuntimePolicySource,
  nativeLifecycleSource: suppliedNativeLifecycleSource,
  rendererHealthHost: suppliedRendererHealthHost,
  nativeRendererHostFactory,
  now = Date.now,
}: PresenceRenderAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
  );
  const [inputPresentationChannel] = useState(
    () => suppliedInputPresentationChannel ?? createPresenceInputPresentationChannel(),
  );
  const [voiceLevelSource] = useState(
    () => suppliedVoiceLevelSource ?? createPresenceVoiceLevelSource(),
  );
  const [renderSettingsChannel] = useState(
    () => suppliedRenderSettingsChannel ?? createPresenceRenderSettingsChannel(),
  );
  const [runtimePolicySource] = useState(
    () => suppliedRuntimePolicySource ?? createPresenceRuntimePolicySource(),
  );
  const [nativeLifecycleSource] = useState(
    () => suppliedNativeLifecycleSource ?? createNativeRendererLifecycleSource(),
  );
  const [rendererHealthHost] = useState(
    () => suppliedRendererHealthHost ?? createPresenceRendererHealthHost(),
  );
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);
  const [interactionReady, setInteractionReady] = useState(false);
  const [inputPresentation, setInputPresentation] = useState(
    DEFAULT_INPUT_PRESENTATION,
  );
  const [voiceLevel, setVoiceLevel] = useState(0);
  const [renderSettings, setRenderSettings] = useState<PresenceRenderSettings>(
    DEFAULT_PRESENCE_RENDER_SETTINGS,
  );
  const [runtimePolicy, setRuntimePolicy] = useState<PresenceRuntimePolicy>(
    DEFAULT_PRESENCE_RUNTIME_POLICY,
  );
  const [forcedCompatibility, setForcedCompatibility] = useState(false);
  const [sessionDisabled, setSessionDisabled] = useState(false);
  const [nativeRendererState, setNativeRendererState] = useState<NativeRendererState>("idle");
  const nativeRendererStateRef = useRef<NativeRendererState>("idle");
  const [experimentMode] = useState(resolvePresenceExperimentMode);
  const [targetFpsOverride] = useState(resolvePresenceTargetFpsOverride);
  const accessibility = usePresenceAccessibilityPreferences();
  const targetFrameRate = targetFpsOverride ?? renderSettings.target_frame_rate;
  const handleRendererHealth = useCallback((health: PresenceRendererHealth) => {
    void rendererHealthHost.report(health).then((directive) => {
      if (directive === "force_compatibility") setForcedCompatibility(true);
      if (directive === "disable_pet") setSessionDisabled(true);
    });
  }, [rendererHealthHost]);
  const handleNativeRendererState = useCallback((state: NativeRendererState) => {
    nativeRendererStateRef.current = state;
    setNativeRendererState(state);
  }, []);
  const handleCompatibilityRendererHealth = useCallback(
    (health: PresenceRendererHealth) => {
      if (!["running", "stopping"].includes(nativeRendererStateRef.current)) {
        handleRendererHealth(health);
      }
    },
    [handleRendererHealth],
  );
  const [nativeRendererHost] = useState(() => (
    nativeRendererHostFactory ?? ((options) => new NativePresenceRendererHost(options))
  )({
    onHealth: handleRendererHealth,
    onStateChange: handleNativeRendererState,
  }));

  useEffect(() => {
    const stop = channel.onProjection((next) => {
      setProjection(next);
      setClock(now());
    });
    channel.requestProjection();
    return stop;
  }, [channel, now]);

  useDeferredChannelClose(channel, suppliedChannel === undefined);

  useEffect(() => {
    const stop = inputPresentationChannel.onPresentation((next) => {
      setInputPresentation((current) =>
        isNewerInputPresentation(next, current) ? next : current,
      );
    });
    inputPresentationChannel.request();
    return stop;
  }, [inputPresentationChannel]);

  useDeferredChannelClose(
    inputPresentationChannel,
    suppliedInputPresentationChannel === undefined,
  );

  useEffect(() => {
    const stop = renderSettingsChannel.onSettings(setRenderSettings);
    let disposed = false;
    void loadNativePresenceRenderSettings().then((settings) => {
      if (!disposed && settings !== null) setRenderSettings(settings);
    }).finally(() => {
      if (!disposed) renderSettingsChannel.request();
    });
    return () => {
      disposed = true;
      stop();
    };
  }, [renderSettingsChannel]);

  useDeferredChannelClose(
    renderSettingsChannel,
    suppliedRenderSettingsChannel === undefined,
  );

  useEffect(() => {
    const timer = window.setInterval(() => setClock(now()), 30_000);
    return () => window.clearInterval(timer);
  }, [now]);

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void interactionSource.subscribe((snapshot) => {
      if (!disposed) {
        setInteraction((current) =>
          current !== null && current.sequence >= snapshot.sequence ? current : snapshot,
        );
      }
    }).then((unlisten) => {
      if (disposed) unlisten();
      else {
        stop = unlisten;
        setInteractionReady(true);
      }
    });
    return () => {
      disposed = true;
      setInteractionReady(false);
      stop?.();
    };
  }, [interactionSource]);

  useEffect(
    () => voiceLevelSource.subscribe((sample) => setVoiceLevel(sample.level)),
    [voiceLevelSource],
  );

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void runtimePolicySource.subscribe((policy) => {
      if (!disposed) setRuntimePolicy(policy);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stop = unlisten;
    });
    return () => {
      disposed = true;
      stop?.();
    };
  }, [runtimePolicySource]);

  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: inputPresentation.motion.do_not_disturb,
    dismissed_notice_ids: [],
  });
  const reducedMotion =
    interaction?.reduced_motion === true ||
    !renderSettings.motion_enabled ||
    accessibility.reduced_motion;
  const interactionActive = interaction !== null && (
    interaction.cursor.band !== "outside" ||
    !["idle", "suspended"].includes(interaction.phase)
  );
  const motionActive = !["idle", "sleeping", "suspended"].includes(
    inputPresentation.motion.state,
  );
  const idleForMs = useRenderIdleDuration(
    motionActive || interactionActive,
    now,
  );
  const requestedMode = forcedCompatibility ? "compatibility" : renderSettings.mode;
  const renderSnapshot: PresenceRenderSnapshot = {
    interaction,
    motion: inputPresentation.motion,
    input_capsule_visible: inputPresentation.capsule_visible,
    input_capsule_width: inputPresentation.capsule_width,
    input_capsule_height: inputPresentation.capsule_height,
    input_surface_visible: inputPresentation.layout === "compact",
    reduced_motion: reducedMotion,
    reduced_transparency: accessibility.reduced_transparency,
    increased_contrast: accessibility.increased_contrast,
    sleeping: inputPresentation.motion.state === "sleeping",
    speaking: inputPresentation.motion.state === "speaking",
    voice_level: inputPresentation.motion.state === "speaking" ? voiceLevel : 0,
    work_state: view.work_state,
    size_scale: renderSettings.size_scale,
    opacity: renderSettings.opacity,
    particles_enabled:
      renderSettings.particles_enabled && experimentMode !== "no-particles",
    optics_mode: renderSettings.optics_mode,
    idle_for_ms: idleForMs,
    target_frame_rate: targetFrameRate,
    frame_rate_limit: runtimePolicy.frame_rate_limit,
  };
  const renderSnapshotRef = useRef(renderSnapshot);
  renderSnapshotRef.current = renderSnapshot;
  const nativeStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    nativeRendererHost.setRequestedMode(requestedMode);
  }, [nativeRendererHost, requestedMode]);

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void nativeLifecycleSource.subscribe((signal) => {
      if (disposed) return;
      if (signal.reason === "shutdown") {
        void nativeRendererHost.stop();
        return;
      }
      if (signal.reason === "drag_suspended") {
        // Drag mode is handled by the native session. Keep the last valid frame visible while a
        // cross-monitor candidate is prepared instead of tearing down the active surface.
        return;
      }
      if (signal.reason === "drag_ended") {
        nativeRendererHost.refreshCaptureSource(renderSnapshotRef.current);
        return;
      }
      nativeRendererHost.reconfigure(renderSnapshotRef.current);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stop = unlisten;
    });
    return () => {
      disposed = true;
      stop?.();
    };
  }, [nativeLifecycleSource, nativeRendererHost]);

  const nativeRendererRequested =
    !sessionDisabled &&
    interactionReady &&
    interaction !== null &&
    requestedMode !== "compatibility" &&
    renderSettings.optics_mode === "enhanced" &&
    experimentMode === "normal";
  const fallbackOccluded = nativeRendererOccludesFallback(
    nativeRendererRequested,
    nativeRendererState,
  );

  useEffect(() => {
    nativeRendererHost.setSnapshot(renderSnapshot);
  }, [nativeRendererHost, renderSnapshot]);

  useEffect(() => {
    if (nativeRendererRequested) {
      void nativeRendererHost.start(renderSnapshotRef.current);
    } else {
      void nativeRendererHost.stop();
    }
  }, [nativeRendererHost, nativeRendererRequested, targetFrameRate]);

  useEffect(() => {
    if (nativeStopTimerRef.current !== null) {
      clearTimeout(nativeStopTimerRef.current);
      nativeStopTimerRef.current = null;
    }
    return () => {
      // StrictMode immediately remounts effects in development. Defer teardown by one task so
      // the replay can cancel it; a real unmount still closes the native session.
      nativeStopTimerRef.current = setTimeout(() => {
        nativeStopTimerRef.current = null;
        void nativeRendererHost.stop();
      }, 0);
    };
  }, [nativeRendererHost]);

  return (
    <main
      aria-hidden="true"
      className="presence-render-window"
      data-cursor-band={interaction?.cursor.band ?? "outside"}
      data-cursor-distance={interaction?.cursor.distance_px ?? ""}
      data-cursor-x={interaction?.cursor.point.x ?? ""}
      data-cursor-y={interaction?.cursor.point.y ?? ""}
      data-expansion-direction={interaction?.placement.expansion_direction ?? "right"}
      data-anchor-x={interaction?.placement.anchor.x ?? ""}
      data-anchor-y={interaction?.placement.anchor.y ?? ""}
      data-placement-scale={interaction?.placement.scale_factor ?? ""}
      data-render-x={interaction?.placement.render_frame.x ?? ""}
      data-render-y={interaction?.placement.render_frame.y ?? ""}
      data-work-area-x={interaction?.placement.monitor_work_area.x ?? ""}
      data-work-area-y={interaction?.placement.monitor_work_area.y ?? ""}
      data-work-area-width={interaction?.placement.monitor_work_area.width ?? ""}
      data-work-area-height={interaction?.placement.monitor_work_area.height ?? ""}
      data-interaction-phase={interaction?.phase ?? "idle"}
      data-interaction-ready={String(interactionReady)}
      data-motion-activity={inputPresentation.motion.activity}
      data-motion-state={inputPresentation.motion.state}
      data-motion-surface={inputPresentation.motion.surface}
      data-input-capsule-visible={String(inputPresentation.capsule_visible)}
      data-input-presentation-session={inputPresentation.session_id}
      data-input-presentation-revision={inputPresentation.sequence}
      data-reduced-motion={String(reducedMotion)}
      data-reduced-transparency={String(accessibility.reduced_transparency)}
      data-increased-contrast={String(accessibility.increased_contrast)}
      data-speaking={String(inputPresentation.motion.state === "speaking")}
      data-work-state={view.work_state}
      data-target-frame-rate={targetFrameRate}
      data-optics-mode={renderSettings.optics_mode}
      data-frame-rate-limit={runtimePolicy.frame_rate_limit}
      data-power-saver={String(runtimePolicy.power_saver)}
      data-foreground-fullscreen={String(runtimePolicy.foreground_fullscreen)}
      data-session-disabled={String(sessionDisabled)}
      data-native-renderer-requested={String(nativeRendererRequested)}
      data-native-renderer-state={nativeRendererState}
      data-fallback-occluded={String(fallbackOccluded)}
      data-experiment-mode={experimentMode}
      data-testid="presence-render-surface"
    >
      {!sessionDisabled && !fallbackOccluded && (
        <PresenceRendererCanvas
          requestedMode={requestedMode}
          experimentMode={experimentMode}
          onHealth={handleCompatibilityRendererHealth}
          snapshot={renderSnapshot}
        />
      )}
    </main>
  );
}

export function nativeRendererOccludesFallback(
  _requested: boolean,
  state: NativeRendererState,
): boolean {
  if (state === "fallback") return false;
  return state === "running" || state === "stopping";
}

function useRenderIdleDuration(active: boolean, now: () => number): number {
  const [idleForMs, setIdleForMs] = useState(0);
  useEffect(() => {
    if (active) {
      setIdleForMs(0);
      return;
    }
    const startedAt = now();
    setIdleForMs(0);
    const timer = window.setTimeout(() => {
      setIdleForMs(Math.max(15_000, now() - startedAt));
    }, 15_000);
    return () => window.clearTimeout(timer);
  }, [active, now]);
  return idleForMs;
}
