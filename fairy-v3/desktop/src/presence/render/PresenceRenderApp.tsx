import { useEffect, useState } from "react";

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
  type PresenceRenderSettings,
  type PresenceRenderSettingsChannel,
} from "../transport/renderSettings";
import {
  createPresenceRuntimePolicySource,
  type PresenceRuntimePolicySource,
} from "../transport/runtimePolicyEvents";
import {
  DEFAULT_PRESENCE_RUNTIME_POLICY,
  type PresenceRuntimePolicy,
} from "../domain/runtimePolicy";
import {
  createPresenceRendererHealthHost,
  type PresenceRendererHealthHost,
} from "../host/rendererHealthHost";
import { PresenceRendererCanvas } from "./PresenceRendererCanvas";
import "../presence.css";
import "./presence-render.css";

interface PresenceRenderAppProps {
  channel?: PresenceChannel;
  interactionSource?: PresenceInteractionSource;
  renderSettingsChannel?: PresenceRenderSettingsChannel;
  voiceLevelSource?: PresenceVoiceLevelSource;
  runtimePolicySource?: PresenceRuntimePolicySource;
  rendererHealthHost?: PresenceRendererHealthHost;
  now?: () => number;
}

export function PresenceRenderApp({
  channel: suppliedChannel,
  interactionSource: suppliedInteractionSource,
  renderSettingsChannel: suppliedRenderSettingsChannel,
  voiceLevelSource: suppliedVoiceLevelSource,
  runtimePolicySource: suppliedRuntimePolicySource,
  rendererHealthHost: suppliedRendererHealthHost,
  now = Date.now,
}: PresenceRenderAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
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
  const [rendererHealthHost] = useState(
    () => suppliedRendererHealthHost ?? createPresenceRendererHealthHost(),
  );
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);
  const [voiceLevel, setVoiceLevel] = useState(0);
  const [renderSettings, setRenderSettings] = useState<PresenceRenderSettings>(
    DEFAULT_PRESENCE_RENDER_SETTINGS,
  );
  const [runtimePolicy, setRuntimePolicy] = useState<PresenceRuntimePolicy>(
    DEFAULT_PRESENCE_RUNTIME_POLICY,
  );
  const [forcedCompatibility, setForcedCompatibility] = useState(false);
  const [sessionDisabled, setSessionDisabled] = useState(false);

  useEffect(() => {
    const stop = channel.onProjection((next) => {
      setProjection(next);
      setClock(now());
    });
    channel.requestProjection();
    return stop;
  }, [channel, now]);

  useEffect(() => {
    if (suppliedChannel !== undefined) return;
    return () => channel.close();
  }, [channel, suppliedChannel]);

  useEffect(() => {
    const stop = renderSettingsChannel.onSettings(setRenderSettings);
    renderSettingsChannel.request();
    return stop;
  }, [renderSettingsChannel]);

  useEffect(() => {
    if (suppliedRenderSettingsChannel !== undefined) return;
    return () => renderSettingsChannel.close();
  }, [renderSettingsChannel, suppliedRenderSettingsChannel]);

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
      else stop = unlisten;
    });
    return () => {
      disposed = true;
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
    quiet_mode: false,
    dismissed_notice_ids: [],
  });
  const reducedMotion =
    interaction?.reduced_motion === true ||
    !renderSettings.motion_enabled ||
    (typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const interactionActive = interaction !== null && (
    interaction.cursor.band !== "outside" ||
    !["idle", "suspended"].includes(interaction.phase)
  );
  const workActive = ["analyzing", "tool", "streaming"].includes(view.work_state);
  const idleForMs = useRenderIdleDuration(
    projection.speaking || interactionActive || workActive,
    now,
  );
  const requestedMode = forcedCompatibility ? "compatibility" : renderSettings.mode;
  return (
    <main
      aria-hidden="true"
      className="presence-render-window"
      data-cursor-band={interaction?.cursor.band ?? "outside"}
      data-expansion-direction={interaction?.placement.expansion_direction ?? "right"}
      data-interaction-phase={interaction?.phase ?? "idle"}
      data-reduced-motion={String(reducedMotion)}
      data-speaking={String(projection.speaking)}
      data-work-state={view.work_state}
      data-frame-rate-limit={runtimePolicy.frame_rate_limit}
      data-power-saver={String(runtimePolicy.power_saver)}
      data-foreground-fullscreen={String(runtimePolicy.foreground_fullscreen)}
      data-session-disabled={String(sessionDisabled)}
      data-testid="presence-render-surface"
    >
      {!sessionDisabled && (
        <PresenceRendererCanvas
          requestedMode={requestedMode}
          onHealth={(health) => {
            void rendererHealthHost.report(health).then((directive) => {
              if (directive === "force_compatibility") setForcedCompatibility(true);
              if (directive === "disable_pet") setSessionDisabled(true);
            });
          }}
          snapshot={{
            interaction,
            reduced_motion: reducedMotion,
            sleeping: view.density === "quiet",
            speaking: projection.speaking,
            voice_level: projection.speaking ? voiceLevel : 0,
            work_state: view.work_state,
            size_scale: renderSettings.size_scale,
            opacity: renderSettings.opacity,
            particles_enabled: renderSettings.particles_enabled,
            idle_for_ms: idleForMs,
            frame_rate_limit: runtimePolicy.frame_rate_limit,
          }}
        />
      )}
    </main>
  );
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
