import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

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
  createPresenceSubmissionId,
  createPresenceChannel,
  type PresenceChannel,
  type PresenceSubmissionFailure,
  type PresenceSubmissionUpdate,
} from "../transport/presenceChannel";
import {
  createPresenceInteractionSource,
  type PresenceInteractionSource,
} from "../transport/interactionEvents";
import {
  createPresenceRenderSettingsChannel,
  safeRenderSettingsFromPreferences,
  type PresenceRenderSettingsChannel,
} from "../transport/renderSettings";
import { presenceInputGate } from "./inputGate";
import {
  PresencePanel,
  type PresenceSubmissionCard,
} from "./PresencePanel";
import "../presence.css";
import "./presence-input.css";

interface PresenceInputAppProps {
  channel?: PresenceChannel;
  host?: PetHost;
  interactionSource?: PresenceInteractionSource;
  renderSettingsChannel?: PresenceRenderSettingsChannel;
  now?: () => number;
  storage?: StorageLike;
}

const MAX_DISMISSED_NOTICES = 128;
const COMPLETED_REPLY_VISIBLE_MS = 5_000;
const TERMINAL_STATUS_VISIBLE_MS = 2_400;

interface PresenceSubmissionState {
  id: string;
  text: string;
  phase: PresenceSubmissionCard["phase"];
  failure: PresenceSubmissionFailure | null;
}

interface PetDragPointer {
  pointerId: number;
  startX: number;
  startY: number;
  deltaX: number;
  deltaY: number;
  frame: number | null;
  ready: Promise<void>;
  queue: Promise<void>;
}

