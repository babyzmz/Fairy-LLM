import { Accessibility, Minus, Plus, Volume2, VolumeX, X } from "lucide-react";
import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { createPresenceChannel, type PresenceChannel } from "./channel";
import {
  createDefaultPresenceWindowPort,
  defaultPresenceSettings,
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
} from "./persistence";
import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "./projection";
import "./presence.css";

interface PresenceAppProps {
  channel?: PresenceChannel;
  storage?: StorageLike;
  windowPort?: PresenceWindowPort;
  now?: () => number;
}

const MAX_DISMISSED_NOTICES = 128;

export function PresenceApp({
  channel: suppliedChannel,
  storage = window.localStorage,
  windowPort: suppliedWindowPort,
  now = Date.now,
}: PresenceAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [windowPort] = useState(
    () => suppliedWindowPort ?? createDefaultPresenceWindowPort(),
  );
  const [settings, setSettings] = useState<PresenceSettings>(() =>
    loadPresenceSettings(storage),
  );
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [hovered, setHovered] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [clock, setClock] = useState(() => now());
  const dragOrigin = useRef<{ x: number; y: number } | null>(null);
  const dragging = useRef(false);

  const updateSettings = useCallback(
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
      setClosedReplyId((current) =>
        current === next.reply?.id ? current : null,
      );
      setClock(now());
    });
    channel.requestProjection();
    return () => {
      stop();
    };
  }, [channel, now]);

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
        const [monitors, size] = await Promise.all([
          windowPort.monitors(),
          windowPort.size(),
        ]);
        if (disposed || monitors.length === 0) return;
        const restored = restoreMonitorPosition(settingsRef.current, monitors, size);
        await windowPort.setPosition(restored.position);
        stopMoved = await windowPort.onMoved((position) => {
          void persistMovedPosition(windowPort, storage, settingsRef, setSettings, position);
        });
      } catch {
        // Browser previews and temporarily unavailable monitor APIs remain usable.
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
  const reducedMotion = resolveReducedMotion(
    settings.reduced_motion_override,
    systemReducedMotion,
  );
  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: settings.quiet_mode,
    dismissed_notice_ids: settings.dismissed_notice_ids,
  });
  const reply = view.reply?.id === closedReplyId ? null : view.reply;

  function handlePointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0) return;
    dragOrigin.current = { x: event.clientX, y: event.clientY };
    dragging.current = false;
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLElement>) {
    const origin = dragOrigin.current;
    if (origin === null || dragging.current) return;
    if (Math.hypot(event.clientX - origin.x, event.clientY - origin.y) < 5) return;
    dragging.current = true;
    void windowPort.startDragging();
  }

  function finishPointerGesture() {
    dragOrigin.current = null;
    dragging.current = false;
  }

  function dismissNotice() {
    const notice = view.notice;
    if (notice === null) return;
    updateSettings((current) => ({
      ...current,
      dismissed_notice_ids: [
        ...current.dismissed_notice_ids.filter((id) => id !== notice.id),
        notice.id,
      ].slice(-MAX_DISMISSED_NOTICES),
    }));
  }

  function changeScale(delta: number) {
    updateSettings((current) => ({
      ...current,
      scale: Math.round(Math.min(1.5, Math.max(0.75, current.scale + delta)) * 100) / 100,
    }));
  }

  return (
    <main
      className="presence-window"
      data-activity={view.activity}
      data-density={view.density}
      data-quiet={String(settings.quiet_mode)}
      data-reduced-motion={String(reducedMotion)}
      data-testid="presence-surface"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setHovered(false);
      }}
      onFocus={() => setHovered(true)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onPointerCancel={finishPointerGesture}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerGesture}
      style={{ "--presence-scale": String(settings.scale) } as CSSProperties}
    >
      <div className="presence-hud" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>

      {view.notice !== null ? (
        <aside className={`presence-notice ${view.notice.tone}`} role="alert">
          <span>{view.notice.text}</span>
          <button
            aria-label="Dismiss notice"
            className="presence-icon-button"
            onClick={dismissNotice}
            title="Dismiss notice"
            type="button"
          >
            <X aria-hidden="true" size={14} />
          </button>
        </aside>
      ) : null}

      {reply !== null ? (
        <aside className="presence-reply" role="status">
          <span>{reply.text}</span>
          <button
            aria-label="Close reply"
            className="presence-icon-button"
            onClick={() => setClosedReplyId(reply.id)}
            title="Close reply"
            type="button"
          >
            <X aria-hidden="true" size={14} />
          </button>
        </aside>
      ) : null}

      <button
        aria-label="Show or hide Fairy workspace"
        className="presence-avatar-button"
        onClick={() => channel.requestWorkspaceToggle()}
        title="Fairy workspace"
        type="button"
      >
        <span className="presence-ring" aria-hidden="true" />
        <img
          alt="Fairy"
          draggable={false}
          height="96"
          src="/presence/fairy-blue-ring.webp"
          width="96"
        />
      </button>
      <div
        aria-hidden={!hovered}
        className="presence-toolbar"
        data-visible={String(hovered)}
        role="toolbar"
        aria-label="Presence controls"
      >
          <button
            aria-label={settings.quiet_mode ? "Disable quiet mode" : "Enable quiet mode"}
            aria-pressed={settings.quiet_mode}
            className="presence-icon-button"
            onClick={() =>
              updateSettings((current) => ({
                ...current,
                quiet_mode: !current.quiet_mode,
              }))
            }
            title={settings.quiet_mode ? "Disable quiet mode" : "Enable quiet mode"}
            tabIndex={hovered ? 0 : -1}
            type="button"
          >
            {settings.quiet_mode ? (
              <VolumeX aria-hidden="true" size={15} />
            ) : (
              <Volume2 aria-hidden="true" size={15} />
            )}
          </button>
          <button
            aria-label="Decrease Presence scale"
            className="presence-icon-button"
            disabled={settings.scale <= 0.75}
            onClick={() => changeScale(-0.1)}
            title="Decrease Presence scale"
            tabIndex={hovered ? 0 : -1}
            type="button"
          >
            <Minus aria-hidden="true" size={15} />
          </button>
          <button
            aria-label="Increase Presence scale"
            className="presence-icon-button"
            disabled={settings.scale >= 1.5}
            onClick={() => changeScale(0.1)}
            title="Increase Presence scale"
            tabIndex={hovered ? 0 : -1}
            type="button"
          >
            <Plus aria-hidden="true" size={15} />
          </button>
          <button
            aria-label={`Motion preference: ${settings.reduced_motion_override}`}
            className="presence-icon-button"
            onClick={() =>
              updateSettings((current) => ({
                ...current,
                reduced_motion_override:
                  current.reduced_motion_override === "system"
                    ? "reduce"
                    : current.reduced_motion_override === "reduce"
                      ? "full"
                      : "system",
              }))
            }
            tabIndex={hovered ? 0 : -1}
            title={`Motion preference: ${settings.reduced_motion_override}`}
            type="button"
          >
            <Accessibility aria-hidden="true" size={15} />
          </button>
        </div>
      <div className="presence-status" aria-live="polite">
        <strong>{view.status_text}</strong>
        <span>{view.density === "busy" ? "High activity" : "Fairy Presence"}</span>
      </div>
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
    const [monitors, size] = await Promise.all([
      windowPort.monitors(),
      windowPort.size(),
    ]);
    const monitor = monitorForPosition(monitors, position, size);
    if (monitor === null) return;
    const snapped = snapToMonitorEdges(position, size, monitor);
    if (snapped.x !== position.x || snapped.y !== position.y) {
      await windowPort.setPosition(snapped);
    }
    const next = rememberMonitorPosition(
      settingsRef.current,
      monitor,
      snapped,
      size,
    );
    settingsRef.current = next;
    savePresenceSettings(storage, next);
    setSettings(next);
  } catch {
    // A monitor can disappear while a native move event is in flight.
  }
}
