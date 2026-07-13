import {
  Check,
  CircleAlert,
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
  useRef,
  useState,
} from "react";
import { m } from "motion/react";

import type { PresenceReply, PresenceView } from "../domain/projection";

export interface PresencePanelActions {
  cancelTurn(): void;
  closeReply(replyId: string): void;
  closeSubmission(): void;
  dismissNotice(): void;
  exit(): void;
  newChat(): void;
  openMain(): void;
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
}

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
      {!menuOpen && view.notice !== null ? (
        <m.aside
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className={`presence-card notice ${view.notice.tone}`}
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          role="alert"
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

      {!menuOpen && reply !== null ? (
        <m.aside
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className="presence-card reply"
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          role="status"
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
          initial={{ opacity: 0, y: 6, scale: 0.98 }}
          role="status"
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
        <form className="presence-input" onSubmit={submit}>
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
            onPointerDown={actions.requestInputFocus}
            placeholder="Message Fairy"
            rows={3}
            value={draft}
          />
          <button
            aria-label="Send quick message"
            disabled={!interactive || draft.trim() === "" || submitting}
            title="Send"
            type="submit"
          >
            <Send size={15} />
          </button>
        </form>
      ) : null}

      {menuOpen ? (
        <m.div
          animate={{ opacity: 1, y: 0 }}
          aria-label="Fairy menu"
          className="presence-menu"
          initial={{ opacity: 0, y: 5 }}
          role="menu"
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
    </section>
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
