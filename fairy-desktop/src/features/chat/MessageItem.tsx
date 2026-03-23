import type { ChatUiMessage } from "../../lib/stores/chatStore";
import { CardRenderer } from "./CardRenderer";

interface MessageItemProps {
  message: ChatUiMessage;
}

export function MessageItem({ message }: MessageItemProps): JSX.Element {
  const roleClass =
    message.kind === "user"
      ? "message message--user"
      : message.kind === "error"
        ? "message message--error"
        : "message message--assistant";

  const cards = "cards" in message ? message.cards : [];
  const errors = "errors" in message ? message.errors : [];
  const text = "text" in message ? message.text : "";
  const progressText = message.kind === "assistant_partial" ? message.progressText : "";

  return (
    <article className={roleClass}>
      <div className="message__bubble">
        {progressText ? <div className="message__progress">{progressText}</div> : null}
        {text ? <p className="message__text">{text}</p> : null}
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
