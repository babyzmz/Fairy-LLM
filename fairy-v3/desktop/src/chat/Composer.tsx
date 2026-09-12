import { CalendarClock, ChevronDown, Paperclip, Pause, Play, Send, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  CaptureControl,
  type PendingImageAttachment,
} from "../perception/CaptureControl";
import { VoiceRecordControl } from "../voice/VoiceController";
import type {
  AssistantWorkflowSummary,
  ModelCatalogPage,
  ModelSelectionPreference,
} from "../core/client";
import { ModelSelector } from "../models/ModelSelector";
import { ScheduleRuleEditor } from "./ScheduleRuleEditor";
import type { ScheduleRuleDraft } from "./scheduleRules";
import type { AssistantDraft } from "./useAssistantTurn";

const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const ACCEPTED_DOCUMENTS = ".txt,.md,.markdown,.html,.htm,.pdf,.docx";

interface ComposerProps {
  inputAriaLabel?: string;
  disabled: boolean;
  isBusy: boolean;
  visionAvailable: boolean;
  modelCatalog: ModelCatalogPage | null;
  modelSelection: ModelSelectionPreference | null;
  modelSelectionDisabled?: boolean;
  submissionBlockedReason?: string | null;
  workflowSummary?: AssistantWorkflowSummary | null;
  clarificationQuestion?: string | null;
  draft?: AssistantDraft | null;
  onSubmit(
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ): Promise<void>;
  onSchedule?(value: string, rule: ScheduleRuleDraft): Promise<void>;
  onStop(): Promise<void>;
  onPauseWorkflow?(): Promise<void>;
  onResumeWorkflow?(): Promise<void>;
  onSelectModel(mode: "auto" | "manual", modelId: string | null): Promise<void>;
  onOpenModelSettings(): Promise<void>;
}

