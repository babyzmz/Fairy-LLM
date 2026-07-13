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
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { m } from "motion/react";

import type { PresenceReply, PresenceView } from "../domain/projection";

export interface PresencePanelActions {
  closeReply(replyId: string): void;
  dismissNotice(): void;
  exit(): void;
  newChat(): void;
  openMain(): void;
  openReview(): void;
  openSettings(): void;
  resetPosition(): void;
  send(text: string): void;
  setInputOpen(open: boolean): void;
  setMenuOpen(open: boolean): void;
  toggleAlwaysOnTop(): void;
  toggleAutoPlay(): void;
  toggleMuted(): void;
}

interface PresencePanelProps {
  actions: PresencePanelActions;
  alwaysOnTop: boolean;
  autoPlay: boolean;
  inputOpen: boolean;
  menuOpen: boolean;
  muted: boolean;
  reply: PresenceReply | null;
  view: PresenceView;
  visible: boolean;
}

export function PresencePanel({
  actions,
  alwaysOnTop,
  autoPlay,
  inputOpen,
  menuOpen,
  muted,
  reply,
  view,
  visible,
}: PresencePanelProps) {
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const composing = useRef(false);
  const submitTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (submitTimer.current !== null) window.clearTimeout(submitTimer.current);
    },
    [],
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (value.length === 0 || composing.current || submitting) return;
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
    <section className="presence-panel" data-visible={String(visible)}>
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
          <div>
            <strong>{reply.streaming ? "Fairy is replying" : "Fairy"}</strong>
            <span>{reply.text}</span>
          </div>
          <button
            aria-label="Close reply"
            onClick={() => actions.closeReply(reply.id)}
            title="Close"
            type="button"
          >
            <X size={14} />
          </button>
        </m.aside>
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
          <button
            aria-label="Send quick message"
            disabled={draft.trim() === "" || submitting}
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
