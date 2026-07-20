import {
  Check,
  CircleAlert,
  Gamepad2,
  LoaderCircle,
  LogOut,
  MessageSquarePlus,
  MonitorUp,
  Pin,
  PinOff,
  RotateCcw,
  Send,
  Settings,
  Square,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { AnimatePresence, m } from "motion/react";

import type { PresenceReply, PresenceView } from "../domain/projection";

export interface PresencePanelActions {
  cancelTurn(): void;
  closeReply(replyId: string): void;
  closeSubmission(): void;
  dismissNotice(): void;
  exit(): void;
  newChat(): void;
  openMain(): void;
  openCompanion(): void;
  openReview(): void;
  openSettings(): void;
  requestInputFocus(): void;
  resetPosition(): void;
  retrySubmission(): void;
  send(text: string): void;
  setInputOpen(open: boolean): void;
  setMenuOpen(open: boolean): void;
  toggleAlwaysOnTop(): void;
  toggleAutoPlay(): void;
  toggleMuted(): void;
  stopVoice(): void;
}

export interface PresenceSubmissionCard {
  id: string;
  phase: "sending" | "accepted" | "cancelling" | "cancelled" | "failed";
  title: string;
  detail: string;
  canCancel: boolean;
  canRetry: boolean;
}

interface PresencePanelProps {
  actions: PresencePanelActions;
  alwaysOnTop: boolean;
  autoPlay: boolean;
  focusRequest?: number;
  inputOpen: boolean;
  interactive?: boolean;
  menuOpen: boolean;
  muted: boolean;
  reply: PresenceReply | null;
  submission: PresenceSubmissionCard | null;
  view: PresenceView;
  visible: boolean;
  onCompactWidthChange?(width: number): void;
}

export const PRESENCE_COMPACT_INPUT_MIN_WIDTH = 280;
export const PRESENCE_COMPACT_INPUT_MAX_WIDTH = 420;
const PRESENCE_COMPACT_INPUT_CHROME_WIDTH = 104;

export function PresencePanel({
  actions,
  alwaysOnTop,
  autoPlay,
  focusRequest = 0,
  inputOpen,
  interactive = true,
  menuOpen,
  muted,
  reply,
  submission,
  view,
  visible,
  onCompactWidthChange,
}: PresencePanelProps) {
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const composing = useRef(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const lastSubmission = useRef<{ text: string; submittedAt: number } | null>(null);
  const submitTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (submitTimer.current !== null) window.clearTimeout(submitTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (focusRequest > 0 && inputOpen && interactive) textarea.current?.focus();
  }, [focusRequest, inputOpen, interactive]);

  useLayoutEffect(() => {
    if (!inputOpen || onCompactWidthChange === undefined) return;
    const field = textarea.current;
    const content = longestLine(draft || field?.placeholder || "Message Fairy");
    const measured = measureInputText(content, field);
    onCompactWidthChange(compactInputWidthForText(measured));
  }, [draft, inputOpen, onCompactWidthChange]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    const now = performance.now();
    const duplicate =
      lastSubmission.current?.text === value &&
      now - lastSubmission.current.submittedAt < 1_000;
    if (value.length === 0 || composing.current || submitting || !interactive || duplicate) {
      return;
    }
    lastSubmission.current = { text: value, submittedAt: now };
    setSubmitting(true);
    actions.send(value);
    setDraft("");
    actions.setInputOpen(false);
    submitTimer.current = window.setTimeout(() => {
      setSubmitting(false);
      submitTimer.current = null;
    }, 300);
  }

  return (
    <section
      className="presence-panel"
      data-interactive={String(interactive)}
      data-visible={String(visible)}
      inert={!interactive}
    >
      <AnimatePresence initial={false} mode="sync">
      {!menuOpen && view.notice !== null ? (
        <m.aside
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className={`presence-card notice ${view.notice.tone}`}
          exit={{ opacity: 0, y: -4, scale: 0.985 }}
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          key={`notice:${view.notice.id}`}
          role="alert"
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <div>
            <strong>{view.status_text}</strong>
            <span>{view.notice.text}</span>
          </div>
          <button
            aria-label="Dismiss notice"
            onClick={actions.dismissNotice}
            title="Dismiss"
            type="button"
          >
            <X size={14} />
          </button>
          {view.work_state === "awaiting_confirmation" ? (
            <button
              className="presence-card-action"
              onClick={actions.openReview}
              type="button"
            >
              Review in Fairy
            </button>
          ) : null}
        </m.aside>
      ) : null}

      {!menuOpen && view.notice === null && reply !== null ? (
        <m.aside
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className="presence-card reply"
          exit={{ opacity: 0, y: -4, scale: 0.985 }}
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          key={`reply:${reply.id}`}
          role="status"
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="presence-card-copy">
            <strong>{reply.streaming ? "Fairy is replying" : "Fairy"}</strong>
            <span>{reply.text}</span>
          </div>
          <div className="presence-card-controls">
            {reply.streaming ? (
              <button
                aria-label="Stop reply"
                onClick={actions.cancelTurn}
                title="Stop reply"
                type="button"
              >
                <Square size={13} />
              </button>
            ) : null}
            {view.speaking ? (
              <button
                aria-label="Stop reading"
                onClick={actions.stopVoice}
                title="Stop reading"
                type="button"
              >
                <VolumeX size={14} />
              </button>
            ) : null}
            <button
              aria-label="Open reply in Fairy"
              onClick={actions.openMain}
              title="Open in Fairy"
              type="button"
            >
              <MonitorUp size={14} />
            </button>
            <button
              aria-label="Close reply"
              onClick={() => actions.closeReply(reply.id)}
              title="Close"
              type="button"
            >
              <X size={14} />
            </button>
          </div>
        </m.aside>
      ) : null}

      {!menuOpen && reply === null && view.notice === null && submission !== null ? (
        <m.aside
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className={`presence-card submission ${submission.phase}`}
          data-submission-id={submission.id}
          exit={{ opacity: 0, y: -4, scale: 0.985 }}
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          key={`submission:${submission.id}`}
          role="status"
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="presence-card-copy">
            <strong>
              {submission.phase === "failed" ? (
                <CircleAlert aria-hidden="true" size={13} />
              ) : submission.phase === "cancelled" ? (
                <Check aria-hidden="true" size={13} />
              ) : (
                <LoaderCircle aria-hidden="true" className="submission-spinner" size={13} />
              )}
              {submission.title}
            </strong>
            <span>{submission.detail}</span>
          </div>
          <div className="presence-card-controls">
            {submission.canCancel ? (
              <button
                aria-label="Cancel request"
                onClick={actions.cancelTurn}
                title="Cancel"
                type="button"
              >
                <Square size={13} />
              </button>
            ) : null}
            {submission.canRetry ? (
              <button
                aria-label="Retry request"
                onClick={actions.retrySubmission}
                title="Retry"
                type="button"
              >
                <RotateCcw size={14} />
              </button>
            ) : null}
            {!submission.canCancel ? (
              <button
                aria-label="Dismiss request status"
                onClick={actions.closeSubmission}
                title="Dismiss"
                type="button"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>
        </m.aside>
      ) : null}

      {inputOpen ? (
        <m.form
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className="presence-input"
          exit={{ opacity: 0, y: 4, scale: 0.985 }}
          initial={false}
          key="input"
          onSubmit={submit}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <div
            className="presence-input-field"
            data-optical-layer="transparent-overlay"
            data-testid="presence-input-field"
            onPointerDown={() => {
              actions.requestInputFocus();
              textarea.current?.focus({ preventScroll: true });
            }}
          >
            <textarea
              aria-label="Quick message to Fairy"
              ref={textarea}
              maxLength={4_000}
              onChange={(event) => setDraft(event.target.value)}
              onCompositionEnd={() => {
                composing.current = false;
              }}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onKeyDown={(event) => {
                const nativeComposing = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229;
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !composing.current &&
                  !nativeComposing
                ) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Message Fairy"
              rows={3}
              value={draft}
            />
          </div>
          <button
            aria-label="Send quick message"
            className="presence-send-button"
            disabled={!interactive || draft.trim() === "" || submitting}
            title="Send"
            type="submit"
          >
            <Send size={15} />
          </button>
        </m.form>
      ) : null}

      {menuOpen ? (
        <m.div
          animate={{ opacity: 1, y: 0 }}
          aria-label="Fairy menu"
          className="presence-menu"
          exit={{ opacity: 0, y: -4 }}
          initial={{ opacity: 0, y: 5 }}
          key="menu"
          role="menu"
          transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
        >
          <MenuButton
            icon={<MessageSquarePlus size={15} />}
            label="New chat"
            onClick={() => {
              actions.newChat();
              actions.setMenuOpen(false);
              actions.setInputOpen(true);
            }}
          />
          <MenuButton
            icon={<Gamepad2 size={15} />}
            label="Game companion"
            onClick={actions.openCompanion}
          />
          <MenuToggle
            checked={autoPlay}
            icon={<Volume2 size={15} />}
            label="Auto-play replies"
            onClick={actions.toggleAutoPlay}
          />
          <MenuToggle
            checked={muted}
            icon={muted ? <VolumeX size={15} /> : <Volume2 size={15} />}
            label="Mute"
            onClick={actions.toggleMuted}
          />
          <MenuToggle
            checked={alwaysOnTop}
            icon={alwaysOnTop ? <Pin size={15} /> : <PinOff size={15} />}
            label="Always on top"
            onClick={actions.toggleAlwaysOnTop}
          />
          <MenuButton
            icon={<MonitorUp size={15} />}
            label="Open Fairy"
            onClick={actions.openMain}
          />
          <MenuButton
            icon={<Settings size={15} />}
            label="Settings"
            onClick={actions.openSettings}
          />
          <MenuButton
            icon={<RotateCcw size={15} />}
            label="Reset position"
            onClick={actions.resetPosition}
          />
          <MenuButton
            danger
            icon={<LogOut size={15} />}
            label="Exit Fairy"
            onClick={actions.exit}
          />
        </m.div>
      ) : null}
      </AnimatePresence>
    </section>
  );
}

export function compactInputWidthForText(measuredTextWidth: number): number {
  const raw = Math.ceil((Math.max(0, measuredTextWidth) + PRESENCE_COMPACT_INPUT_CHROME_WIDTH) / 4) * 4;
  return Math.min(
    PRESENCE_COMPACT_INPUT_MAX_WIDTH,
    Math.max(PRESENCE_COMPACT_INPUT_MIN_WIDTH, raw),
  );
}

function longestLine(value: string): string {
  return value.split(/\r?\n/u).reduce(
    (longest, line) => line.length > longest.length ? line : longest,
    "",
  );
}

function measureInputText(value: string, field: HTMLTextAreaElement | null): number {
  const userAgent = field?.ownerDocument.defaultView?.navigator.userAgent ?? "";
  if (field !== null && !userAgent.toLowerCase().includes("jsdom")) {
    const context = document.createElement("canvas").getContext("2d");
    if (context !== null) {
      const style = window.getComputedStyle(field);
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      return context.measureText(value).width;
    }
  }
  return Array.from(value).reduce(
    (width, character) => width + (/^[\u0000-\u00ff]$/u.test(character) ? 7 : 13),
    0,
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
    <button
      className={danger ? "danger" : undefined}
      onClick={onClick}
      role="menuitem"
      type="button"
    >
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
    <button
      aria-checked={checked}
      onClick={onClick}
      role="menuitemcheckbox"
      type="button"
    >
      {icon}<span>{label}</span>
      {checked ? <Check className="menu-check" size={14} /> : null}
    </button>
  );
}