export function Composer({
  inputAriaLabel = "Message Fairy",
  disabled,
  isBusy,
  visionAvailable,
  modelCatalog,
  modelSelection,
  modelSelectionDisabled = false,
  submissionBlockedReason = null,
  workflowSummary = null,
  clarificationQuestion = null,
  draft = null,
  onSubmit,
  onSchedule,
  onStop,
  onPauseWorkflow,
  onResumeWorkflow,
  onSelectModel,
  onOpenModelSettings,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [capture, setCapture] = useState<PendingImageAttachment | null>(null);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [scheduleMenuOpen, setScheduleMenuOpen] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"later" | "repeat" | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scheduleMenuRef = useRef<HTMLDivElement>(null);
  const draftRevisionRef = useRef(0);
  const submittingRef = useRef(false);
  const taskUpdateMode =
    workflowSummary !== null &&
    ["queued", "running", "waiting_for_approval", "paused"].includes(
      workflowSummary.status,
    );
  const taskUpdateAvailable =
    workflowSummary !== null &&
    ["queued", "running", "paused"].includes(workflowSummary.status);
  const workflowPaused = workflowSummary?.status === "paused";
  const clarificationMode = clarificationQuestion !== null;
  const restrictedInputMode = taskUpdateMode || clarificationMode;
  const effectiveInputAriaLabel = clarificationMode
    ? "Answer Fairy's clarification"
    : taskUpdateMode
      ? "Update the current task"
      : inputAriaLabel;
  const effectiveBusy = isSubmitting || (isBusy && !taskUpdateMode);
  const canSubmit =
    !disabled &&
    (capture === null || visionAvailable) &&
    scheduleMode === null &&
    !effectiveBusy &&
    (submissionBlockedReason === null || taskUpdateAvailable) &&
    (clarificationMode
      ? value.trim().length > 0 && files.length === 0 && capture === null
      : taskUpdateMode
        ? taskUpdateAvailable &&
        value.trim().length > 0 &&
        files.length === 0 &&
        capture === null
        : value.trim().length > 0 || files.length > 0 || capture !== null);

  useEffect(() => {
    if (draft === null) return;
    draftRevisionRef.current += 1;
    setValue(draft.value);
    setFiles(draft.files);
    setCapture(draft.images[0] ?? null);
    inputRef.current?.focus();
  }, [draft]);

  useEffect(() => {
    const input = inputRef.current;
    if (input === null) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }, [value]);

  useEffect(() => {
    if (!scheduleMenuOpen) return;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !scheduleMenuRef.current?.contains(target)) {
        setScheduleMenuOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setScheduleMenuOpen(false);
    };
    window.addEventListener("pointerdown", closeOnOutside, true);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutside, true);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [scheduleMenuOpen]);

  const submit = async () => {
    if (!canSubmit || submittingRef.current) return;
    const submittedDraft = {
      value,
      files,
      capture,
      revision: draftRevisionRef.current,
    };
    submittingRef.current = true;
    setIsSubmitting(true);
    setValue("");
    setFiles([]);
    setCapture(null);
    setAttachmentError(null);
    setActionError(null);
    setScheduleMenuOpen(false);
    if (fileInputRef.current !== null) fileInputRef.current.value = "";
    inputRef.current?.focus();
    try {
      await onSubmit(
        submittedDraft.value.trim(),
        submittedDraft.files,
        submittedDraft.capture === null ? [] : [submittedDraft.capture],
      );
    } catch (error) {
      setActionError("Could not send this message. Your draft has been kept; check the connection and try again.");
      if (draftRevisionRef.current === submittedDraft.revision) {
        setValue(submittedDraft.value);
        setFiles(submittedDraft.files);
        setCapture(submittedDraft.capture);
      }
      throw error;
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  const openScheduleEditor = (mode: "later" | "repeat") => {
    setScheduleMenuOpen(false);
    if (disabled || effectiveBusy || restrictedInputMode || submissionBlockedReason !== null) return;
    if (value.trim().length === 0) {
      setAttachmentError("Enter an instruction before scheduling it");
      inputRef.current?.focus();
      return;
    }
    if (files.length > 0 || capture !== null) {
      setAttachmentError(
        "Scheduled tasks cannot use temporary attachments or screenshots. Remove them or bind files to the Workspace first.",
      );
      return;
    }
    setAttachmentError(null);
    setScheduleMode(mode);
  };

  const createSchedule = async (rule: ScheduleRuleDraft) => {
    if (submittingRef.current || disabled || restrictedInputMode || submissionBlockedReason !== null) {
      throw new Error(submissionBlockedReason ?? "Scheduling is not available right now");
    }
    if (onSchedule === undefined) throw new Error("Scheduling is unavailable");
    const instruction = value.trim();
    if (!instruction) throw new Error("Enter an instruction before scheduling it");
    if (files.length > 0 || capture !== null) {
      throw new Error("Scheduled tasks cannot use temporary attachments or screenshots");
    }
    const revision = draftRevisionRef.current;
    submittingRef.current = true;
    setIsSubmitting(true);
    try {
      await onSchedule(instruction, rule);
      if (draftRevisionRef.current === revision) {
        draftRevisionRef.current += 1;
        setValue("");
      }
      setScheduleMode(null);
      inputRef.current?.focus();
    } finally {
      submittingRef.current = false;
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
                onClick={() => {
                  draftRevisionRef.current += 1;
                  setFiles((current) => current.filter((_, item) => item !== index));
                }}
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
      {actionError ? <div className="composer-error" role="alert">{actionError}</div> : null}
      <div className="composer-input-surface">
        <label className="composer-field chat-composer-field">
          <span className="sr-only">{effectiveInputAriaLabel}</span>
          <textarea
            ref={inputRef}
            aria-label={effectiveInputAriaLabel}
            value={value}
            rows={1}
            placeholder={
              workflowSummary?.status === "waiting_for_approval"
                ? "Resolve approval to continue"
                : clarificationMode
                  ? "Type the missing detail"
                  : taskUpdateMode
                    ? "Update the current task"
                    : "Message Fairy"
            }
            disabled={disabled}
            onChange={(event) => {
              draftRevisionRef.current += 1;
              setValue(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                event.preventDefault();
                void submit().catch(() => undefined);
              }
              if (event.key === "Escape" && isBusy) {
                event.preventDefault();
                void onStop().catch(() => setActionError("Could not stop the task. Please try again."));
              }
            }}
          />
        </label>
        {submissionBlockedReason ? (
          <div className="composer-guidance" role="status">
            {submissionBlockedReason}
            <button type="button" className="composer-settings-link" onClick={() => void onOpenModelSettings().catch(() => setActionError("Could not open model settings."))}>Model settings</button>
          </div>
        ) : null}
        {capture !== null && !visionAvailable ? (
          <div className="composer-guidance" role="status">Choose a model with vision or remove the screenshot before sending.</div>
        ) : null}
        <div className="composer-toolbar">
          <div className="composer-toolbar-start">
            {taskUpdateMode ? (
              <span className="composer-workflow-mode" role="status">
                {workflowPaused
                  ? "Task paused"
                  : workflowSummary.status === "waiting_for_approval"
                    ? "Approval required"
                    : workflowSummary.pause_requested
                      ? "Pausing at the next boundary"
                      : "Update current task"}
                <span>r{workflowSummary.active_plan_revision}</span>
              </span>
            ) : clarificationMode ? (
              <span className="composer-workflow-mode clarification" role="status">
                Answer one detail
              </span>
            ) : null}
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              multiple
              accept={ACCEPTED_DOCUMENTS}
              aria-label="Attach documents"
              disabled={disabled || effectiveBusy || restrictedInputMode}
              onChange={(event) => {
                const selected = Array.from(event.currentTarget.files ?? []);
                const oversized = selected.find((file) => file.size > MAX_ATTACHMENT_BYTES);
                const unsupported = selected.find((file) => !ACCEPTED_DOCUMENTS.split(",").some(extension => file.name.toLowerCase().endsWith(extension)));
                if (files.length + selected.length > 10) {
                  setAttachmentError("Attach at most 10 documents. Remove some files before adding this batch.");
                } else if (unsupported) {
                  setAttachmentError(`${unsupported.name} is not a supported document format`);
                } else if (oversized) {
                  setAttachmentError(`${oversized.name} exceeds the 20 MiB document limit`);
                } else {
                  setAttachmentError(null);
                  draftRevisionRef.current += 1;
                  setFiles((current) => [...current, ...selected]);
                }
                event.currentTarget.value = "";
              }}
            />
            <button
              className="icon-button"
              type="button"
              aria-label="Attach documents"
              title="Attach documents"
              disabled={disabled || effectiveBusy || restrictedInputMode}
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip size={17} />
            </button>
            <CaptureControl
              disabled={disabled || effectiveBusy || restrictedInputMode}
              visionAvailable={visionAvailable}
              value={capture}
              onChange={(nextCapture) => {
                draftRevisionRef.current += 1;
                setCapture(nextCapture);
              }}
            />
            <ModelSelector
              catalog={modelCatalog}
              selection={modelSelection}
              disabled={modelSelectionDisabled || restrictedInputMode || isSubmitting}
              onSelect={onSelectModel}
              onOpenSettings={onOpenModelSettings}
            />
          </div>
          <div className="composer-toolbar-end">
            <VoiceRecordControl
              disabled={disabled || effectiveBusy || taskUpdateMode}
              onTranscript={(text) => {
                draftRevisionRef.current += 1;
                setValue((current) => (
                  current.trim() ? `${current.trimEnd()} ${text}` : text
                ));
              }}
            />
            {taskUpdateMode ? (
              <>
                <button
                  className="icon-button workflow-pause-button"
                  type="button"
                  aria-label={workflowPaused ? "Resume task" : "Pause task"}
                  title={workflowPaused ? "Resume task" : "Pause task"}
                  disabled={
                    isSubmitting ||
                    workflowSummary.status === "waiting_for_approval" ||
                    (!workflowPaused && workflowSummary.pause_requested)
                  }
                  onClick={() => void (
                    workflowPaused ? onResumeWorkflow?.() : onPauseWorkflow?.()
                  )?.catch(() => setActionError("Could not update the task state. Please try again."))}
                >
                  {workflowPaused ? <Play size={15} /> : <Pause size={15} />}
                </button>
                <button
                  className="send-button stop-button"
                  type="button"
                  aria-label="Stop response"
                  title="Stop response"
                  disabled={isSubmitting}
                  onClick={() => void onStop().catch(() => setActionError("Could not stop the task. Please try again."))}
                >
                  <Square size={15} />
                </button>
                <button
                  className="send-button workflow-update-button"
                  type="submit"
                  aria-label="Update current task"
                  title="Update current task"
                  disabled={!canSubmit}
                >
                  <Send size={17} />
                </button>
              </>
            ) : effectiveBusy ? (
              <button
                className="send-button stop-button"
                type="button"
                aria-label="Stop response"
                title="Stop response"
                onClick={() => void onStop().catch(() => setActionError("Could not stop the task. Please try again."))}
              >
                <Square size={15} />
              </button>
            ) : (
              <>
                {onSchedule !== undefined && !clarificationMode ? (
                  <div className="composer-schedule-menu" ref={scheduleMenuRef}>
                    <button
                      className="icon-button composer-schedule-trigger"
                      type="button"
                      aria-label="Schedule message"
                      aria-expanded={scheduleMenuOpen}
                      title="Schedule message"
                      disabled={disabled || effectiveBusy || submissionBlockedReason !== null || scheduleMode !== null}
                      onClick={() => setScheduleMenuOpen((open) => !open)}
                    >
                      <CalendarClock size={16} />
                      <ChevronDown size={11} />
                    </button>
                    {scheduleMenuOpen ? (
                      <div className="composer-schedule-options" role="menu" aria-label="Execution time">
                        <button type="button" role="menuitem" disabled={!canSubmit} onClick={() => void submit().catch(() => undefined)}>
                          <Send size={14} />
                          <span><strong>Run now</strong><small>Send this message immediately</small></span>
                        </button>
                        <button type="button" role="menuitem" onClick={() => openScheduleEditor("later")}>
                          <CalendarClock size={14} />
                          <span><strong>Run later</strong><small>Choose a future local time</small></span>
                        </button>
                        <button type="button" role="menuitem" onClick={() => openScheduleEditor("repeat")}>
                          <Play size={14} />
                          <span><strong>Repeat</strong><small>Daily, weekly, or an interval</small></span>
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <button
                  className="send-button"
                  type="submit"
                  aria-label="Send message"
                  title="Send message"
                  disabled={!canSubmit}
                >
                  <Send size={17} />
                </button>
              </>
            )}
          </div>
        </div>
      </div>
      {scheduleMode !== null ? (
        <ScheduleRuleEditor
          key={scheduleMode}
          mode={scheduleMode}
          busy={isSubmitting}
          onSubmit={createSchedule}
          onCancel={() => {
            setScheduleMode(null);
            inputRef.current?.focus();
          }}
        />
      ) : null}
    </form>
  );
}
