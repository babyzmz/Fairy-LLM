import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { isTauri } from "@tauri-apps/api/core";

import {
  type DesktopPreferences,
  type VoiceWorkerHealth,
  voiceStatusFromLifecycle,
} from "../../settings/client";
import type { PresenceInteractionSnapshot } from "../domain/interaction";
import {
  advanceFairyMotionSnapshot,
  DEFAULT_FAIRY_MOTION_SNAPSHOT,
  type FairyMotionSnapshot,
  type FairySurface,
} from "../domain/motionState";
import {
  derivePresenceView,
  PresenceProjection,
  type PresenceProjectionState,
} from "../domain/projection";
import {
  createDefaultPetHost,
  type PetHost,
  type PetInputPresentationApply,
  type PetInputLayout,
  type PetPreferencePatch,
} from "../host/petHost";
import {
  loadPresenceSettings,
  type PresenceSettings,
  type StorageLike,
  savePresenceSettings,
} from "../host/persistence";
import { usePresenceAccessibilityPreferences } from "../host/usePresenceAccessibility";
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
  createPresenceInputPresentationChannel,
  DEFAULT_INPUT_PRESENTATION,
  type PresenceInputPresentation,
  type PresenceInputPresentationChannel,
} from "../transport/inputPresentation";
import {
  createPresenceRenderSettingsChannel,
  safeRenderSettingsFromPreferences,
  type PresenceRenderSettingsChannel,
} from "../transport/renderSettings";
import { useDeferredChannelClose } from "../transport/useDeferredChannelClose";
import { presenceInputGate } from "./inputGate";
import {
  INITIAL_PRESENCE_INPUT_INTENT,
  reducePresenceInputIntent,
  type PresenceInputIntentAction,
} from "./inputIntent";
import {
  PresencePanel,
  PRESENCE_COMPACT_INPUT_MIN_HEIGHT,
  PRESENCE_COMPACT_INPUT_MIN_WIDTH,
  type PresenceSubmissionCard,
} from "./PresencePanel";
import "../presence.css";
import "./presence-input.css";

interface PresenceInputAppProps {
  channel?: PresenceChannel;
  host?: PetHost;
  interactionSource?: PresenceInteractionSource;
  inputPresentationChannel?: PresenceInputPresentationChannel;
  renderSettingsChannel?: PresenceRenderSettingsChannel;
  now?: () => number;
  storage?: StorageLike;
}

const MAX_DISMISSED_NOTICES = 128;
const COMPLETED_REPLY_VISIBLE_MS = 5_000;
const TERMINAL_STATUS_VISIBLE_MS = 2_400;
const PET_DRAG_CLICK_SUPPRESSION_MS = 450;
const PET_DRAG_HOLD_MS = 320;
const PET_DRAG_DISTANCE_PX = 6;
const PRESENCE_SURFACE_EXIT_MS = 240;
const PRESENCE_SURFACE_REDUCED_EXIT_MS = 80;
const MENU_FOCUS_LOSS_MS = 600;
const INPUT_PRESENTATION_RETRY_BASE_MS = 120;
const INPUT_PRESENTATION_RETRY_MAX_MS = 2_000;

interface PresenceSubmissionState {
  id: string;
  text: string;
  phase: PresenceSubmissionCard["phase"];
  failure: PresenceSubmissionFailure | null;
}

interface CorePointerPress {
  pointerId: number;
  screenX: number;
  screenY: number;
  startedAt: number;
  moved: boolean;
}

