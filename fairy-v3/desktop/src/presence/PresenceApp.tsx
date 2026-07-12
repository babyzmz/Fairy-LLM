import {
  Check,
  LogOut,
  MessageSquarePlus,
  MonitorUp,
  Pin,
  PinOff,
  RotateCcw,
  Send,
  Settings,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { type DesktopPreferences } from "../settings/client";
import { createPresenceChannel, type PresenceChannel } from "./channel";
import { FairyCanvas } from "./FairyCanvas";
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
} from "./persistence";
import { createDefaultPetHost, type PetHost, type PetPreferencePatch } from "./petHost";
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
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [clock, setClock] = useState(() => now());
  const [hovered, setHovered] = useState(false);
  const [inputOpen, setInputOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [gaze, setGaze] = useState({ x: 0, y: 0 });
  const composing = useRef(false);
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
  const expanded = inputOpen || menuOpen || reply !== null || view.notice !== null;

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

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (value.length === 0 || composing.current || submitting) return;
    setSubmitting(true);
    channel.requestChatSend(value);
    setDraft("");
    setInputOpen(false);
    window.setTimeout(() => setSubmitting(false), 300);
  }

  function handleCoreClick() {
    if (dragged.current) {
      dragged.current = false;
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
  const autoPlay = preferences?.voice_auto_play_pet ?? true;
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
      <section className="presence-panel" data-visible={String(expanded)}>
        {!menuOpen && view.notice !== null ? (
          <aside className={`presence-card notice ${view.notice.tone}`} role="alert">
            <div>
              <strong>{view.status_text}</strong>
              <span>{view.notice.text}</span>
            </div>
            <button aria-label="Dismiss notice" onClick={dismissNotice} title="Dismiss" type="button">
              <X size={14} />
            </button>
            {view.work_state === "awaiting_confirmation" ? (
              <button className="presence-card-action" onClick={() => channel.requestWorkspaceOpen()} type="button">
                Review in Fairy
              </button>
            ) : null}
          </aside>
        ) : null}

        {!menuOpen && reply !== null ? (
          <aside className="presence-card reply" role="status">
            <div>
              <strong>{reply.streaming ? "Fairy is replying" : "Fairy"}</strong>
              <span>{reply.text}</span>
            </div>
            <button aria-label="Close reply" onClick={() => setClosedReplyId(reply.id)} title="Close" type="button">
              <X size={14} />
            </button>
          </aside>
        ) : null}

        {inputOpen ? (
          <form className="presence-input" onSubmit={submit}>
            <textarea
              aria-label="Quick message to Fairy"
              autoFocus
              maxLength={4_000}
              onChange={(event) => setDraft(event.target.value)}
              onCompositionEnd={() => {
                composing.current = false;
              }}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !composing.current) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Message Fairy"
              rows={3}
              value={draft}
            />
            <button aria-label="Send quick message" disabled={draft.trim() === "" || submitting} title="Send" type="submit">
              <Send size={15} />
            </button>
          </form>
        ) : null}

        {menuOpen ? (
          <div aria-label="Fairy menu" className="presence-menu" role="menu">
            <MenuButton icon={<MessageSquarePlus size={15} />} label="New chat" onClick={() => {
              channel.requestNewChat();
              setMenuOpen(false);
              setInputOpen(true);
            }} />
            <MenuToggle checked={autoPlay} icon={<Volume2 size={15} />} label="Auto-play replies" onClick={() => void updatePetPreferences({ voice_auto_play_pet: !autoPlay })} />
            <MenuToggle checked={muted} icon={muted ? <VolumeX size={15} /> : <Volume2 size={15} />} label="Mute" onClick={() => {
              if (!muted) channel.requestVoiceStop();
              void updatePetPreferences({ pet_muted: !muted });
            }} />
            <MenuToggle checked={alwaysOnTop} icon={alwaysOnTop ? <Pin size={15} /> : <PinOff size={15} />} label="Always on top" onClick={() => void updatePetPreferences({ pet_always_on_top: !alwaysOnTop })} />
            <MenuButton icon={<MonitorUp size={15} />} label="Open Fairy" onClick={() => channel.requestWorkspaceOpen()} />
            <MenuButton icon={<Settings size={15} />} label="Settings" onClick={() => void host.openSettings()} />
            <MenuButton icon={<RotateCcw size={15} />} label="Reset position" onClick={() => void resetPosition(windowPort, settingsRef.current)} />
            <MenuButton danger icon={<LogOut size={15} />} label="Exit Fairy" onClick={() => void host.exit()} />
          </div>
        ) : null}
      </section>

      <button
        aria-label="Fairy companion"
        className="presence-core-button"
        onClick={handleCoreClick}
        onDoubleClick={handleCoreDoubleClick}
        title={view.status_text}
        type="button"
      >
        <FairyCanvas
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

function MenuButton({
  danger = false,
  icon,
  label,
  onClick,
}: {
  danger?: boolean;
  icon: ReactNode;
  label: string;
  onClick(): void;
}) {
  return (
    <button className={danger ? "danger" : undefined} onClick={onClick} role="menuitem" type="button">
      {icon}<span>{label}</span>
    </button>
  );
}

function MenuToggle({
  checked,
  icon,
  label,
  onClick,
}: {
  checked: boolean;
  icon: ReactNode;
  label: string;
  onClick(): void;
}) {
  return (
    <button aria-checked={checked} onClick={onClick} role="menuitemcheckbox" type="button">
      {icon}<span>{label}</span>{checked ? <Check className="menu-check" size={14} /> : null}
    </button>
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
