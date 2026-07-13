import { useCallback, useEffect, useRef, useState } from "react";

import type { DesktopPreferences } from "../../settings/client";
import type { PresenceInteractionSnapshot } from "../domain/interaction";
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
import {
  createPresenceInteractionSource,
  type PresenceInteractionSource,
} from "../transport/interactionEvents";
import { presenceInputGate } from "./inputGate";
import { PresencePanel } from "./PresencePanel";
import "../presence.css";
import "./presence-input.css";

interface PresenceInputAppProps {
  channel?: PresenceChannel;
  host?: PetHost;
  interactionSource?: PresenceInteractionSource;
  now?: () => number;
  storage?: StorageLike;
}

const MAX_DISMISSED_NOTICES = 128;

export function PresenceInputApp({
  channel: suppliedChannel,
  host: suppliedHost,
  interactionSource: suppliedInteractionSource,
  now = Date.now,
  storage = window.localStorage,
}: PresenceInputAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [host] = useState(() => suppliedHost ?? createDefaultPetHost());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
  );
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
  const [manualInputOpen, setManualInputOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);
  const [hoverSuppressed, setHoverSuppressed] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const presentationQueue = useRef(Promise.resolve());
  const appliedFocusRequest = useRef(0);

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
      setHoverSuppressed(false);
      setManualInputOpen(true);
      setFocusRequest((value) => value + 1);
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

  useEffect(() => {
    if (interaction?.phase === "idle" || interaction?.phase === "aware") {
      setHoverSuppressed(false);
    }
  }, [interaction?.phase]);

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
  const hoverGate = presenceInputGate(interaction, hoverSuppressed);
  const cardOpen = menuOpen || reply !== null || view.notice !== null;
  const inputOpen = manualInputOpen || hoverGate.window_visible;
  const layout = cardOpen
    ? "expanded"
    : inputOpen
      ? "compact"
      : "hidden";
  const contentVisible = cardOpen || manualInputOpen || hoverGate.content_visible;
  const surfaceInteractive = cardOpen || manualInputOpen || hoverGate.interactive;

  useEffect(() => {
    presentationQueue.current = presentationQueue.current
      .catch(() => undefined)
      .then(async () => {
        await host.setInputLayout(layout);
        await host.setInputInteractive(layout !== "hidden" && surfaceInteractive);
        if (
          layout !== "hidden" &&
          surfaceInteractive &&
          focusRequest > appliedFocusRequest.current
        ) {
          appliedFocusRequest.current = focusRequest;
          await host.requestInputFocus();
        }
      })
      .catch(() => undefined);
  }, [focusRequest, host, layout, surfaceInteractive]);

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

  function setInputOpen(open: boolean) {
    setManualInputOpen(open);
    setHoverSuppressed(!open);
    if (!open && document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  }

  function dismissInput() {
    setInputOpen(false);
    setMenuOpen(false);
  }

  return (
    <main
      className="presence-input-window"
      data-content-visible={String(contentVisible)}
      data-interactive={String(surfaceInteractive)}
      data-layout={layout}
      data-reduced-motion={String(
        interaction?.reduced_motion === true || preferences?.reduced_motion === true
      )}
      data-testid="presence-input-surface"
      onContextMenu={(event) => {
        event.preventDefault();
        setManualInputOpen(false);
        setHoverSuppressed(true);
        setMenuOpen(true);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        dismissInput();
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
          requestInputFocus: () => void host.requestInputFocus(),
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
        focusRequest={focusRequest}
        inputOpen={inputOpen}
        interactive={surfaceInteractive}
        menuOpen={menuOpen}
        muted={muted}
        reply={reply}
        view={view}
        visible={contentVisible}
      />
    </main>
  );
}
