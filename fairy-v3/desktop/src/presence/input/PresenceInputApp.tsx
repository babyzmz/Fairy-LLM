import { useCallback, useEffect, useRef, useState } from "react";

import type { DesktopPreferences } from "../../settings/client";
import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "../domain/projection";
import {
  createDefaultPetHost,
  type PetHost,
  type PetPreferencePatch,
} from "../host/petHost";
import {
  loadPresenceSettings,
  type PresenceSettings,
  type StorageLike,
  savePresenceSettings,
} from "../host/persistence";
import {
  createPresenceChannel,
  type PresenceChannel,
} from "../transport/presenceChannel";
import { PresencePanel } from "./PresencePanel";
import "../presence.css";
import "./presence-input.css";

interface PresenceInputAppProps {
  channel?: PresenceChannel;
  host?: PetHost;
  now?: () => number;
  storage?: StorageLike;
}

const MAX_DISMISSED_NOTICES = 128;

export function PresenceInputApp({
  channel: suppliedChannel,
  host: suppliedHost,
  now = Date.now,
  storage = window.localStorage,
}: PresenceInputAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [host] = useState(() => suppliedHost ?? createDefaultPetHost());
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [settings, setSettings] = useState<PresenceSettings>(() =>
    loadPresenceSettings(storage),
  );
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [clock, setClock] = useState(() => now());
  const [inputOpen, setInputOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);

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
    if (suppliedChannel !== undefined) return;
    return () => channel.close();
  }, [channel, suppliedChannel]);

  useEffect(() => {
    let disposed = false;
    let stopPreferences: (() => void) | undefined;
    let stopInput: (() => void) | undefined;
    void host.getPreferences().then((value) => {
      if (!disposed) setPreferences(value);
    }).catch(() => undefined);
    void host.onPreferences((value) => {
      if (!disposed) setPreferences(value);
    }).then((stop) => {
      if (disposed) stop();
      else stopPreferences = stop;
    });
    void host.onInputRequested(() => {
      if (disposed) return;
      setMenuOpen(false);
      setInputOpen(true);
    }).then((stop) => {
      if (disposed) stop();
      else stopInput = stop;
    });
    return () => {
      disposed = true;
      stopPreferences?.();
      stopInput?.();
    };
  }, [host]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(now()), 30_000);
    return () => window.clearInterval(timer);
  }, [now]);

  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: false,
    dismissed_notice_ids: settings.dismissed_notice_ids,
  });
  const reply = view.reply?.id === closedReplyId ? null : view.reply;
  const layout = menuOpen || reply !== null || view.notice !== null
    ? "expanded"
    : inputOpen
      ? "compact"
      : "hidden";

  useEffect(() => {
    void host.setInputLayout(layout);
  }, [host, layout]);

  const updatePetPreferences = useCallback(
    async (patch: Omit<PetPreferencePatch, "expected_revision">) => {
      if (preferences === null) return;
      try {
        setPreferences(await host.updatePreferences({
          expected_revision: preferences.revision,
          ...patch,
        }));
      } catch {
        setPreferences(await host.getPreferences());
      }
    },
    [host, preferences],
  );

  function dismissNotice() {
    if (view.notice === null) return;
    const next = {
      ...settingsRef.current,
      dismissed_notice_ids: [
        ...settingsRef.current.dismissed_notice_ids.filter(
          (id) => id !== view.notice?.id,
        ),
        view.notice.id,
      ].slice(-MAX_DISMISSED_NOTICES),
    };
    settingsRef.current = next;
    savePresenceSettings(storage, next);
    setSettings(next);
  }

  const muted = preferences?.pet_muted ?? false;
  const autoPlay = preferences?.voice_auto_play_pet ?? true;
  const alwaysOnTop = preferences?.pet_always_on_top ?? true;

  return (
    <main
      className="presence-input-window"
      data-layout={layout}
      data-testid="presence-input-surface"
      onContextMenu={(event) => {
        event.preventDefault();
        setInputOpen(false);
        setMenuOpen(true);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        setInputOpen(false);
        setMenuOpen(false);
      }}
    >
      <PresencePanel
        actions={{
          closeReply: setClosedReplyId,
          dismissNotice,
          exit: () => void host.exit(),
          newChat: () => channel.requestNewChat(),
          openMain: () => {
            channel.requestWorkspaceOpen();
            void host.openMain().catch(() => undefined);
          },
          openReview: () => {
            channel.requestWorkspaceOpen();
            void host.openMain().catch(() => undefined);
          },
          openSettings: () => void host.openSettings(),
          resetPosition: () => undefined,
          send: (text) => channel.requestChatSend(text),
          setInputOpen,
          setMenuOpen,
          toggleAlwaysOnTop: () =>
            void updatePetPreferences({ pet_always_on_top: !alwaysOnTop }),
          toggleAutoPlay: () =>
            void updatePetPreferences({ voice_auto_play_pet: !autoPlay }),
          toggleMuted: () => {
            if (!muted) channel.requestVoiceStop();
            void updatePetPreferences({ pet_muted: !muted });
          },
        }}
        alwaysOnTop={alwaysOnTop}
        autoPlay={autoPlay}
        inputOpen={inputOpen}
        menuOpen={menuOpen}
        muted={muted}
        reply={reply}
        view={view}
        visible={layout !== "hidden"}
      />
    </main>
  );
}
