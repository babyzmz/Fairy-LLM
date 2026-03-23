import { useEffect, useRef } from "react";

import type { ChatUiMessage } from "../../lib/stores/chatStore";
import { MessageItem } from "./MessageItem";

interface MessageListProps {
  messages: ChatUiMessage[];
}

export function MessageList({ messages }: MessageListProps): JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages]);

  return (
    <div className="message-list" ref={ref}>
      {messages.length === 0 ? (
        <div className="message-list__empty">
          Fairy is ready. Try a weather, map, or news query against the backend.
        </div>
      ) : null}
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}
    </div>
  );
}
