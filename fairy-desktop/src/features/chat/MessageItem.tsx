import type { ChatUiMessage } from "../../lib/stores/chatStore";
import { CardRenderer } from "./CardRenderer";

interface MessageItemProps {
  message: ChatUiMessage;
  activityPreview?: string;
  onOpenActivity?: (requestId: string) => void;
}

export function MessageItem({ message, activityPreview = "", onOpenActivity }: MessageItemProps): JSX.Element {
  const roleClass =
    message.kind === "user"
      ? "message message--user"
      : message.kind === "error"
        ? "message message--error"
        : "message message--assistant";

  const cards = "cards" in message ? message.cards : [];
  const errors = "errors" in message ? message.errors : [];
  const text = "text" in message ? message.text : "";
  const attachments = "attachments" in message && Array.isArray(message.attachments) ? message.attachments : [];
  const requestId = "requestId" in message && typeof message.requestId === "string" ? message.requestId : "";
  const showThoughtChip = Boolean(activityPreview) && Boolean(requestId) && message.kind !== "user" && message.kind !== "error";

  return (
    <article className={roleClass}>
      <div className="message__bubble">
        {showThoughtChip ? (
          <button className="message__thought-chip" onClick={() => onOpenActivity?.(requestId)} type="button">
            {activityPreview}
          </button>
        ) : null}
        {text ? <p className="message__text">{text}</p> : null}
        {attachments.length > 0 ? (
          <div className="message__attachments">
            {attachments.map((attachment) => (
              <span className="message__attachment-chip" key={`${attachment.path}-${attachment.name}`}>
                {attachment.name}
              </span>
            ))}
          </div>
        ) : null}
        {errors.length > 0 ? (
          <div className="message__errors">
            {errors.map((error) => (
              <div key={`${error.code}-${error.message}`} className="message__error">
                <strong>{error.code}</strong>: {error.message}
              </div>
            ))}
          </div>
        ) : null}
        {cards.length > 0 ? (
          <div className="message__cards">
            {cards.map((card, index) => (
              <CardRenderer card={card} key={`${card.type}-${index}`} />
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
