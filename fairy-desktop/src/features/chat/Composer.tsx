import { FormEvent, KeyboardEvent, useState } from "react";

interface ComposerProps {
  disabled?: boolean;
  streaming?: boolean;
  onSend: (message: string) => Promise<void> | void;
  onCancel?: () => void;
}

export function Composer({ disabled = false, streaming = false, onSend, onCancel }: ComposerProps): JSX.Element {
  const [value, setValue] = useState("");

  async function handleSubmit(event?: FormEvent<HTMLFormElement>): Promise<void> {
    event?.preventDefault();
    const message = value.trim();
    if (!message || disabled) {
      return;
    }
    setValue("");
    await onSend(message);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  }

  return (
    <form className="composer" onSubmit={(event) => void handleSubmit(event)}>
      <textarea
        className="composer__input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={3}
        placeholder="Ask Fairy something..."
      />
      <button className="composer__send" type="submit" disabled={disabled || !value.trim()}>
        {disabled ? "Sending..." : "Send"}
      </button>
      {streaming ? (
        <button className="composer__cancel" type="button" onClick={onCancel}>
          Interrupt
        </button>
      ) : null}
    </form>
  );
}
