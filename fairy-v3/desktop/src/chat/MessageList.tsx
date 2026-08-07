import {
  Bot,
  CircleUserRound,
  Copy,
  LoaderCircle,
  Pencil,
  RotateCcw,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { m } from "motion/react";

import type {
  AssistantTurn,
  AssistantSchedule,
  EventEnvelope,
  Message,
  RealtimeTranscriptEntry,
  TurnTrace,
} from "../core/client";
import { VoiceSpeakControl } from "../voice/VoiceController";
import { ActivityRail } from "./ActivityRail";
import { EvidenceSources } from "./EvidenceSources";
import {
  messageLineKey,
  MessageLineSidebar,
  projectMessageLineItems,
  streamingMessageLineKey,
} from "./MessageLineSidebar";
import { RealtimeTranscript } from "./RealtimeTranscript";
import type { OptimisticUserMessage } from "./useAssistantTurn";
import type { TurnTraceQueryState } from "./useTurnTraces";
import { MessageContent } from "./MessageContent";
import { ScheduleCard, type ScheduleCardActions } from "./ScheduleCard";
import type { ChatTimelineTarget } from "./timelineTarget";

interface MessageListProps {
  messages: Message[];
  schedules?: AssistantSchedule[];
  schedulesLoading?: boolean;
  scheduleBusy?: boolean;
  scheduleActions?: ScheduleCardActions;
  scheduleCanRunNow?(schedule: AssistantSchedule): boolean;
  timelineTarget?: ChatTimelineTarget | null;
  onTimelineTargetLocated?(key: string): void;
  realtimeTranscript: RealtimeTranscriptEntry[];
  streamedText: string;
  turn: AssistantTurn | null;
  turnTraces: Record<string, TurnTrace>;
  turnTraceStates: Record<string, TurnTraceQueryState>;
  events: EventEnvelope[];
  pendingUserMessage: OptimisticUserMessage | null;
  developerMode: boolean;
  onRetryPending(): Promise<void>;
  onEditPending(): void;
  onDeletePending(): void;
  onPauseWorkflow?(): Promise<void>;
  onResumeWorkflow?(): Promise<void>;
  onCancelWorkflow?(): Promise<void>;
  onCopy(taskId: string, content: string): Promise<void>;
  onOpenLink(taskId: string, url: string): Promise<void>;
}

export function MessageList({
  messages,
  schedules = [],
  schedulesLoading = false,
  scheduleBusy = false,
  scheduleActions = UNAVAILABLE_SCHEDULE_ACTIONS,
  scheduleCanRunNow,
  timelineTarget = null,
  onTimelineTargetLocated,
  realtimeTranscript,
  streamedText,
  turn,
  turnTraces,
  turnTraceStates,
  events,
  pendingUserMessage,
  developerMode,
  onRetryPending,
  onEditPending,
  onDeletePending,
  onPauseWorkflow,
  onResumeWorkflow,
  onCancelWorkflow,
  onCopy,
  onOpenLink,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const anchorRefs = useRef(new Map<string, HTMLElement>());
  const followingRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const [activeLineKey, setActiveLineKey] = useState<string | null>(null);
  const visibleMessages = useMemo(
    () => messages.filter((message) => message.role !== "tool"),
    [messages],
  );
  const timelineItems = useMemo(() => [
    ...visibleMessages.map((message) => ({ kind: "message" as const, sequence: message.sequence, message })),
    ...schedules.map((schedule) => ({
      kind: "schedule" as const,
      sequence: schedule.timeline_sequence,
      schedule,
    })),
  ].sort((left, right) => left.sequence - right.sequence), [schedules, visibleMessages]);
  const durableAssistantForTurn =
    turn !== null &&
    visibleMessages.some(
      (message) => message.role === "assistant" && message.turn_id === turn.id,
    );
  const visibleStreamedText = durableAssistantForTurn ? "" : streamedText;
  const lineItems = useMemo(
    () => projectMessageLineItems(visibleMessages, visibleStreamedText, turn?.id ?? null),
    [turn?.id, visibleMessages, visibleStreamedText],
  );
  const lineItemKeys = useMemo(() => lineItems.map((item) => item.key), [lineItems]);
  const lineItemSignature = lineItemKeys.join("\u0000");
  const lineItemKeySet = useMemo(
    () => new Set(lineItemSignature ? lineItemSignature.split("\u0000") : []),
    [lineItemSignature],
  );
  const lineItemByKey = useMemo(
    () => new Map(lineItems.map((item) => [item.key, item])),
    [lineItems],
  );
  const lineItemAnchorKeys = useMemo(
    () => new Set(lineItems.map((item) => item.anchorKey)),
    [lineItems],
  );
  const firstLineItemKey = lineItemKeys[0] ?? null;
  const conversationScopeKey =
    visibleMessages[0]?.conversation_id ?? schedules[0]?.conversation_id ?? turn?.conversation_id ?? "empty";

  const updateActiveLine = useCallback(
    (list: HTMLDivElement | null) => {
      if (list === null || lineItems.length === 0) {
        setActiveLineKey(null);
        return;
      }
      const scanLine = list.getBoundingClientRect().top + 96;
      let next = lineItems[0]?.key ?? null;
      for (const item of lineItems) {
        const anchor = anchorRefs.current.get(item.anchorKey);
        if (anchor === undefined) continue;
        if (anchor.getBoundingClientRect().top > scanLine) break;
        next = item.key;
      }
      setActiveLineKey((current) => current === next ? current : next);
    },
    [lineItems],
  );

  const registerAnchor = useCallback((key: string, node: HTMLElement | null) => {
    if (node === null) anchorRefs.current.delete(key);
    else anchorRefs.current.set(key, node);
  }, []);

  useEffect(() => {
    if (!followingRef.current) return;
    scrollToLatest(listRef.current, endRef.current, false);
    const frame = requestAnimationFrame(() => updateActiveLine(listRef.current));
    return () => cancelAnimationFrame(frame);
  }, [messages, pendingUserMessage, schedules, streamedText, updateActiveLine]);

  useEffect(() => {
    followingRef.current = true;
    setShowJump(false);
    setActiveLineKey(null);
  }, [conversationScopeKey]);

  useEffect(() => {
    anchorRefs.current.forEach((_node, key) => {
      if (!lineItemAnchorKeys.has(key)) anchorRefs.current.delete(key);
    });
    setActiveLineKey((current) =>
      current !== null && lineItemKeySet.has(current)
        ? current
        : firstLineItemKey,
    );
  }, [firstLineItemKey, lineItemAnchorKeys, lineItemKeySet]);

  useEffect(() => {
    if (timelineTarget === null) return;
    const frame = requestAnimationFrame(() => {
      const list = listRef.current;
      if (list === null) return;
      const candidates = Array.from(
        list.querySelectorAll<HTMLElement>("[data-schedule-id], [data-turn-id]"),
      );
      const target = candidates.find((candidate) => (
        timelineTarget.scheduleId !== null &&
          candidate.dataset.scheduleId === timelineTarget.scheduleId
      ) || (
        timelineTarget.turnId !== null &&
          candidate.dataset.turnId === timelineTarget.turnId
      ));
      if (target === undefined) return;
      followingRef.current = false;
      setShowJump(true);
      scrollToAnchor(list, target, true);
      target.focus({ preventScroll: true });
      onTimelineTargetLocated?.(timelineTarget.key);
    });
    return () => cancelAnimationFrame(frame);
  }, [onTimelineTargetLocated, schedules, timelineItems, timelineTarget]);

  if (
    visibleMessages.length === 0 &&
    schedules.length === 0 &&
    realtimeTranscript.length === 0 &&
    !visibleStreamedText &&
    pendingUserMessage === null
  ) {
    return (
      <div className="message-list message-list-empty" aria-label="Conversation messages">
        <Bot size={24} />
        <strong>Start a conversation</strong>
      </div>
    );
  }

  const durableUserTurnIds = new Set(
    visibleMessages
      .filter((message) => message.role === "user" && message.turn_id !== null)
      .map((message) => message.turn_id as string),
  );
  return (
    <div className="message-list-shell">
      <MessageLineSidebar
        key={conversationScopeKey}
        items={lineItems}
        activeKey={activeLineKey}
        onNavigate={(key, smooth) => {
          const list = listRef.current;
          const anchorKey = lineItemByKey.get(key)?.anchorKey;
          const anchor =
            anchorKey === undefined ? undefined : anchorRefs.current.get(anchorKey);
          if (list === null || anchor === undefined) return;
          followingRef.current = false;
          setActiveLineKey(key);
          scrollToAnchor(list, anchor, smooth);
        }}
      />
      <div
        ref={listRef}
        className="message-list message-list-with-outline"
        aria-label="Conversation messages"
        aria-live="polite"
        onScroll={(event) => {
          const node = event.currentTarget;
          const following = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
          followingRef.current = following;
          setShowJump(!following);
          updateActiveLine(node);
        }}
      >
        <RealtimeTranscript entries={realtimeTranscript} />
        {timelineItems.map((item) => item.kind === "schedule" ? (
          <ScheduleCard
            key={item.schedule.id}
            schedule={item.schedule}
            busy={scheduleBusy}
            canRunNow={scheduleCanRunNow?.(item.schedule)}
            actions={scheduleActions}
          />
        ) : (
          <Fragment key={item.message.id}>
            <MessageRow
              message={item.message}
              anchorKey={
                item.message.role === "user" || item.message.role === "assistant"
                  ? messageLineKey(item.message.id)
                  : null
              }
              registerAnchor={registerAnchor}
              developerMode={developerMode}
              onCopy={onCopy}
              onOpenLink={onOpenLink}
              evidenceSources={
                item.message.role === "assistant" && item.message.turn_id !== null
                  ? turnTraces[item.message.turn_id]?.evidence_sources ?? []
                  : []
              }
            />
            {item.message.role === "user" && item.message.turn_id !== null &&
            (turnTraceStates[item.message.turn_id] !== undefined ||
              turnTraces[item.message.turn_id] !== undefined ||
              turn?.id === item.message.turn_id) ? (
              <ActivityRail
                turn={turn?.id === item.message.turn_id ? turn : null}
                turnId={item.message.turn_id}
                trace={turnTraces[item.message.turn_id] ?? null}
                traceState={turnTraceStates[item.message.turn_id] ?? null}
                events={events}
                developerMode={developerMode}
                onPause={turn?.id === item.message.turn_id ? onPauseWorkflow : undefined}
                onResume={turn?.id === item.message.turn_id ? onResumeWorkflow : undefined}
                onCancel={turn?.id === item.message.turn_id ? onCancelWorkflow : undefined}
              />
              ) : null}
          </Fragment>
        ))}
        {schedulesLoading && schedules.length === 0 ? (
          <div className="schedule-card-loading" role="status">Loading scheduled tasks</div>
        ) : null}
        {pendingUserMessage !== null ? (
          <>
            <PendingMessageRow
              message={pendingUserMessage}
              onRetry={onRetryPending}
              onEdit={onEditPending}
              onDelete={onDeletePending}
            />
            {turn !== null && !durableUserTurnIds.has(turn.id) ? (
              <ActivityRail
                turn={turn}
                trace={turnTraces[turn.id] ?? null}
                traceState={turnTraceStates[turn.id] ?? null}
                events={events}
                developerMode={developerMode}
                onPause={onPauseWorkflow}
                onResume={onResumeWorkflow}
                onCancel={onCancelWorkflow}
              />
            ) : null}
          </>
        ) : null}
        {visibleStreamedText ? (
          <m.article
            ref={(node) => registerAnchor(
              streamingMessageLineKey(turn?.id ?? null),
              node,
            )}
            data-message-line-key={streamingMessageLineKey(turn?.id ?? null)}
            initial={{ opacity: 0.4, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className="message-row message-assistant message-streaming"
          >
            <div className="message-avatar" aria-hidden="true">
              <LoaderCircle className="spin" size={16} />
            </div>
            <div className="message-content">
              <div className="message-meta">
                <strong>Fairy</strong>
                <span>responding</span>
              </div>
              <MessageContent
                content={visibleStreamedText}
                taskId={turn?.task_id ?? ""}
                onCopy={onCopy}
                onOpenLink={onOpenLink}
              />
              <span className="streaming-cursor" aria-hidden="true" />
            </div>
          </m.article>
        ) : null}
        {showJump ? (
          <button
            className="jump-to-latest"
            type="button"
            onClick={() => {
              followingRef.current = true;
              setShowJump(false);
              scrollToLatest(listRef.current, endRef.current, true);
            }}
          >
            Jump to latest
          </button>
        ) : null}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function MessageRow({
  message,
  anchorKey,
  registerAnchor,
  developerMode,
  onCopy,
  onOpenLink,
  evidenceSources,
}: {
  message: Message;
  anchorKey: string | null;
  registerAnchor(key: string, node: HTMLElement | null): void;
  developerMode: boolean;
  onCopy(taskId: string, content: string): Promise<void>;
  onOpenLink(taskId: string, url: string): Promise<void>;
  evidenceSources: TurnTrace["evidence_sources"];
}) {
  return (
    <m.article
      ref={(node) => {
        if (anchorKey !== null) registerAnchor(anchorKey, node);
      }}
      initial={{ opacity: 0.4, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`message-row message-${message.role}`}
      data-message-sequence={message.sequence}
      data-turn-id={message.turn_id ?? undefined}
      data-message-line-key={anchorKey ?? undefined}
      tabIndex={message.turn_id === null ? undefined : -1}
    >
      <div className="message-avatar" aria-hidden="true">
        <MessageIcon role={message.role} />
      </div>
      <div className="message-content">
        <div className="message-meta">
          <strong>{messageLabel(message.role)}</strong>
          <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
          <button
            className="message-action"
            type="button"
            aria-label="Copy message"
            title="Copy message"
            onClick={() => void onCopy(message.task_id, message.content)}
          >
            <Copy size={13} />
          </button>
          <VoiceSpeakControl message={message} />
        </div>
        <MessageContent
          content={message.content}
          taskId={message.task_id}
          onCopy={onCopy}
          onOpenLink={onOpenLink}
        />
        {message.role === "assistant" ? (
          <EvidenceSources
            taskId={message.task_id}
            sources={[...evidenceSources]}
            onOpenLink={onOpenLink}
          />
        ) : null}
        {developerMode ? (
          <div className="message-developer-meta">
            seq {message.sequence} | task {message.task_id} | turn {message.turn_id ?? "none"}
          </div>
        ) : null}
      </div>
    </m.article>
  );
}

function PendingMessageRow({
  message,
  onRetry,
  onEdit,
  onDelete,
}: {
  message: OptimisticUserMessage;
  onRetry(): Promise<void>;
  onEdit(): void;
  onDelete(): void;
}) {
  return (
    <m.article initial={{ opacity: 0.4, y: 6 }} animate={{ opacity: 1, y: 0 }} className={`message-row message-user message-pending message-${message.status}`}>
      <div className="message-avatar" aria-hidden="true">
        {message.status === "sending" ? <LoaderCircle className="spin" size={16} /> : <CircleUserRound size={16} />}
      </div>
      <div className="message-content">
        <div className="message-meta">
          <strong>You</strong>
          <span>{message.status === "sending" ? "Sending" : "Not sent"}</span>
        </div>
        <div className="message-markdown"><p>{message.content}</p></div>
        {message.attachmentCount > 0 ? (
          <span className="pending-attachments">{message.attachmentCount} attachment(s)</span>
        ) : null}
        {message.status === "failed" ? (
          <div className="pending-actions" role="group" aria-label="Failed message actions">
            <span>{message.error}</span>
            <button type="button" onClick={() => void onRetry()}><RotateCcw size={13} /> Retry</button>
            <button type="button" onClick={onEdit}><Pencil size={13} /> Edit</button>
            <button type="button" onClick={onDelete}><Trash2 size={13} /> Delete</button>
          </div>
        ) : null}
      </div>
    </m.article>
  );
}

function scrollToLatest(
  list: HTMLDivElement | null,
  end: HTMLDivElement | null,
  smooth: boolean,
) {
  if (list !== null && typeof list.scrollTo === "function") {
    list.scrollTo({ top: list.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    return;
  }
  end?.scrollIntoView?.({ block: "end" });
}

function scrollToAnchor(
  list: HTMLDivElement,
  anchor: HTMLElement,
  smooth: boolean,
) {
  const top = Math.max(
    0,
    anchor.getBoundingClientRect().top -
      list.getBoundingClientRect().top +
      list.scrollTop -
      18,
  );
  if (typeof list.scrollTo === "function") {
    list.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
  } else {
    list.scrollTop = top;
  }
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

const unavailableScheduleAction = async () => {
  throw new Error("Schedule controls are unavailable");
};

const UNAVAILABLE_SCHEDULE_ACTIONS: ScheduleCardActions = {
  update: unavailableScheduleAction,
  pause: unavailableScheduleAction,
  resume: unavailableScheduleAction,
  runNow: unavailableScheduleAction,
  cancel: unavailableScheduleAction,
};