export function PresenceInputApp({
  channel: suppliedChannel,
  host: suppliedHost,
  interactionSource: suppliedInteractionSource,
  renderSettingsChannel: suppliedRenderSettingsChannel,
  now = Date.now,
  storage = window.localStorage,
}: PresenceInputAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const [host] = useState(() => suppliedHost ?? createDefaultPetHost());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
  );
  const [renderSettingsChannel] = useState(
    () => suppliedRenderSettingsChannel ?? createPresenceRenderSettingsChannel(),
  );
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const preferencesRef = useRef(preferences);
  preferencesRef.current = preferences;
  const [settings, setSettings] = useState<PresenceSettings>(() =>
    loadPresenceSettings(storage),
  );
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [clock, setClock] = useState(() => now());
  const [manualInputOpen, setManualInputOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [submission, setSubmission] = useState<PresenceSubmissionState | null>(null);
  const [replyInteraction, setReplyInteraction] = useState(0);
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);
  const [hoverSuppressed, setHoverSuppressed] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const [moving, setMoving] = useState(false);
  const presentationQueue = useRef(Promise.resolve());
  const appliedFocusRequest = useRef(0);
  const previouslyMuted = useRef(false);
  const legacyPositionMigrationAttempted = useRef(false);
  const dragPointer = useRef<PetDragPointer | null>(null);

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
    const stop = channel.onSubmission((update) => {
      setSubmission((current) => applySubmissionUpdate(current, update));
    });
    return stop;
  }, [channel]);

  useEffect(() => {
    if (suppliedChannel !== undefined) return;
    return () => channel.close();
  }, [channel, suppliedChannel]);

  useEffect(() => {
    let disposed = false;
    let stopPreferences: (() => void) | undefined;
    let stopInput: (() => void) | undefined;
    let stopNewChat: (() => void) | undefined;
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
    void host.onNewChatRequested(() => {
      if (disposed) return;
      channel.requestNewChat();
      setMenuOpen(false);
      setHoverSuppressed(false);
      setManualInputOpen(true);
      setFocusRequest((value) => value + 1);
    }).then((stop) => {
      if (disposed) stop();
      else stopNewChat = stop;
    });
    return () => {
      disposed = true;
      stopPreferences?.();
      stopInput?.();
      stopNewChat?.();
    };
  }, [channel, host]);

  useEffect(() => {
    if (preferences === null || legacyPositionMigrationAttempted.current) return;
    legacyPositionMigrationAttempted.current = true;
    const monitorId = settings.last_monitor_id;
    const position = monitorId === null ? undefined : settings.positions[monitorId];
    if (
      preferences.pet_anchor !== null ||
      !preferences.pet_remember_position ||
      monitorId === null ||
      position === undefined
    ) {
      return;
    }
    void host.updatePreferences({
      expected_revision: preferences.revision,
      pet_anchor: {
        monitor_id: monitorId,
        x_ratio: position.x_ratio,
        y_ratio: position.y_ratio,
      },
    }).then((saved) => {
      preferencesRef.current = saved;
      setPreferences(saved);
    }).catch(() => undefined);
  }, [host, preferences, settings.last_monitor_id, settings.positions]);

  useEffect(() => {
    const muted = preferences?.pet_muted ?? false;
    if (muted && !previouslyMuted.current) channel.requestVoiceStop();
    previouslyMuted.current = muted;
  }, [channel, preferences?.pet_muted]);

  useEffect(() => {
    if (preferences !== null) {
      renderSettingsChannel.publish(safeRenderSettingsFromPreferences(preferences));
    }
    return renderSettingsChannel.onRequest(() => {
      if (preferences !== null) {
        renderSettingsChannel.publish(safeRenderSettingsFromPreferences(preferences));
      }
    });
  }, [preferences, renderSettingsChannel]);

  useEffect(() => {
    if (suppliedRenderSettingsChannel !== undefined) return;
    return () => renderSettingsChannel.close();
  }, [renderSettingsChannel, suppliedRenderSettingsChannel]);

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
    quiet_mode: preferences?.pet_do_not_disturb ?? false,
    dismissed_notice_ids: settings.dismissed_notice_ids,
  });
  const reply = view.reply?.id === closedReplyId ? null : view.reply;
  const submissionCard = toSubmissionCard(submission, view);
  const hoverGate = presenceInputGate(interaction, hoverSuppressed);
  const cardOpen =
    menuOpen || reply !== null || view.notice !== null || submissionCard !== null;
  const inputOpen = manualInputOpen || hoverGate.window_visible;
  const layout = cardOpen
    ? "expanded"
    : inputOpen
      ? "compact"
      : "hidden";
  const contentVisible = cardOpen || manualInputOpen || hoverGate.content_visible;
  const surfaceInteractive = cardOpen || manualInputOpen || hoverGate.interactive;

  useEffect(() => {
    if (reply === null || reply.streaming) return;
    const timer = window.setTimeout(
      () => setClosedReplyId(reply.id),
      COMPLETED_REPLY_VISIBLE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [reply?.id, reply?.streaming, replyInteraction]);

  useEffect(() => {
    if (submission?.phase !== "cancelled") return;
    const timer = window.setTimeout(() => setSubmission(null), TERMINAL_STATUS_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [submission?.id, submission?.phase]);

  useEffect(() => {
    if ((reply !== null && !reply.streaming) || view.notice !== null) {
      setSubmission(null);
    }
  }, [reply?.id, reply?.streaming, view.notice?.id]);

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
      const current = preferencesRef.current;
      if (current === null) return;
      try {
        const saved = await host.updatePreferences({
          expected_revision: current.revision,
          ...patch,
        });
        preferencesRef.current = saved;
        setPreferences(saved);
      } catch {
        const saved = await host.getPreferences();
        preferencesRef.current = saved;
        setPreferences(saved);
      }
    },
    [host],
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

  function sendMessage(text: string) {
    const id = createPresenceSubmissionId();
    setSubmission({ id, text, phase: "sending", failure: null });
    channel.requestChatSend(text, id);
  }

  function cancelTurn() {
    const id = submission?.id ?? createPresenceSubmissionId();
    setSubmission((current) => ({
      id,
      text: current?.text ?? "",
      phase: "cancelling",
      failure: null,
    }));
    channel.requestChatCancel(id);
  }

  function retrySubmission() {
    if (submission === null || submission.text === "") return;
    sendMessage(submission.text);
  }

  function movePointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!surfaceInteractive || dragPointer.current !== null) return;
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.currentTarget.setPointerCapture === "function") {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    const ready = host.beginGroupDrag();
    dragPointer.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      deltaX: 0,
      deltaY: 0,
      frame: null,
      ready,
      queue: ready,
    };
    setMoving(true);
  }

  function movePointerMove(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragPointer.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    drag.deltaX = event.clientX - drag.startX;
    drag.deltaY = event.clientY - drag.startY;
    if (drag.frame !== null) return;
    drag.frame = window.requestAnimationFrame(() => {
      const current = dragPointer.current;
      if (current === null || current.pointerId !== drag.pointerId) return;
      current.frame = null;
      current.queue = current.queue.then(() =>
        host.moveGroupDrag(current.deltaX, current.deltaY)
      ).catch(() => undefined);
    });
  }

  function movePointerUp(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragPointer.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    if (
      typeof event.currentTarget.hasPointerCapture === "function" &&
      event.currentTarget.hasPointerCapture(event.pointerId)
    ) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (drag.frame !== null) window.cancelAnimationFrame(drag.frame);
    dragPointer.current = null;
    setMoving(false);
    const currentPreferences = preferencesRef.current;
    void drag.ready
      .then(async () => {
        await drag.queue.catch(() => undefined);
        try {
          await host.moveGroupDrag(drag.deltaX, drag.deltaY);
        } finally {
          return currentPreferences === null
            ? host.getPreferences()
            : host.endGroupDrag(currentPreferences.revision);
        }
      })
      .then((saved) => {
        preferencesRef.current = saved;
        setPreferences(saved);
      })
      .catch(async () => {
        const saved = await host.getPreferences().catch(() => null);
        if (saved !== null) {
          preferencesRef.current = saved;
          setPreferences(saved);
        }
      });
  }

  function resetPosition() {
    const current = preferencesRef.current;
    if (current === null) return;
    setMenuOpen(false);
    void host.resetPosition(current.revision).then((saved) => {
      preferencesRef.current = saved;
      setPreferences(saved);
    }).catch(async () => {
      const saved = await host.getPreferences().catch(() => null);
      if (saved !== null) {
        preferencesRef.current = saved;
        setPreferences(saved);
      }
    });
  }

  return (
    <main
      className="presence-input-window"
      data-content-visible={String(contentVisible)}
      data-interactive={String(surfaceInteractive)}
      data-layout={layout}
      data-moving={String(moving)}
      data-reduced-motion={String(
        interaction?.reduced_motion === true || preferences?.reduced_motion === true
      )}
      data-testid="presence-input-surface"
      onPointerDown={() => setReplyInteraction((value) => value + 1)}
      onPointerEnter={() => setReplyInteraction((value) => value + 1)}
      onWheel={() => setReplyInteraction((value) => value + 1)}
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
          cancelTurn,
          closeReply: (replyId) => {
            setClosedReplyId(replyId);
            setSubmission(null);
          },
          closeSubmission: () => setSubmission(null),
          dismissNotice,
          exit: () => void host.exit(),
          movePointerDown,
          movePointerMove,
          movePointerUp,
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
          resetPosition,
          retrySubmission,
          send: sendMessage,
          setInputOpen,
          setMenuOpen,
          showMoveGrip: () => {
            setMenuOpen(false);
            setHoverSuppressed(false);
            setManualInputOpen(true);
          },
          toggleAlwaysOnTop: () =>
            void updatePetPreferences({ pet_always_on_top: !alwaysOnTop }),
          toggleAutoPlay: () =>
            void updatePetPreferences({ voice_auto_play_pet: !autoPlay }),
          toggleMuted: () => {
            if (!muted) channel.requestVoiceStop();
            void updatePetPreferences({ pet_muted: !muted });
          },
          stopVoice: () => channel.requestVoiceStop(),
        }}
        alwaysOnTop={alwaysOnTop}
        autoPlay={autoPlay}
        focusRequest={focusRequest}
        inputOpen={inputOpen}
        interactive={surfaceInteractive}
        menuOpen={menuOpen}
        moving={moving}
        muted={muted}
        reply={reply}
        submission={submissionCard}
        view={view}
        visible={contentVisible}
      />
    </main>
  );
}