export function PresenceInputApp({
  channel: suppliedChannel,
  host: suppliedHost,
  interactionSource: suppliedInteractionSource,
  inputPresentationChannel: suppliedInputPresentationChannel,
  renderSettingsChannel: suppliedRenderSettingsChannel,
  now = Date.now,
  storage = window.localStorage,
}: PresenceInputAppProps) {
  const [channel] = useState(() => suppliedChannel ?? createPresenceChannel());
  const accessibility = usePresenceAccessibilityPreferences();
  const [host] = useState(() => suppliedHost ?? createDefaultPetHost());
  const [nativePointerGestures] = useState(() => isTauri());
  const [interactionSource] = useState(
    () => suppliedInteractionSource ?? createPresenceInteractionSource(),
  );
  const [renderSettingsChannel] = useState(
    () => suppliedRenderSettingsChannel ?? createPresenceRenderSettingsChannel(),
  );
  const [inputPresentationChannel] = useState(
    () => suppliedInputPresentationChannel ?? createPresenceInputPresentationChannel(),
  );
  const [inputPresentationSessionId, setInputPresentationSessionId] = useState<
    number | null
  >(null);
  const [inputPresentationGeneration, setInputPresentationGeneration] = useState(0);
  const latestInputPresentation = useRef<PresenceInputPresentation>(
    DEFAULT_INPUT_PRESENTATION,
  );
  const [projection, setProjection] = useState<PresenceProjectionState>(() =>
    PresenceProjection.initial(),
  );
  const motionSnapshot = useRef<FairyMotionSnapshot>(
    DEFAULT_FAIRY_MOTION_SNAPSHOT,
  );
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<VoiceWorkerHealth["status"]>("idle");
  const voiceSequence = useRef(-1);
  const preferencesRef = useRef(preferences);
  preferencesRef.current = preferences;
  const [settings, setSettings] = useState<PresenceSettings>(() =>
    loadPresenceSettings(storage),
  );
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const [clock, setClock] = useState(() => now());
  const [inputIntent, setInputIntent] = useState(INITIAL_PRESENCE_INPUT_INTENT);
  const inputIntentRef = useRef(inputIntent);
  inputIntentRef.current = inputIntent;
  const transientInputVisible = useRef(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [closedReplyId, setClosedReplyId] = useState<string | null>(null);
  const [closedAmbientId, setClosedAmbientId] = useState<string | null>(null);
  const [submission, setSubmission] = useState<PresenceSubmissionState | null>(null);
  const [replyInteraction, setReplyInteraction] = useState(0);
  const [interaction, setInteraction] = useState<PresenceInteractionSnapshot | null>(null);
  const [interactionReady, setInteractionReady] = useState(false);
  const [focusRequest, setFocusRequest] = useState(0);
  const [compactSize, setCompactSize] = useState(() => ({
    width: PRESENCE_COMPACT_INPUT_MIN_WIDTH,
    height: PRESENCE_COMPACT_INPUT_MIN_HEIGHT,
  }));
  const [expandedContentHeight, setExpandedContentHeight] = useState(72);
  const presentationQueue = useRef(Promise.resolve());
  const presentationRevision = useRef(0);
  const appliedFocusRequest = useRef(0);
  const previouslyMuted = useRef(false);
  const legacyPositionMigrationAttempted = useRef(false);
  const suppressCoreActivation = useRef(false);
  const suppressCoreActivationTimer = useRef<number | null>(null);
  const corePress = useRef<CorePointerPress | null>(null);
  const coreLongPressTimer = useRef<number | null>(null);
  const nativeDragWasActive = useRef(false);
  const menuFocusLossTimer = useRef<number | null>(null);
  const presentationRetryTimer = useRef<number | null>(null);
  const presentationRetryAttempt = useRef(0);
  const inputTransitionRequest = useRef(0);
  const manualCloseTransition = useRef(false);
  const applyInputIntent = useCallback((action: PresenceInputIntentAction) => {
    const next = reducePresenceInputIntent(inputIntentRef.current, action);
    inputIntentRef.current = next;
    setInputIntent(next);
    return next;
  }, []);
  const transitionInputIntent = useCallback((
    open: boolean,
    action: PresenceInputIntentAction,
    requestFocus = false,
  ) => {
    const request = inputTransitionRequest.current + 1;
    inputTransitionRequest.current = request;
    manualCloseTransition.current = !open;
    const previous = inputIntentRef.current;
    applyInputIntent(action);
    if (requestFocus) {
      setFocusRequest((value) => value + 1);
    }
    void host.setInputOpenIntent(open).catch(() => {
      if (request !== inputTransitionRequest.current) return;
      manualCloseTransition.current = false;
      inputIntentRef.current = previous;
      setInputIntent(previous);
    });
  }, [applyInputIntent, host]);
  const scheduleInputPresentationRestart = useCallback(() => {
    if (presentationRetryTimer.current !== null) return;
    const delay = Math.min(
      INPUT_PRESENTATION_RETRY_BASE_MS * (2 ** presentationRetryAttempt.current),
      INPUT_PRESENTATION_RETRY_MAX_MS,
    );
    presentationRetryAttempt.current = Math.min(
      presentationRetryAttempt.current + 1,
      8,
    );
    presentationRetryTimer.current = window.setTimeout(() => {
      presentationRetryTimer.current = null;
      setInputPresentationGeneration((generation) => generation + 1);
    }, delay);
  }, []);
  const manualInputOpen = inputIntent.pinned;
  const hoverSuppressed = inputIntent.hover_blocked_until_exit;
  const updateCompactSize = useCallback((width: number, height: number) => {
    setCompactSize((current) =>
      current.width === width && current.height === height
        ? current
        : { width, height },
    );
  }, []);

  useEffect(
    () => () => {
      if (suppressCoreActivationTimer.current !== null) {
        window.clearTimeout(suppressCoreActivationTimer.current);
      }
      if (coreLongPressTimer.current !== null) {
        window.clearTimeout(coreLongPressTimer.current);
      }
      if (menuFocusLossTimer.current !== null) {
        window.clearTimeout(menuFocusLossTimer.current);
      }
      if (presentationRetryTimer.current !== null) {
        window.clearTimeout(presentationRetryTimer.current);
      }
    },
    [],
  );

  function suppressCoreClickAfterDrag() {
    suppressCoreActivation.current = true;
    if (suppressCoreActivationTimer.current !== null) {
      window.clearTimeout(suppressCoreActivationTimer.current);
    }
    suppressCoreActivationTimer.current = window.setTimeout(() => {
      suppressCoreActivation.current = false;
      suppressCoreActivationTimer.current = null;
    }, PET_DRAG_CLICK_SUPPRESSION_MS);
  }

  useEffect(() => {
    const stop = channel.onProjection((next) => {
      setProjection(next);
      setClosedReplyId((current) => (current === next.reply?.id ? current : null));
      setClosedAmbientId((current) =>
        current === next.ambient_dialogue?.presentation_id ? current : null,
      );
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

  useDeferredChannelClose(channel, suppliedChannel === undefined);

  useEffect(() => {
    let disposed = false;
    presentationRevision.current = 0;
    latestInputPresentation.current = DEFAULT_INPUT_PRESENTATION;
    writeInputPresentationDiagnostics(null, "starting");
    setInputPresentationSessionId(null);
    void host.beginInputPresentationSession().then((commit) => {
      if (!disposed) {
        writeInputPresentationDiagnostics({
          session_id: commit.session_id,
          revision: commit.revision,
        }, "pending");
        setInputPresentationSessionId(commit.session_id);
      }
    }).catch(() => {
      if (!disposed) {
        writeInputPresentationDiagnostics(null, "failed");
        void host.setInputInteractive(false).catch(() => undefined);
        scheduleInputPresentationRestart();
      }
    });
    return () => {
      disposed = true;
    };
  }, [host, inputPresentationGeneration, scheduleInputPresentationRestart]);

  useEffect(() => {
    let disposed = false;
    let stopPreferences: (() => void) | undefined;
    let stopVoice: (() => void) | undefined;
    let stopInput: (() => void) | undefined;
    let stopInputToggle: (() => void) | undefined;
    let stopInputClose: (() => void) | undefined;
    let stopMenu: (() => void) | undefined;
    let stopNewChat: (() => void) | undefined;
    void host.getPreferences().then((value) => {
      if (!disposed) setPreferences(value);
    }).catch(() => undefined);
    void host.getVoiceHealth().then((value) => {
      if (!disposed && value.sequence >= voiceSequence.current) {
        voiceSequence.current = value.sequence;
        setVoiceStatus(value.status);
      }
    }).catch(() => undefined);
    void host.onVoiceLifecycle?.((snapshot) => {
      if (disposed || snapshot.sequence <= voiceSequence.current) return;
      voiceSequence.current = snapshot.sequence;
      setVoiceStatus(voiceStatusFromLifecycle(snapshot));
    }).then((stop) => {
      if (disposed) stop();
      else stopVoice = stop;
    });
    void host.onPreferences((value) => {
      if (!disposed) setPreferences(value);
    }).then((stop) => {
      if (disposed) stop();
      else stopPreferences = stop;
    });
    void host.onInputRequested(() => {
      if (disposed) return;
      setMenuOpen(false);
      transitionInputIntent(true, { type: "open" }, true);
    }).then((stop) => {
      if (disposed) stop();
      else stopInput = stop;
    });
    void host.onInputToggleRequested(() => {
      if (disposed) return;
      const opening = !inputIntentRef.current.pinned && !transientInputVisible.current;
      setMenuOpen(false);
      transitionInputIntent(opening, {
        type: "toggle",
        transient_visible: transientInputVisible.current,
      }, opening);
      if (!opening && document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    }).then((stop) => {
      if (disposed) stop();
      else stopInputToggle = stop;
    });
    void host.onInputCloseRequested(() => {
      if (disposed) return;
      setMenuOpen(false);
      transitionInputIntent(false, { type: "close" });
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    }).then((stop) => {
      if (disposed) stop();
      else stopInputClose = stop;
    });
    void host.onMenuRequested(() => {
      if (disposed) return;
      transitionInputIntent(false, { type: "close" });
      setMenuOpen(true);
    }).then((stop) => {
      if (disposed) stop();
      else stopMenu = stop;
    });
    void host.onNewChatRequested(() => {
      if (disposed) return;
      channel.requestNewChat();
      setMenuOpen(false);
      transitionInputIntent(true, { type: "open" }, true);
    }).then((stop) => {
      if (disposed) stop();
      else stopNewChat = stop;
    });
    return () => {
      disposed = true;
      stopPreferences?.();
      stopVoice?.();
      stopInput?.();
      stopInputToggle?.();
      stopInputClose?.();
      stopMenu?.();
      stopNewChat?.();
    };
  }, [channel, host, transitionInputIntent]);

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

  useDeferredChannelClose(
    renderSettingsChannel,
    suppliedRenderSettingsChannel === undefined,
  );

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

  useEffect(() => {
    if (interaction?.phase === "idle") {
      manualCloseTransition.current = false;
      applyInputIntent({ type: "pointer_exited" });
    }
  }, [applyInputIntent, interaction?.phase]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(now()), 30_000);
    return () => window.clearInterval(timer);
  }, [now]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      transitionInputIntent(false, { type: "close" });
      setMenuOpen(false);
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    };
    window.addEventListener("keydown", handleEscape, true);
    return () => window.removeEventListener("keydown", handleEscape, true);
  }, [transitionInputIntent]);

  useEffect(() => {
    if (!menuOpen) return;
    const cancelClose = () => {
      if (menuFocusLossTimer.current === null) return;
      window.clearTimeout(menuFocusLossTimer.current);
      menuFocusLossTimer.current = null;
    };
    const scheduleClose = () => {
      cancelClose();
      menuFocusLossTimer.current = window.setTimeout(() => {
        menuFocusLossTimer.current = null;
        setMenuOpen(false);
      }, MENU_FOCUS_LOSS_MS);
    };
    window.addEventListener("blur", scheduleClose);
    window.addEventListener("focus", cancelClose);
    document.addEventListener("focusin", cancelClose);
    document.addEventListener("pointerdown", cancelClose, true);
    return () => {
      window.removeEventListener("blur", scheduleClose);
      window.removeEventListener("focus", cancelClose);
      document.removeEventListener("focusin", cancelClose);
      document.removeEventListener("pointerdown", cancelClose, true);
      cancelClose();
    };
  }, [menuOpen]);

  const view = derivePresenceView(projection, {
    now_ms: clock,
    quiet_mode: preferences?.pet_do_not_disturb ?? false,
    dismissed_notice_ids: settings.dismissed_notice_ids,
  });
  const reply = view.reply?.id === closedReplyId ? null : view.reply;
  const ambientDialogue =
    view.ambient_dialogue?.presentation_id === closedAmbientId
      ? null
      : view.ambient_dialogue;
  const submissionCard = toSubmissionCard(submission);
  const menuBlocked =
    view.notice !== null ||
    reply !== null ||
    ambientDialogue !== null ||
    submission !== null ||
    view.speaking ||
    !["idle", "ready"].includes(view.work_state);
  const effectiveMenuOpen = menuOpen && !menuBlocked;
  useEffect(() => {
    if (menuOpen && menuBlocked) setMenuOpen(false);
  }, [menuBlocked, menuOpen]);
  const moving = interaction?.phase === "repositioning";
  const preserveCloseSurface =
    manualCloseTransition.current &&
    (interaction?.phase === "interactive" || interaction?.phase === "returning");
  const hoverGate = presenceInputGate(
    interaction,
    (hoverSuppressed && !preserveCloseSurface) || moving,
  );
  transientInputVisible.current = !manualInputOpen && hoverGate.window_visible;
  const reducedMotion =
    interaction?.reduced_motion === true ||
    preferences?.reduced_motion === true ||
    accessibility.reduced_motion;
  useEffect(() => {
    if (moving) {
      nativeDragWasActive.current = true;
      suppressCoreActivation.current = true;
      applyInputIntent({ type: "drag_started" });
      setMenuOpen(false);
      if (suppressCoreActivationTimer.current !== null) {
        window.clearTimeout(suppressCoreActivationTimer.current);
        suppressCoreActivationTimer.current = null;
      }
      return;
    }
    if (!nativeDragWasActive.current) return;
    nativeDragWasActive.current = false;
    suppressCoreClickAfterDrag();
  }, [applyInputIntent, moving]);
  const nextMotionSnapshot = advanceFairyMotionSnapshot(
    motionSnapshot.current,
    {
      interaction,
      input_window_visible: hoverGate.window_visible,
      input_content_visible: hoverGate.content_visible,
      input_interactive: hoverGate.interactive,
      manual_input_open: manualInputOpen,
      menu_open: effectiveMenuOpen,
      reply,
      ambient_dialogue: ambientDialogue !== null,
      notice_tone: view.notice?.tone ?? null,
      submission_phase: submission?.phase ?? null,
      work_state: view.work_state,
      speaking: view.speaking,
      moving,
      sleeping: view.density === "quiet",
      reduced_motion: reducedMotion,
      do_not_disturb: preferences?.pet_do_not_disturb ?? false,
    },
    now(),
  );
  motionSnapshot.current = nextMotionSnapshot;
  const requestedLayout = layoutForSurface(nextMotionSnapshot.surface);
  const requestedContentVisible = nextMotionSnapshot.content_visible;
  const requestedSurfaceInteractive = nextMotionSnapshot.surface_interactive;
  const [retainedLayout, setRetainedLayout] = useState<PetInputLayout>("hidden");
  useLayoutEffect(() => {
    if (requestedLayout !== "hidden") {
      setRetainedLayout(requestedLayout);
      return;
    }
    if (retainedLayout === "hidden") return;
    const timer = window.setTimeout(
      () => setRetainedLayout("hidden"),
      reducedMotion ? PRESENCE_SURFACE_REDUCED_EXIT_MS : PRESENCE_SURFACE_EXIT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [reducedMotion, requestedLayout, retainedLayout]);
  const layout = requestedLayout === "hidden" ? retainedLayout : requestedLayout;
  const retainingExitSurface =
    requestedLayout === "hidden" && layout !== "hidden";
  const contentVisible = requestedContentVisible;
  const surfaceInteractive = layout !== "hidden" && (
    retainingExitSurface ? false : requestedSurfaceInteractive
  );
  const capsuleVisible =
    layout === "compact" && nextMotionSnapshot.capsule_visible;
  const presentedMotionSnapshot: FairyMotionSnapshot = nextMotionSnapshot;
  const cardOpen = layout === "expanded";
  const inputOpen = nextMotionSnapshot.surface === "input";

  useEffect(() => {
    channel.publishInputState?.(inputOpen, inputOpen && document.hasFocus());
    const publishFocus = () => {
      channel.publishInputState?.(inputOpen, inputOpen && document.hasFocus());
    };
    window.addEventListener("focus", publishFocus);
    window.addEventListener("blur", publishFocus);
    return () => {
      window.removeEventListener("focus", publishFocus);
      window.removeEventListener("blur", publishFocus);
      channel.publishInputState?.(false, false);
    };
  }, [channel, inputOpen]);

  useEffect(
    () => inputPresentationChannel.onRequest(() => {
      if (latestInputPresentation.current.session_id > 0) {
        inputPresentationChannel.publish(latestInputPresentation.current);
      }
    }),
    [inputPresentationChannel],
  );

  useDeferredChannelClose(
    inputPresentationChannel,
    suppliedInputPresentationChannel === undefined,
  );

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
    if (inputPresentationSessionId === null) return;
    const revision = presentationRevision.current + 1;
    presentationRevision.current = revision;
    const presentation: PresenceInputPresentation = {
      schema_version: 5,
      session_id: inputPresentationSessionId,
      sequence: revision,
      layout: layout === "hidden" ? "core" : layout,
      capsule_visible: capsuleVisible,
      capsule_width: compactSize.width,
      capsule_height: compactSize.height,
      motion: presentedMotionSnapshot,
    };
    const requestFocus =
      layout === "compact" &&
      inputOpen &&
      surfaceInteractive &&
      focusRequest > appliedFocusRequest.current;
    const nativePresentation: PetInputPresentationApply = {
      session_id: inputPresentationSessionId,
      revision,
      layout,
      ...(layout === "compact" ? {
        compact_width: compactSize.width,
        compact_height: compactSize.height,
      } : {}),
      ...(layout === "expanded" ? {
        expanded_content_height: expandedContentHeight,
      } : {}),
      interactive: surfaceInteractive,
      request_focus: requestFocus,
    };
    presentationQueue.current = presentationQueue.current
      .catch(() => undefined)
      .then(async () => {
        if (revision !== presentationRevision.current) return;
        const commit = await host.applyInputPresentation(nativePresentation);
        if (
          commit.session_id !== inputPresentationSessionId ||
          commit.revision !== revision
        ) {
          throw new Error("PET_INPUT_PRESENTATION_COMMIT_MISMATCH");
        }
        if (requestFocus) {
          appliedFocusRequest.current = focusRequest;
        }
        if (revision !== presentationRevision.current) return;
        presentationRetryAttempt.current = 0;
        latestInputPresentation.current = presentation;
        writeInputPresentationDiagnostics(commit, "committed");
        inputPresentationChannel.publish(presentation);
      })
      .catch(() => {
        if (revision !== presentationRevision.current) return;
        writeInputPresentationDiagnostics({
          session_id: inputPresentationSessionId,
          revision,
        }, "failed");
        const recoveryRevision = revision + 1;
        presentationRevision.current = recoveryRevision;
        const safeMotion: FairyMotionSnapshot = {
          ...presentedMotionSnapshot,
          revision: presentedMotionSnapshot.revision + 1,
          surface: "core",
          content_visible: false,
          surface_interactive: false,
          capsule_visible: false,
        };
        const safePresentation: PresenceInputPresentation = {
          schema_version: 5,
          session_id: inputPresentationSessionId,
          sequence: recoveryRevision,
          layout: "core",
          capsule_visible: false,
          capsule_width: compactSize.width,
          capsule_height: compactSize.height,
          motion: safeMotion,
        };
        latestInputPresentation.current = safePresentation;
        inputPresentationChannel.publish(safePresentation);
        void host.applyInputPresentation({
          session_id: inputPresentationSessionId,
          revision: recoveryRevision,
          layout: "core",
          interactive: false,
          request_focus: false,
        }).then((commit) => {
          if (
            commit.session_id === inputPresentationSessionId &&
            commit.revision === recoveryRevision
          ) {
            writeInputPresentationDiagnostics(commit, "recovered");
          }
        }).catch(() => host.setInputInteractive(false).catch(() => undefined));
        scheduleInputPresentationRestart();
      });
  }, [
    capsuleVisible,
    compactSize.height,
    compactSize.width,
    expandedContentHeight,
    focusRequest,
    host,
    inputOpen,
    inputPresentationChannel,
    inputPresentationSessionId,
    layout,
    presentedMotionSnapshot.revision,
    scheduleInputPresentationRestart,
    surfaceInteractive,
  ]);

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
  const autoPlay = preferences?.voice_replies_enabled ?? true;
  const alwaysOnTop = preferences?.pet_always_on_top ?? true;

  function setInputOpen(open: boolean) {
    transitionInputIntent(open, { type: open ? "open" : "close" });
    if (!open && document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  }

  function openQuickInput() {
    setMenuOpen(false);
    transitionInputIntent(true, { type: "open" }, true);
  }

  function toggleQuickInput() {
    const opening = !inputIntentRef.current.pinned && !transientInputVisible.current;
    setMenuOpen(false);
    transitionInputIntent(opening, {
      type: "toggle",
      transient_visible: transientInputVisible.current,
    }, opening);
    if (!opening && document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  }

  function activateCore() {
    if (
      view.notice !== null ||
      view.work_state === "awaiting_confirmation" ||
      view.work_state === "error"
    ) {
      channel.requestWorkspaceOpen();
      void host.openMain().catch(() => undefined);
      return;
    }
    toggleQuickInput();
  }

  function captureCorePointer(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    corePress.current = {
      pointerId: event.pointerId,
      screenX: event.screenX,
      screenY: event.screenY,
      startedAt: performance.now(),
      moved: false,
    };
    if (coreLongPressTimer.current !== null) {
      window.clearTimeout(coreLongPressTimer.current);
    }
    coreLongPressTimer.current = window.setTimeout(() => {
      if (corePress.current?.pointerId !== event.pointerId) return;
      suppressCoreActivation.current = true;
      coreLongPressTimer.current = null;
    }, PET_DRAG_HOLD_MS);
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // WebView2 can reject capture while the native surface is being recreated.
    }
  }

  function trackCorePointer(event: ReactPointerEvent<HTMLButtonElement>) {
    const press = corePress.current;
    if (press === null || press.pointerId !== event.pointerId) return;
    const deltaX = event.screenX - press.screenX;
    const deltaY = event.screenY - press.screenY;
    if (Math.hypot(deltaX, deltaY) < PET_DRAG_DISTANCE_PX) return;
    press.moved = true;
    suppressCoreActivation.current = true;
  }

  function releaseCorePointer(event: ReactPointerEvent<HTMLButtonElement>) {
    const press = corePress.current;
    const shouldSuppress = press !== null && press.pointerId === event.pointerId && (
      press.moved || performance.now() - press.startedAt >= PET_DRAG_HOLD_MS
    );
    corePress.current = null;
    if (coreLongPressTimer.current !== null) {
      window.clearTimeout(coreLongPressTimer.current);
      coreLongPressTimer.current = null;
    }
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Pointer capture is also released automatically when the pointer ends.
    }
    if (shouldSuppress) suppressCoreClickAfterDrag();
  }

  function cancelCorePointer(event: ReactPointerEvent<HTMLButtonElement>) {
    releaseCorePointer(event);
  }

  function openContextMenu() {
    if (menuBlocked) return;
    transitionInputIntent(false, { type: "close" });
    setMenuOpen(true);
  }

  function dismissInput() {
    setInputOpen(false);
    setMenuOpen(false);
  }

  function engageQuickInput() {
    if (inputIntentRef.current.pinned) return;
    transitionInputIntent(true, { type: "engage" });
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
      style={{
        "--presence-compact-width": `${compactSize.width}px`,
        "--presence-compact-height": `${compactSize.height}px`,
      } as CSSProperties}
      data-content-visible={String(contentVisible)}
      data-expansion-direction={
        interaction?.placement.expansion_direction ?? "right"
      }
      data-interactive={String(surfaceInteractive)}
      data-interaction-phase={interaction?.phase ?? "idle"}
      data-interaction-ready={String(interactionReady)}
      data-motion-activity={presentedMotionSnapshot.activity}
      data-motion-state={presentedMotionSnapshot.state}
      data-motion-surface={presentedMotionSnapshot.surface}
      data-layout={layout}
      data-menu-open={String(effectiveMenuOpen)}
      data-moving={String(moving)}
      data-reduced-motion={String(reducedMotion)}
      data-reduced-transparency={String(accessibility.reduced_transparency)}
      data-increased-contrast={String(accessibility.increased_contrast)}
      data-testid="presence-input-surface"
      onPointerDown={() => setReplyInteraction((value) => value + 1)}
      onPointerEnter={() => setReplyInteraction((value) => value + 1)}
      onWheel={() => setReplyInteraction((value) => value + 1)}
      onContextMenu={(event) => {
        event.preventDefault();
        if (!nativePointerGestures) openContextMenu();
      }}
    >
      <button
        aria-label="Open Fairy quick input"
        className="presence-core-hit-target"
        onClick={(event) => {
          if (
            nativePointerGestures ||
            event.detail > 1 ||
            suppressCoreActivation.current ||
            moving
          ) return;
          activateCore();
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          if (!nativePointerGestures) openContextMenu();
        }}
        onDoubleClick={() => {
          if (nativePointerGestures || suppressCoreActivation.current || moving) return;
          transitionInputIntent(false, { type: "close" });
          setMenuOpen(false);
          channel.requestWorkspaceOpen();
          void host.openMain().catch(() => undefined);
        }}
        onPointerCancel={nativePointerGestures ? undefined : cancelCorePointer}
        onPointerDown={nativePointerGestures ? undefined : captureCorePointer}
        onPointerMove={nativePointerGestures ? undefined : trackCorePointer}
        onPointerUp={nativePointerGestures ? undefined : releaseCorePointer}
        tabIndex={-1}
        type="button"
      />
      <PresencePanel
        onCompactSizeChange={updateCompactSize}
        onExpandedHeightChange={setExpandedContentHeight}
        onInputEngaged={engageQuickInput}
        actions={{
          cancelTurn,
          closeReply: (replyId) => {
            setClosedReplyId(replyId);
            setSubmission(null);
          },
          closeSubmission: () => setSubmission(null),
          dismissAmbient: () => {
            if (ambientDialogue !== null) {
              channel.requestVoiceStop();
              setClosedAmbientId(ambientDialogue.presentation_id);
            }
          },
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
          resetPosition,
          retrySubmission,
          send: sendMessage,
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
        autoPlay={autoPlay}
        voiceStatus={voiceStatus}
        focusRequest={focusRequest}
        inputOpen={inputOpen && !cardOpen}
        interactive={surfaceInteractive}
        menuOpen={effectiveMenuOpen}
        muted={muted}
        reply={reply}
        ambientDialogue={ambientDialogue}
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
): PresenceSubmissionCard | null {
  if (submission?.phase !== "failed") return null;
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

function layoutForSurface(surface: FairySurface): PetInputLayout {
  switch (surface) {
    case "input": return "compact";
    case "options":
    case "submission":
    case "reply": return "expanded";
    case "ambient": return "expanded";
    case "notice": return "core";
    case "core": return "core";
  }
}

function writeInputPresentationDiagnostics(
  commit: { session_id: number; revision: number } | null,
  status: "starting" | "pending" | "committed" | "failed" | "recovered",
): void {
  if (typeof document === "undefined") return;
  const targets = [
    document.documentElement,
    document.querySelector('[data-testid="presence-input-surface"]'),
  ];
  for (const target of targets) {
    if (!(target instanceof HTMLElement)) continue;
    target.dataset.inputPresentationStatus = status;
    target.dataset.inputPresentationSession = String(commit?.session_id ?? 0);
    target.dataset.inputPresentationRevision = String(commit?.revision ?? 0);
  }
}
