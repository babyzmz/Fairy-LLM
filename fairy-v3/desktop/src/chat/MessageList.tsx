import { Bot, CircleUserRound, LoaderCircle, Wrench } from "lucide-react";
import { useEffect, useRef } from "react";

import type { AssistantTurn, Message } from "../core/client";
import { VoiceSpeakControl } from "../voice/VoiceController";

interface MessageListProps {
  messages: Message[];
  streamedText: string;
  turn: AssistantTurn | null;
  developerMode: boolean;
}

export function MessageList({
  messages,
  streamedText,
  turn,
  developerMode,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scrollIntoView = endRef.current?.scrollIntoView;
    if (typeof scrollIntoView === "function") {
      scrollIntoView.call(endRef.current, { block: "end" });
    }
  }, [messages, streamedText]);

  if (messages.length === 0 && !streamedText) {
    return (
      <div className="message-list message-list-empty" aria-label="Conversation messages">
        <Bot size={24} />
        <strong>Start a conversation</strong>
      </div>
    );
  }

  return (
    <div className="message-list" aria-label="Conversation messages" aria-live="polite">
      {messages.map((message) => (
        <article
          className={`message-row message-${message.role}`}
          key={message.id}
          data-message-sequence={message.sequence}
        >
          <div className="message-avatar" aria-hidden="true">
            <MessageIcon role={message.role} />
          </div>
          <div className="message-content">
            <div className="message-meta">
              <strong>{messageLabel(message.role)}</strong>
              <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
              <VoiceSpeakControl message={message} />
            </div>
            <p>{message.content}</p>
            {developerMode ? (
              <div className="message-developer-meta">
                seq {message.sequence} | task {message.task_id} | turn {message.turn_id ?? "none"}
              </div>
            ) : null}
          </div>
        </article>
      ))}
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
            <p>{streamedText}</p>
            {developerMode && turn ? (
              <div className="message-developer-meta">turn {turn.id}</div>
            ) : null}
          </div>
        </article>
      ) : null}
      <div ref={endRef} />
    </div>
  );
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
