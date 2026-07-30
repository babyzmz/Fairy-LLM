import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { type DesktopPreferences } from "../settings/client";
import type { VoiceWorkerHealth } from "../settings/client";
import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "./domain/projection";
import {
  createDefaultPresenceWindowPort,
  loadPresenceSettings,
  monitorForPosition,
  type PresenceSettings,
  type PresenceWindowPort,
  type StorageLike,
  rememberMonitorPosition,
  resolveReducedMotion,
  restoreMonitorPosition,
  savePresenceSettings,
  snapToMonitorEdges,
} from "./host/persistence";
import {
  createDefaultPetHost,
  type PetHost,
  type PetPreferencePatch,
} from "./host/petHost";
import { PresencePanel } from "./input/PresencePanel";
import { CompatibilityFairyCanvas } from "./render/CompatibilityFairyCanvas";
import {
  createPresenceSubmissionId,
  createPresenceChannel,
  type PresenceChannel,
} from "./transport/presenceChannel";
import "./presence.css";

interface PresenceAppProps {
  channel?: PresenceChannel;
  storage?: StorageLike;
  windowPort?: PresenceWindowPort;
  host?: PetHost;
  now?: () => number;
}

const MAX_DISMISSED_NOTICES = 128;

export function PresenceApp({
  channel: suppliedChannel,
  storage = window.localStorage,
  windowPort: suppliedWindowPort,
  host: suppliedHost,
  now = Date.now,
}: PresenceAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [windowPort] = useState(
    () => suppliedWindowPort ?? createDefaultPresenceWindowPort(),
  );
  const [host] = useState(() => suppliedHost ?? createDefaultPetHost());
  const [settings, setSettings] = useState<PresenceSettings>(() =>
    loadPresenceSettings(storage),
  );
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<VoiceWorkerHealth["status"]>("idle");
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());
  const [hovered, setHovered] = useState(false);
  const [inputOpen, setInputOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [gaze, setGaze] = useState({ x: 0, y: 0 });
  const dragOrigin = useRef<{ x: number; y: number } | null>(null);
  const dragged = useRef(false);
  const clickTimer = useRef<number | null>(null);

  const updateLocalSettings = useCallback(
    (update: (current: PresenceSettings) => PresenceSettings) => {
      const next = update(settingsRef.current);
      settingsRef.current = next;
      savePresenceSettings(storage, next);
      setSettings(next);
    },
    [storage],
  );

  useEffect(() => {
    const stop = channel.onProjection((next) => {
      setProjection(next);
      setClosedReplyId((current) => (current === next.reply?.id ? current : null));
      setClock(now());
    });
    channel.requestProjection();
    return stop;
  }, [channel, now]);

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void host.getPreferences().then((value) => {
      if (!disposed) setPreferences(value);
    }).catch(() => undefined);
    void host.getVoiceHealth().then((value) => {
      if (!disposed) setVoiceStatus(value.status);
    }).catch(() => undefined);
    void host.onPreferences((value) => {
      if (!disposed) setPreferences(value);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stop = unlisten;
    });
    return () => {
      disposed = true;
      stop?.();
    };
  }, [host]);

  useEffect(() => {
    if (suppliedChannel !== undefined) return;
    const close = () => channel.close();
    window.addEventListener("beforeunload", close, { once: true });
    return () => window.removeEventListener("beforeunload", close);
  }, [channel, suppliedChannel]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(now()), 30_000);
    return () => window.clearInterval(timer);
  }, [now]);

  useEffect(() => {
    let disposed = false;
    let stopMoved: (() => void) | null = null;
    void (async () => {
      try {
        const [monitors, size] = await Promise.all([windowPort.monitors(), windowPort.size()]);
        if (disposed || monitors.length === 0) return;
        const restored = restoreMonitorPosition(settingsRef.current, monitors, size);
        await windowPort.setPosition(restored.position);
        stopMoved = await windowPort.onMoved((position) => {
          void persistMovedPosition(windowPort, storage, settingsRef, setSettings, position);
        });
      } catch {
        // Browser previews and monitor changes remain non-fatal.
      }
    })();
    return () => {
      disposed = true;
      stopMoved?.();
    };
  }, [storage, windowPort]);

  const systemReducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const reducedMotion =
    preferences?.reduced_motion === true ||
    resolveReducedMotion(settings.reduced_motion_override, systemReducedMotion);
  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: false,
    dismissed_notice_ids: settings.dismissed_notice_ids,
  });
  const reply = view.reply?.id === closedReplyId ? null : view.reply;
  const expanded = inputOpen || menuOpen || reply !== null;

  useEffect(() => {
    void host.setExpanded(expanded);
  }, [expanded, host]);

  const updatePetPreferences = useCallback(
    async (patch: Omit<PetPreferencePatch, "expected_revision">) => {
      if (preferences === null) return;
      try {
        const next = await host.updatePreferences({
          expected_revision: preferences.revision,
          ...patch,
        });
        setPreferences(next);
      } catch {
        const latest = await host.getPreferences();
        setPreferences(latest);
      }
    },
    [host, preferences],
  );

  function dismissNotice() {
    const notice = view.notice;
    if (notice === null) return;
    updateLocalSettings((current) => ({
      ...current,
      dismissed_notice_ids: [
        ...current.dismissed_notice_ids.filter((id) => id !== notice.id),
        notice.id,
      ].slice(-MAX_DISMISSED_NOTICES),
    }));
  }

  function handleCoreClick() {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    if (
      view.notice !== null ||
      view.work_state === "awaiting_confirmation" ||
      view.work_state === "error"
    ) {
      channel.requestWorkspaceOpen();
      void host.openMain().catch(() => undefined);
      return;
    }
    if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
    clickTimer.current = window.setTimeout(() => {
      setMenuOpen(false);
      setInputOpen((current) => !current);
      clickTimer.current = null;
    }, 220);
  }

  function handleCoreDoubleClick() {
    if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
    clickTimer.current = null;
    setInputOpen(false);
    setMenuOpen(false);
    channel.requestWorkspaceOpen();
    void host.openMain().catch(() => undefined);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0) return;
    dragOrigin.current = { x: event.clientX, y: event.clientY };
    dragged.current = false;
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    setGaze({
      x: clamp((event.clientX - bounds.left) / Math.max(1, bounds.width) * 2 - 1, -1, 1),
      y: clamp((event.clientY - bounds.top) / Math.max(1, bounds.height) * 2 - 1, -1, 1),
    });
    const origin = dragOrigin.current;
    if (origin === null || dragged.current) return;
    if (Math.hypot(event.clientX - origin.x, event.clientY - origin.y) < 5) return;
    dragged.current = true;
    setDragging(true);
    void windowPort.startDragging();
  }

  function finishPointerGesture() {
    dragOrigin.current = null;
    setDragging(false);
  }

  const muted = preferences?.pet_muted ?? false;
  const autoPlay = preferences?.voice_replies_enabled ?? true;
  const alwaysOnTop = preferences?.pet_always_on_top ?? true;

  return (
    <main
      className="presence-window"
      data-expanded={String(expanded)}
      data-reduced-motion={String(reducedMotion)}
      data-testid="presence-surface"
      onContextMenu={(event) => {
        event.preventDefault();
        setInputOpen(false);
        setMenuOpen(true);
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => {
        setHovered(false);
        setGaze({ x: 0, y: 0 });
      }}
      onPointerCancel={finishPointerGesture}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerGesture}
    >
      <PresencePanel
        actions={{
          cancelTurn: () => channel.requestChatCancel(createPresenceSubmissionId()),
          closeReply: setClosedReplyId,
          closeSubmission: () => undefined,
          dismissAmbient: () => undefined,
          dismissNotice,
          exit: () => void host.exit(),
          newChat: () => channel.requestNewChat(),
          openMain: () => {
            channel.requestWorkspaceOpen();
            void host.openMain().catch(() => undefined);
          },
          openCompanion: () => {
            void host.openCompanion().catch(() => undefined);
          },
          openReview: () => {
            channel.requestWorkspaceOpen();
            void host.openMain().catch(() => undefined);
          },
          openSettings: () => void host.openSettings(),
          prepareVoice: () => {
            void host.prepareVoice().then((health) => setVoiceStatus(health.status))
              .catch(() => setVoiceStatus("error"));
          },
          requestInputFocus: () => void host.requestInputFocus(),
          resetPosition: () => void resetPosition(windowPort, settingsRef.current),
          retrySubmission: () => undefined,
          send: (text) => channel.requestChatSend(text, createPresenceSubmissionId()),
          setInputOpen,
          setMenuOpen,
          toggleAlwaysOnTop: () =>
            void updatePetPreferences({ pet_always_on_top: !alwaysOnTop }),
          toggleAutoPlay: () => {
            const enabled = !autoPlay;
            if (!enabled) channel.requestVoiceStop();
            void updatePetPreferences({ voice_replies_enabled: enabled }).then(() =>
              enabled ? host.prepareVoice() : host.stopVoice(),
            ).then((health) => setVoiceStatus(health.status))
              .catch(() => setVoiceStatus("error"));
          },
          toggleMuted: () => {
            if (!muted) channel.requestVoiceStop();
            void updatePetPreferences({ pet_muted: !muted });
          },
          stopVoice: () => channel.requestVoiceStop(),
        }}
        alwaysOnTop={alwaysOnTop}
        ambientDialogue={null}
        autoPlay={autoPlay}
        voiceStatus={voiceStatus}
        inputOpen={inputOpen}
        menuOpen={menuOpen}
        muted={muted}
        reply={reply}
        submission={null}
        view={view}
        visible={expanded}
      />

      <button
        aria-label="Fairy companion"
        className="presence-core-button"
        onClick={handleCoreClick}
        onDoubleClick={handleCoreDoubleClick}
        title={view.status_text}
        type="button"
      >
        <CompatibilityFairyCanvas
          dragging={dragging}
          gaze={gaze}
          hovered={hovered}
          listening={inputOpen}
          reducedMotion={reducedMotion}
          sleeping={view.density === "quiet"}
          speaking={projection.speaking}
          workState={view.work_state}
        />
      </button>
    </main>
  );
}

async function persistMovedPosition(
  windowPort: PresenceWindowPort,
  storage: StorageLike,
  settingsRef: { current: PresenceSettings },
  setSettings: (settings: PresenceSettings) => void,
  position: { x: number; y: number },
): Promise<void> {
  try {
    const [monitors, size] = await Promise.all([windowPort.monitors(), windowPort.size()]);
    const monitor = monitorForPosition(monitors, position, size);
    if (monitor === null) return;
    const snapped = snapToMonitorEdges(position, size, monitor);
    if (snapped.x !== position.x || snapped.y !== position.y) {
      await windowPort.setPosition(snapped);
    }
    const next = rememberMonitorPosition(settingsRef.current, monitor, snapped, size);
    settingsRef.current = next;
    savePresenceSettings(storage, next);
    setSettings(next);
  } catch {
    // A monitor can disappear while a native move event is in flight.
  }
}

async function resetPosition(windowPort: PresenceWindowPort, settings: PresenceSettings) {
  const [monitors, size] = await Promise.all([windowPort.monitors(), windowPort.size()]);
  if (monitors.length === 0) return;
  const restored = restoreMonitorPosition({ ...settings, last_monitor_id: null, positions: {} }, monitors, size);
  await windowPort.setPosition(restored.position);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