function applySubmissionUpdate(
  current: PresenceSubmissionState | null,
  update: PresenceSubmissionUpdate,
): PresenceSubmissionState | null {
  if (current === null || current.id !== update.submission_id) return current;
  return {
    ...current,
    phase: update.status,
    failure: update.failure,
  };
}

function toSubmissionCard(
  submission: PresenceSubmissionState | null,
  view: { status_text: string; work_state: PresenceProjectionState["work_state"] },
): PresenceSubmissionCard | null {
  if (submission === null) return null;
  switch (submission.phase) {
    case "sending":
      return card(submission, "Sending to Fairy", "Starting a private scratch chat", true);
    case "accepted":
      return card(
        submission,
        ["analyzing", "tool", "streaming"].includes(view.work_state)
          ? view.status_text
          : "Fairy is working",
        "You can continue in the main window",
        true,
      );
    case "cancelling":
      return card(submission, "Stopping", "Waiting for the active turn to stop", false);
    case "cancelled":
      return card(submission, "Stopped", "The active turn was cancelled", false);
    case "failed":
      return {
        ...card(
          submission,
          failureTitle(submission.failure),
          "Your message was not started",
          false,
        ),
        canRetry: submission.text !== "",
      };
  }
}

function card(
  submission: PresenceSubmissionState,
  title: string,
  detail: string,
  canCancel: boolean,
): PresenceSubmissionCard {
  return {
    id: submission.id,
    phase: submission.phase,
    title,
    detail,
    canCancel,
    canRetry: false,
  };
}

function failureTitle(failure: PresenceSubmissionFailure | null): string {
  if (failure === "offline") return "Fairy is offline";
  if (failure === "busy") return "Fairy is already working";
  return "Message could not be sent";
}
