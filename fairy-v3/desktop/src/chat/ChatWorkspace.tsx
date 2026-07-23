import { AlertTriangle, Check, MessageSquarePlus, RotateCcw, X } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  Approval,
  AssistantTurn,
  EventEnvelope,
  Message,
  ModelCatalogPage,
  ModelSelectionPreference,
  ProviderHealth,
  ProviderProfile,
  SlashCommandMetadata,
  TurnTrace,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import type { AssistantDraft, OptimisticUserMessage } from "./useAssistantTurn";
import type { TurnTraceQueryState } from "./useTurnTraces";
import { parseSlashCommand, slashCommandHelp } from "./slashCommands";
import "./streaming.css";

export interface ChatWorkspaceProps {
  conversationAvailable: boolean;
  contentState?: "idle" | "loading" | "ready" | "error";
  messages: Message[];
  events: EventEnvelope[];
  streamedText: string;
  pendingUserMessage: OptimisticUserMessage | null;
  turn: AssistantTurn | null;
  turnTraces: Record<string, TurnTrace>;
  turnTraceStates: Record<string, TurnTraceQueryState>;
  approvals: Approval[];
  providers: ProviderProfile[];
  providerHealth: ProviderHealth[];
  selectedProfileId: string | null;
  modelCatalog: ModelCatalogPage | null;
  modelSelection: ModelSelectionPreference | null;
  modelSelectionLoading: boolean;
  modelSelectionBlockReason: string | null;
  visionAvailable: boolean;
  isBusy: boolean;
  isActing: boolean;
  offline: boolean;
  developerMode: boolean;
  error: string | null;
  slashCommands: SlashCommandMetadata[];
  onNewConversation(): Promise<void>;
  onSwitchProject(): void;
  onPermissionChange(
    profile: "observe" | "standard" | "autonomous",
  ): Promise<void>;
  onSend(
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ): Promise<void>;
  onCancel(): Promise<void>;
  onRetry(): Promise<void>;
  onRetryPending(): Promise<void>;
  onDeletePending(): void;
  onTakePendingForEdit(): AssistantDraft | null;
  onCopyMessage(taskId: string, content: string): Promise<void>;
  onOpenMessageLink(taskId: string, url: string): Promise<void>;
  onDecision(approvalId: string, approved: boolean): Promise<void>;
  onSelectModel(mode: "auto" | "manual", modelId: string | null): Promise<void>;
  onOpenModelSettings(): Promise<void>;
}

