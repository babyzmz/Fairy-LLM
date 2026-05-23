import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { executeCommand, type CommandSpec } from "../../lib/api/commands";
import { CommandAutocomplete } from "../commands/CommandAutocomplete";
import { applyRewrite, matchPrefix, parseCommandInput, useSlashCommands } from "../commands/useSlashCommands";

interface ComposerProps {
  disabled?: boolean;
  streaming?: boolean;
  onSend: (message: string, attachments: File[]) => Promise<void> | void;
  onCancel?: () => void;
}

const SCREENSHOT_REWRITE = "请截取当前屏幕并分析画面要点。";

function attachmentKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function resolveDispatch(spec: CommandSpec, args: string): { mode: "chat"; message: string } | { mode: "server" } | { mode: "ignore" } {
  switch (spec.kind) {
    case "rewrite_message":
      return { mode: "chat", message: applyRewrite(spec, args) };
    case "client_action":
      if (spec.client_action_type === "screenshot_then_chat") {
        return { mode: "chat", message: SCREENSHOT_REWRITE };
      }
      return { mode: "ignore" };
    case "server_action":
      return { mode: "server" };
    default:
      return { mode: "ignore" };
  }
}

export function Composer({ disabled = false, streaming = false, onSend, onCancel }: ComposerProps): JSX.Element {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<File[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const { commands } = useSlashCommands();

  const matches = useMemo(() => matchPrefix(commands, value), [commands, value]);
  const showSuggestions = matches.length > 0 && value.trimStart().startsWith("/");

  useEffect(() => {
    if (highlighted >= matches.length) {
      setHighlighted(0);
    }
  }, [highlighted, matches.length]);

  function pickSpec(spec: CommandSpec): void {
    const argSuffix = spec.argument_label ? " " : "";
    setValue(spec.name + argSuffix);
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>): Promise<void> {
    event?.preventDefault();
    const message = value.trim();
    if ((message.length === 0 && attachments.length === 0) || disabled) {
      return;
    }
    const parsed = parseCommandInput(commands, message);
    if (parsed) {
      const dispatch = resolveDispatch(parsed.spec, parsed.args);
      if (dispatch.mode === "server" && parsed.spec.server_handler_name) {
        await executeCommand(parsed.spec.name).catch(() => undefined);
        setValue("");
        return;
      }
      if (dispatch.mode === "chat") {
        await onSend(dispatch.message, attachments);
        setValue("");
        setAttachments([]);
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }
      if (dispatch.mode === "ignore") {
        setValue("");
        return;
      }
    }
    await onSend(message, attachments);
    setValue("");
    setAttachments([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (showSuggestions) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlighted((current) => (current + 1) % matches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlighted((current) => (current - 1 + matches.length) % matches.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        const spec = matches[highlighted];
        if (spec) pickSpec(spec);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setHighlighted(0);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const selected = Array.from(event.target.files || []);
    if (selected.length === 0) {
      return;
    }
    setAttachments((current) => {
      const existing = new Set(current.map(attachmentKey));
      const next = [...current];
      for (const file of selected) {
        const key = attachmentKey(file);
        if (!existing.has(key)) {
          existing.add(key);
          next.push(file);
        }
      }
      return next;
    });
  }

  function removeAttachment(index: number): void {
    setAttachments((current) => current.filter((_, currentIndex) => currentIndex !== index));
  }

  return (
    <form ref={formRef} className="composer" onSubmit={(event) => void handleSubmit(event)} style={{ position: "relative" }}>
      {showSuggestions ? (
        <CommandAutocomplete matches={matches} highlighted={highlighted} onPick={pickSpec} />
      ) : null}
      <div className="composer__main">
        {attachments.length > 0 ? (
          <div className="composer__attachments">
            {attachments.map((file, index) => (
              <button
                className="composer__attachment-chip"
                key={attachmentKey(file)}
                onClick={() => removeAttachment(index)}
                type="button"
              >
                {file.name}
              </button>
            ))}
          </div>
        ) : null}
        <textarea
          className="composer__input"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={3}
          placeholder="Ask Fairy something (输入 / 唤起命令)"
        />
      </div>
      <div className="composer__actions">
        <input
          className="composer__file-input"
          multiple
          onChange={handleFileChange}
          ref={fileInputRef}
          type="file"
        />
        <button
          className="composer__attach"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          type="button"
        >
          Attach
        </button>
        <button className="composer__send" type="submit" disabled={disabled || (!value.trim() && attachments.length === 0)}>
          {disabled ? "Sending..." : "Send"}
        </button>
        {streaming ? (
          <button className="composer__cancel" type="button" onClick={onCancel}>
            Interrupt
          </button>
        ) : null}
      </div>
    </form>
  );
}
