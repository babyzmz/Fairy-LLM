import { Paperclip, Send, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  CaptureControl,
  type PendingImageAttachment,
} from "../perception/CaptureControl";
import { VoiceRecordControl } from "../voice/VoiceController";
import type { AssistantDraft } from "./useAssistantTurn";

const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const ACCEPTED_DOCUMENTS = ".txt,.md,.markdown,.html,.htm,.pdf,.docx";

interface ComposerProps {
  disabled: boolean;
  isBusy: boolean;
  visionAvailable: boolean;
  draft?: AssistantDraft | null;
  onSubmit(
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ): Promise<void>;
  onStop(): Promise<void>;
}

export function Composer({
  disabled,
  isBusy,
  visionAvailable,
  draft = null,
  onSubmit,
  onStop,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [capture, setCapture] = useState<PendingImageAttachment | null>(null);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const effectiveBusy = isBusy || isSubmitting;
  const canSubmit =
    !disabled &&
    !effectiveBusy &&
    (value.trim().length > 0 || files.length > 0 || capture !== null);

  useEffect(() => {
    if (draft === null) return;
    setValue(draft.value);
    setFiles(draft.files);
    setCapture(draft.images[0] ?? null);
    inputRef.current?.focus();
  }, [draft]);

  const submit = async () => {
    if (!canSubmit) return;
    setIsSubmitting(true);
    try {
      await onSubmit(value.trim(), files, capture === null ? [] : [capture]);
      setValue("");
      setFiles([]);
      setCapture(null);
      setAttachmentError(null);
      inputRef.current?.focus();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form
      className="chat-composer"
      onSubmit={(event) => {
        event.preventDefault();
        void submit().catch(() => undefined);
      }}
    >
      {files.length > 0 ? (
        <div className="attachment-strip" aria-label="Pending attachments">
          {files.map((file, index) => (
            <span className="attachment-item" key={`${file.name}:${file.size}:${index}`}>
              <span>{file.name}</span>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                title={`Remove ${file.name}`}
                onClick={() => setFiles((current) => current.filter((_, item) => item !== index))}
              >
                <X size={13} />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {attachmentError ? (
        <div className="composer-error" role="alert">
          {attachmentError}
        </div>
      ) : null}
      <div className="composer-controls">
        <input
          ref={fileInputRef}
          className="sr-only"
          type="file"
          multiple
          accept={ACCEPTED_DOCUMENTS}
          aria-label="Attach documents"
          disabled={disabled || effectiveBusy}
          onChange={(event) => {
            const selected = Array.from(event.currentTarget.files ?? []);
            const oversized = selected.find((file) => file.size > MAX_ATTACHMENT_BYTES);
            if (oversized) {
              setAttachmentError(`${oversized.name} exceeds the 20 MiB document limit`);
            } else {
              setAttachmentError(null);
              setFiles((current) => [...current, ...selected].slice(0, 10));
            }
            event.currentTarget.value = "";
          }}
        />
        <button
          className="icon-button"
          type="button"
          aria-label="Attach documents"
          title="Attach documents"
          disabled={disabled || effectiveBusy}
          onClick={() => fileInputRef.current?.click()}
        >
          <Paperclip size={17} />
        </button>
        <CaptureControl
          disabled={disabled || effectiveBusy}
          visionAvailable={visionAvailable}
          value={capture}
          onChange={setCapture}
        />
        <label className="composer-field chat-composer-field">
          <span className="sr-only">Message Fairy</span>
          <textarea
            ref={inputRef}
            aria-label="Message Fairy"
            value={value}
            rows={1}
            placeholder="Message Fairy"
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submit().catch(() => undefined);
              }
              if (event.key === "Escape" && effectiveBusy) {
                event.preventDefault();
                void onStop();
              }
            }}
          />
        </label>
        <VoiceRecordControl
          disabled={disabled || effectiveBusy}
          onTranscript={(text) =>
            setValue((current) => (current.trim() ? `${current.trimEnd()} ${text}` : text))
          }
        />
        {effectiveBusy ? (
          <button
            className="send-button stop-button"
            type="button"
            aria-label="Stop response"
            title="Stop response"
            onClick={() => void onStop()}
          >
            <Square size={15} />
          </button>
        ) : (
          <button
            className="send-button"
            type="submit"
            aria-label="Send message"
            title="Send message"
            disabled={!canSubmit}
          >
            <Send size={17} />
          </button>
        )}
      </div>
    </form>
  );
}