export function ChatWorkspace(props: ChatWorkspaceProps) {
  const [notice, setNotice] = useState<string | null>(null);
  const [composerDraft, setComposerDraft] = useState<AssistantDraft | null>(null);
  const providerAvailable = props.modelSelectionBlockReason === null;
  const contentState = props.contentState ?? "ready";
  const retryAvailable = ["failed", "cancelled"].includes(props.turn?.status ?? "");
  const newConversationAvailable = props.slashCommands.some(
    (command) => command.name === "new" && command.available,
  );
  const pendingApproval =
    props.approvals.find((approval) => approval.decision === "pending") ?? null;
  const statusLabel = useMemo(() => {
    if (!providerAvailable) return "Provider unavailable";
    if (props.offline) return "Core offline";
    if (pendingApproval !== null) return "Approval required";
    if (props.isBusy) return "Fairy is working";
    return "Ready";
  }, [pendingApproval, props.isBusy, props.offline, providerAvailable]);

  const submit = async (
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ) => {
    const command = parseSlashCommand(value, props.slashCommands);
    if (command === null) {
      setNotice(null);
      await props.onSend(
        value || "Review the attached documents and screen captures.",
        files,
        images,
      );
      return;
    }
    if (files.length > 0 || images.length > 0) {
      setNotice("Slash commands cannot include attachments");
      return;
    }
    if (command.name === "unavailable" || command.name === "unknown") {
      setNotice(
        command.name === "unavailable"
          ? `Command unavailable: /${command.command}`
          : `Unknown command: /${command.command}`,
      );
      return;
    }
    if (command.name === "new" || command.name === "clear") {
      await props.onNewConversation();
      setNotice(command.name === "clear" ? "Started a new durable conversation" : null);
      return;
    }
    if (command.name === "project") {
      props.onSwitchProject();
      return;
    }
    if (command.name === "stop") {
      await props.onCancel();
      return;
    }
    if (command.name === "permission") {
      if (["observe", "standard", "autonomous"].includes(command.argument)) {
        await props.onPermissionChange(
          command.argument as "observe" | "standard" | "autonomous",
        );
        setNotice(`Permission profile: ${command.argument}`);
      } else {
        setNotice("Permission must be observe, standard, or autonomous");
      }
      return;
    }
    if (command.name === "help") {
      setNotice(slashCommandHelp(props.slashCommands));
      return;
    }
  };

  return (
    <section className="chat-workspace" aria-label="Chat workspace">
      <header className="chat-toolbar">
        <div>
          <span className="eyebrow">Conversation</span>
          <h1>Chat</h1>
        </div>
        <div className="chat-toolbar-actions">
          <span
            className={`chat-status ${providerAvailable && !props.offline ? "online" : "offline"}`}
          >
            {statusLabel}
          </span>
          <button
            className="icon-button"
            type="button"
            aria-label="New conversation"
            title="New conversation"
            disabled={props.offline || props.isBusy || !newConversationAvailable}
            onClick={() => void props.onNewConversation()}
          >
            <MessageSquarePlus size={17} />
          </button>
        </div>
      </header>
      {props.error || notice ? (
        <div className="chat-notice" role={props.error ? "alert" : "status"}>
          {props.error ?? notice}
        </div>
      ) : null}
      {props.conversationAvailable && contentState === "loading" ? (
        <div className="message-list message-list-loading" role="status">
          <span className="message-loading-indicator" aria-hidden="true" />
          <strong>Loading conversation</strong>
        </div>
      ) : props.conversationAvailable && contentState === "error" ? (
        <div className="message-list message-list-empty" role="alert">
          <AlertTriangle size={24} />
          <strong>Conversation could not be loaded</strong>
        </div>
      ) : props.conversationAvailable ? (
        <MessageList
          messages={props.messages}
          events={props.events}
          streamedText={props.streamedText}
          turn={props.turn}
          turnTraces={props.turnTraces}
          turnTraceStates={props.turnTraceStates}
          pendingUserMessage={props.pendingUserMessage}
          developerMode={props.developerMode}
          onRetryPending={props.onRetryPending}
          onDeletePending={props.onDeletePending}
          onEditPending={() => {
            const draft = props.onTakePendingForEdit();
            if (draft !== null) setComposerDraft(draft);
          }}
          onCopy={props.onCopyMessage}
          onOpenLink={props.onOpenMessageLink}
        />
      ) : (
        <div className="message-list message-list-empty">
          <MessageSquarePlus size={24} />
          <strong>No conversation selected</strong>
          <button
            className="primary-command"
            type="button"
            disabled={props.offline || !newConversationAvailable}
            onClick={() => void props.onNewConversation()}
          >
            <MessageSquarePlus size={15} /> New conversation
          </button>
        </div>
      )}
      {retryAvailable ? (
        <div className="chat-retry-bar">
          <span>
            {props.developerMode && props.turn?.error_code
              ? props.turn.error_code
              : "Response stopped before completion"}
          </span>
          <button className="secondary-command" type="button" onClick={() => void props.onRetry()}>
            <RotateCcw size={14} /> Retry response
          </button>
        </div>
      ) : null}
      {pendingApproval !== null ? (
        <div
          className="approval-block chat-approval-block"
          role="group"
          aria-label="Pending approval"
        >
          <div>
            <span className="eyebrow">
              <AlertTriangle size={12} /> APPROVAL REQUIRED
            </span>
            <strong>{pendingApproval.reason}</strong>
            <p>{pendingApproval.requested_by}</p>
          </div>
          <div className="approval-actions">
            <button
              className="secondary-command"
              type="button"
              disabled={props.isActing || props.isBusy}
              onClick={() => settle(props.onDecision(pendingApproval.id, false))}
            >
              <X size={14} /> Reject
            </button>
            <button
              className="primary-command"
              type="button"
              disabled={props.isActing || props.isBusy}
              onClick={() => settle(props.onDecision(pendingApproval.id, true))}
            >
              <Check size={14} /> Approve
            </button>
          </div>
        </div>
      ) : null}
      <Composer
        disabled={
          props.offline ||
          !props.conversationAvailable ||
          contentState !== "ready"
        }
        isBusy={props.isBusy}
        visionAvailable={props.visionAvailable}
        modelCatalog={props.modelCatalog}
        modelSelection={props.modelSelection}
        modelSelectionDisabled={props.offline || props.modelSelectionLoading}
        submissionBlockedReason={props.modelSelectionBlockReason}
        draft={composerDraft}
        onSubmit={submit}
        onStop={props.onCancel}
        onSelectModel={props.onSelectModel}
        onOpenModelSettings={props.onOpenModelSettings}
      />
    </section>
  );
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
