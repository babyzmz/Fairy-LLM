import {
  AlertTriangle,
  Check,
  ListTodo,
  MessageSquarePlus,
  PanelRightOpen,
  RotateCcw,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type {
  Approval,
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
  AssistantSchedule,
  AssistantTurn,
  EventEnvelope,
  Message,
  ModelCatalogPage,
  ModelSelectionPreference,
  ProviderHealth,
  ProviderProfile,
  RealtimeTranscriptEntry,
  SlashCommandMetadata,
  TurnTrace,
} from "../core/client";
import type { PendingImageAttachment } from "../perception/CaptureControl";
import type { BackgroundTaskAction } from "../app/workspaceTypes";
import { BackgroundTasksPopover } from "./BackgroundTasksPopover";
import { Composer } from "./Composer";
import { ChatFairyEye } from "../fairyEye/ChatFairyEye";
import { MessageList } from "./MessageList";
import type { ScheduleCardActions } from "./ScheduleCard";
import type { ScheduleRuleDraft } from "./scheduleRules";
import type { ChatTimelineTarget } from "./timelineTarget";
import type { AssistantDraft, OptimisticUserMessage } from "./useAssistantTurn";
import type { TurnTraceQueryState } from "./useTurnTraces";
import { parseSlashCommand } from "./slashCommands";
import "./streaming.css";
import "./requestInterpretation.css";

export interface ChatWorkspaceProps {
  fairyEyeEnabled?: boolean;
  fairyEyeReducedMotion?: boolean;
  fairyEyeActive?: boolean;
  conversationAvailable: boolean;
  contentState?: "idle" | "loading" | "ready" | "error";
  messages: Message[];
  realtimeTranscript: RealtimeTranscriptEntry[];
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
  onCommand?(text: string): Promise<string | null>;
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
  onPauseWorkflow?(): Promise<void>;
  onResumeWorkflow?(): Promise<void>;
  onSteer?(instruction: string): Promise<void>;
  onRespondToClarification?(content: string): Promise<void>;
  onRetry(): Promise<void>;
  onRetryPending(): Promise<void>;
  onDeletePending(): void;
  onTakePendingForEdit(): AssistantDraft | null;
  onCopyMessage(taskId: string, content: string): Promise<void>;
  onOpenMessageLink(taskId: string, url: string): Promise<void>;
  onDecision(approvalId: string, approved: boolean): Promise<void>;
  onSelectModel(mode: "auto" | "manual", modelId: string | null): Promise<void>;
  onOpenModelSettings(): Promise<void>;
  inspectorCollapsed?: boolean;
  onRestoreInspector?(): void;
  conversationId?: string | null;
  backgroundTasks?: AssistantBackgroundTaskPage;
  backgroundTasksLoading?: boolean;
  onOpenBackgroundTask?(task: AssistantBackgroundTask): void;
  onManageBackgroundTask?(
    task: AssistantBackgroundTask,
    action: BackgroundTaskAction,
  ): Promise<void>;
  schedules?: AssistantSchedule[];
  schedulesLoading?: boolean;
  onCreateSchedule?(instruction: string, rule: ScheduleRuleDraft): Promise<void>;
  onUpdateSchedule?(schedule: AssistantSchedule, rule: ScheduleRuleDraft): Promise<void>;
  onPauseSchedule?(schedule: AssistantSchedule): Promise<void>;
  onResumeSchedule?(schedule: AssistantSchedule): Promise<void>;
  onRunNowSchedule?(schedule: AssistantSchedule): Promise<void>;
  onCancelSchedule?(schedule: AssistantSchedule): Promise<void>;
  timelineTarget?: ChatTimelineTarget | null;
  onTimelineTargetLocated?(key: string): void;
}

export function ChatWorkspace(props: ChatWorkspaceProps) {
  const [notice, setNotice] = useState<string | null>(null);
  const [composerDraft, setComposerDraft] = useState<AssistantDraft | null>(null);
  const restoreInspectorRef = useRef<HTMLButtonElement | null>(null);
  const backgroundTasksButtonRef = useRef<HTMLButtonElement | null>(null);
  const backgroundTasksPanelRef = useRef<HTMLDivElement | null>(null);
  const inspectorWasCollapsed = useRef(props.inspectorCollapsed ?? false);
  const [backgroundTasksOpen, setBackgroundTasksOpen] = useState(false);
  const providerAvailable = props.modelSelectionBlockReason === null;
  const contentState = props.contentState ?? "ready";
  const retryAvailable = ["failed", "cancelled"].includes(props.turn?.status ?? "");
  const newConversationAvailable = props.slashCommands.some(
    (command) => command.name === "new" && command.available,
  );
  const pendingApproval =
    props.approvals.find((approval) => approval.decision === "pending") ?? null;
  const workflowSummary = props.turn?.workflow_summary ?? null;
  const interpretation = props.turn?.interpretation_summary ?? null;
  const waitingForClarification = props.turn?.status === "waiting_for_input";
  const workflowCanUpdate =
    props.turn?.status === "running" &&
    workflowSummary !== null &&
    ["queued", "running", "paused"].includes(workflowSummary.status);
  const statusLabel = useMemo(() => {
    if (!providerAvailable) return "Provider unavailable";
    if (props.offline) return "Core offline";
    if (pendingApproval !== null) return "Approval required";
    if (waitingForClarification) return "Clarification needed";
    if (workflowSummary?.status === "paused") return "Task paused";
    if (props.isBusy) return "Fairy is working";
    return "Ready";
  }, [
    pendingApproval,
    props.isBusy,
    props.offline,
    providerAvailable,
    waitingForClarification,
    workflowSummary?.status,
  ]);
  const scheduleActions = useMemo<ScheduleCardActions>(() => ({
    update: (schedule, rule) => requiredScheduleAction(props.onUpdateSchedule)(schedule, rule),
    pause: (schedule) => requiredScheduleAction(props.onPauseSchedule)(schedule),
    resume: (schedule) => requiredScheduleAction(props.onResumeSchedule)(schedule),
    runNow: (schedule) => requiredScheduleAction(props.onRunNowSchedule)(schedule),
    cancel: (schedule) => requiredScheduleAction(props.onCancelSchedule)(schedule),
  }), [
    props.onCancelSchedule,
    props.onPauseSchedule,
    props.onResumeSchedule,
    props.onRunNowSchedule,
    props.onUpdateSchedule,
  ]);
  const scheduleRunNowAvailability = useMemo(() => {
    const tasks = [
      ...(props.backgroundTasks?.current ?? []),
      ...(props.backgroundTasks?.other ?? []),
    ];
    return new Map(
      tasks
        .filter((task) => task.schedule_id !== null)
        .map((task) => [task.schedule_id as string, task.can_run_now]),
    );
  }, [props.backgroundTasks]);

  useEffect(() => {
    const collapsed = props.inspectorCollapsed ?? false;
    if (collapsed && !inspectorWasCollapsed.current) {
      restoreInspectorRef.current?.focus();
    }
    inspectorWasCollapsed.current = collapsed;
  }, [props.inspectorCollapsed]);

  useEffect(() => {
    setBackgroundTasksOpen(false);
  }, [props.conversationId]);

  useEffect(() => {
    if (!props.inspectorCollapsed) setBackgroundTasksOpen(false);
  }, [props.inspectorCollapsed]);

  useEffect(() => {
    if (!backgroundTasksOpen) return;
    backgroundTasksPanelRef.current
      ?.querySelector<HTMLButtonElement>("[data-autofocus='true']")
      ?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (
        backgroundTasksPanelRef.current?.contains(target) ||
        backgroundTasksButtonRef.current?.contains(target)
      ) return;
      setBackgroundTasksOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setBackgroundTasksOpen(false);
      backgroundTasksButtonRef.current?.focus();
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [backgroundTasksOpen]);

  const submit = async (
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ) => {
    if (waitingForClarification) {
      setNotice(null);
      if (files.length > 0 || images.length > 0) {
        setNotice("Clarification replies cannot add attachments");
        return;
      }
      if (props.onRespondToClarification === undefined) {
        throw new Error("Clarification response control is unavailable");
      }
      await props.onRespondToClarification(value);
      return;
    }
    const command = parseSlashCommand(value, props.slashCommands);
    if (command === null) {
      setNotice(null);
      if (workflowCanUpdate) {
        if (files.length > 0 || images.length > 0) {
          setNotice("Task updates cannot add attachments");
          return;
        }
        if (props.onSteer === undefined) throw new Error("Task update control is unavailable");
        await props.onSteer(value);
        return;
      }
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
    if (!props.onCommand) {
      setNotice("Core command dispatcher is unavailable");
      return;
    }
    setNotice(await props.onCommand(value));
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
          {props.inspectorCollapsed ? (
            <button
              ref={backgroundTasksButtonRef}
              className="icon-button background-tasks-trigger"
              type="button"
              aria-label="Background tasks"
              aria-controls="chat-background-tasks"
              aria-expanded={backgroundTasksOpen}
              title="Background tasks"
              onClick={() => setBackgroundTasksOpen((open) => !open)}
            >
              <ListTodo size={17} />
              {(props.backgroundTasks?.nonterminal_count ?? 0) > 0 ? (
                <span className="background-tasks-badge">
                  {Math.min(props.backgroundTasks?.nonterminal_count ?? 0, 99)}
                </span>
              ) : null}
            </button>
          ) : null}
          {props.inspectorCollapsed ? (
            <button
              ref={restoreInspectorRef}
              className="icon-button"
              type="button"
              aria-label="Restore workspace inspector"
              aria-controls="workspace-inspector"
              aria-expanded="false"
              title="Restore inspector"
              onClick={() => {
                setBackgroundTasksOpen(false);
                props.onRestoreInspector?.();
              }}
            >
              <PanelRightOpen size={17} />
            </button>
          ) : null}
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
      {props.fairyEyeEnabled === true && <ChatFairyEye
        key={props.conversationId ?? "no-conversation"}
        conversationId={props.conversationId ?? null}
        turnId={props.turn?.id ?? null}
        turnConversationId={props.turn?.conversation_id ?? null}
        turnStatus={props.turn?.status ?? null}
        messageTurnIds={props.messages.flatMap(message => message.turn_id ? [message.turn_id] : [])}
        busy={props.isBusy}
        waiting={pendingApproval !== null || waitingForClarification || workflowSummary?.status === "paused"}
        error={Boolean(props.error) || props.offline || !providerAvailable}
        loading={contentState === "loading"}
        empty={props.messages.length === 0 && !props.streamedText && !props.pendingUserMessage}
        reducedMotion={props.fairyEyeReducedMotion}
        active={props.fairyEyeActive ?? true}
      />}
      {backgroundTasksOpen ? (
        <BackgroundTasksPopover
          page={props.backgroundTasks ?? EMPTY_BACKGROUND_TASKS}
          loading={props.backgroundTasksLoading ?? false}
          busy={props.isActing}
          panelRef={backgroundTasksPanelRef}
          onOpen={(task) => {
            setBackgroundTasksOpen(false);
            props.onOpenBackgroundTask?.(task);
          }}
          onAction={async (task, action) => {
            if (props.onManageBackgroundTask === undefined) {
              throw new Error("Background task controls are unavailable");
            }
            await props.onManageBackgroundTask(task, action);
          }}
          onClose={() => {
            setBackgroundTasksOpen(false);
            backgroundTasksButtonRef.current?.focus();
          }}
        />
      ) : null}
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
          schedules={props.schedules ?? []}
          schedulesLoading={props.schedulesLoading ?? false}
          scheduleBusy={props.isActing}
          scheduleActions={scheduleActions}
          scheduleCanRunNow={(schedule) => scheduleRunNowAvailability.get(schedule.id) ?? true}
          timelineTarget={props.timelineTarget ?? null}
          onTimelineTargetLocated={props.onTimelineTargetLocated}
          realtimeTranscript={props.realtimeTranscript}
          events={props.events}
          streamedText={props.streamedText}
          turn={props.turn}
          turnTraces={props.turnTraces}
          turnTraceStates={props.turnTraceStates}
          pendingUserMessage={props.pendingUserMessage}
          developerMode={props.developerMode}
          onRetryPending={props.onRetryPending}
          onDeletePending={props.onDeletePending}
          onPauseWorkflow={props.onPauseWorkflow}
          onResumeWorkflow={props.onResumeWorkflow}
          onCancelWorkflow={props.onCancel}
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
      {interpretation !== null &&
      (waitingForClarification ||
        interpretation.disposition === "assumed" ||
        interpretation.objectives.length > 1) ? (
        <section
          className={`request-interpretation-card ${waitingForClarification ? "needs-input" : "assumed"}`}
          aria-label={waitingForClarification ? "Clarification required" : "Request interpretation"}
          role={waitingForClarification ? "status" : undefined}
        >
          <span className="eyebrow">
            {waitingForClarification
              ? "ONE DETAIL NEEDED"
              : interpretation.objectives.length > 1
                ? "PLAN UNDERSTOOD"
                : "INTERPRETATION"}
          </span>
          <strong>
            {waitingForClarification
              ? interpretation.clarification_question
              : interpretation.public_summary}
          </strong>
          {!waitingForClarification && interpretation.assumptions.length > 0 ? (
            <p>{interpretation.assumptions.join(" · ")}</p>
          ) : null}
        </section>
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
        workflowSummary={workflowSummary}
        clarificationQuestion={
          waitingForClarification ? interpretation?.clarification_question ?? null : null
        }
        draft={composerDraft}
        onSubmit={submit}
        onSchedule={props.onCreateSchedule === undefined ? undefined : async (instruction, rule) => {
          const command = parseSlashCommand(instruction, props.slashCommands);
          if (command !== null) {
            throw new Error("Slash commands cannot be scheduled");
          }
          await props.onCreateSchedule?.(instruction, rule);
          setNotice("Scheduled task added to this conversation");
        }}
        onStop={props.onCancel}
        onPauseWorkflow={props.onPauseWorkflow}
        onResumeWorkflow={props.onResumeWorkflow}
        onSelectModel={props.onSelectModel}
        onOpenModelSettings={props.onOpenModelSettings}
      />
    </section>
  );
}

const EMPTY_BACKGROUND_TASKS: AssistantBackgroundTaskPage = {
  current: [],
  other: [],
  recent: [],
  nonterminal_count: 0,
};

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}

function requiredScheduleAction<T extends unknown[], R>(
  action: ((...args: T) => Promise<R>) | undefined,
): (...args: T) => Promise<R> {
  if (action === undefined) {
    return async () => {
      throw new Error("Schedule controls are unavailable");
    };
  }
  return action;
}
