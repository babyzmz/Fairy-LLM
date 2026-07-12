import {
  Bot,
  CircleUserRound,
  Copy,
  LoaderCircle,
  Pencil,
  RotateCcw,
  Trash2,
  Wrench,
} from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";

import type { AssistantTurn, EventEnvelope, Message } from "../core/client";
import { VoiceSpeakControl } from "../voice/VoiceController";
import { ActivityRail } from "./ActivityRail";
import type { OptimisticUserMessage } from "./useAssistantTurn";
import { MessageContent } from "./MessageContent";

interface MessageListProps {
  messages: Message[];
  streamedText: string;
  turn: AssistantTurn | null;
  events: EventEnvelope[];
  pendingUserMessage: OptimisticUserMessage | null;
  developerMode: boolean;
  onRetryPending(): Promise<void>;
  onEditPending(): void;
  onDeletePending(): void;
  onCopy(taskId: string, content: string): Promise<void>;
  onOpenLink(taskId: string, url: string): Promise<void>;
}

export function MessageList({
  messages,
  streamedText,
  turn,
  events,
  pendingUserMessage,
  developerMode,
  onRetryPending,
  onEditPending,
  onDeletePending,
  onCopy,
  onOpenLink,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const visibleMessages = developerMode
    ? messages
    : messages.filter((message) => message.role !== "tool");

  useEffect(() => {
    if (!followingRef.current) return;
    scrollToLatest(listRef.current, endRef.current, false);
  }, [messages, pendingUserMessage, streamedText]);

  if (visibleMessages.length === 0 && !streamedText && pendingUserMessage === null) {
    return (
      <div className="message-list message-list-empty" aria-label="Conversation messages">
        <Bot size={24} />
        <strong>Start a conversation</strong>
      </div>
    );
  }

  const railTurnId = turn?.id ?? null;
  return (
    <div
      ref={listRef}
      className="message-list"
      aria-label="Conversation messages"
      aria-live="polite"
      onScroll={(event) => {
        const node = event.currentTarget;
        const following = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
        followingRef.current = following;
        setShowJump(!following);
      }}
    >
      {visibleMessages.map((message) => (
        <Fragment key={message.id}>
          <MessageRow
            message={message}
            developerMode={developerMode}
            onCopy={onCopy}
            onOpenLink={onOpenLink}
          />
          {message.role === "user" && message.turn_id === railTurnId && turn !== null ? (
            <ActivityRail turn={turn} events={events} />
          ) : null}
        </Fragment>
      ))}
      {pendingUserMessage !== null ? (
        <>
          <PendingMessageRow
            message={pendingUserMessage}
            onRetry={onRetryPending}
            onEdit={onEditPending}
            onDelete={onDeletePending}
          />
          {turn !== null ? <ActivityRail turn={turn} events={events} /> : null}
        </>
      ) : null}
      {streamedText ? (
        <article className="message-row message-assistant message-streaming">
          <div className="message-avatar" aria-hidden="true">
            <LoaderCircle className="spin" size={16} />
          </div>
          <div className="message-content">
            <div className="message-meta">
              <strong>Fairy</strong>
              <span>responding</span>
            </div>
            <MessageContent
              content={streamedText}
              taskId={turn?.task_id ?? ""}
              onCopy={onCopy}
              onOpenLink={onOpenLink}
            />
            <span className="streaming-cursor" aria-hidden="true" />
          </div>
        </article>
      ) : null}
      {showJump ? (
        <button
          className="jump-to-latest"
          type="button"
          onClick={() => {
            followingRef.current = true;
            setShowJump(false);
            scrollToLatest(listRef.current, endRef.current, true);
          }}
        >
          Jump to latest
        </button>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function MessageRow({
  message,
  developerMode,
  onCopy,
  onOpenLink,
}: {
  message: Message;
  developerMode: boolean;
  onCopy(taskId: string, content: string): Promise<void>;
  onOpenLink(taskId: string, url: string): Promise<void>;
}) {
  return (
    <article
      className={`message-row message-${message.role}`}
      data-message-sequence={message.sequence}
    >
      <div className="message-avatar" aria-hidden="true">
        <MessageIcon role={message.role} />
      </div>
      <div className="message-content">
        <div className="message-meta">
          <strong>{messageLabel(message.role)}</strong>
          <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
          <button
            className="message-action"
            type="button"
            aria-label="Copy message"
            title="Copy message"
            onClick={() => void onCopy(message.task_id, message.content)}
          >
            <Copy size={13} />
          </button>
          <VoiceSpeakControl message={message} />
        </div>
        <MessageContent
          content={message.content}
          taskId={message.task_id}
          onCopy={onCopy}
          onOpenLink={onOpenLink}
        />
        {developerMode ? (
          <div className="message-developer-meta">
            seq {message.sequence} | task {message.task_id} | turn {message.turn_id ?? "none"}
          </div>
        ) : null}
      </div>
    </article>
  );
}

function PendingMessageRow({
  message,
  onRetry,
  onEdit,
  onDelete,
}: {
  message: OptimisticUserMessage;
  onRetry(): Promise<void>;
  onEdit(): void;
  onDelete(): void;
}) {
  return (
    <article className={`message-row message-user message-pending message-${message.status}`}>
      <div className="message-avatar" aria-hidden="true">
        {message.status === "sending" ? <LoaderCircle className="spin" size={16} /> : <CircleUserRound size={16} />}
      </div>
      <div className="message-content">
        <div className="message-meta">
          <strong>You</strong>
          <span>{message.status === "sending" ? "Sending" : "Not sent"}</span>
        </div>
        <div className="message-markdown"><p>{message.content}</p></div>
        {message.attachmentCount > 0 ? (
          <span className="pending-attachments">{message.attachmentCount} attachment(s)</span>
        ) : null}
        {message.status === "failed" ? (
          <div className="pending-actions" role="group" aria-label="Failed message actions">
            <span>{message.error}</span>
            <button type="button" onClick={() => void onRetry()}><RotateCcw size={13} /> Retry</button>
            <button type="button" onClick={onEdit}><Pencil size={13} /> Edit</button>
            <button type="button" onClick={onDelete}><Trash2 size={13} /> Delete</button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function scrollToLatest(
  list: HTMLDivElement | null,
  end: HTMLDivElement | null,
  smooth: boolean,
) {
  if (list !== null && typeof list.scrollTo === "function") {
    list.scrollTo({ top: list.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    return;
  }
  end?.scrollIntoView?.({ block: "end" });
}

function MessageIcon({ role }: { role: Message["role"] }) {
  if (role === "user") return <CircleUserRound size={16} />;
  if (role === "tool") return <Wrench size={16} />;
  return <Bot size={16} />;
}

function messageLabel(role: Message["role"]): string {
  if (role === "user") return "You";
  if (role === "tool") return "Tool";
  if (role === "system_notice") return "Fairy Core";
  return "Fairy";
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}
