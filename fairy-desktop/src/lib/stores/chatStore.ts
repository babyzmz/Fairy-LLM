import { useSyncExternalStore } from "react";

import { ApiRequestError } from "../api/client";
import { getCapabilities, getHealth, invokeChat, streamChat } from "../api/chat";
import { getSystemState, performSystemAction } from "../api/system";
import type {
  ApiErrorModel,
  CapabilitiesResponse,
  CardStreamEvent,
  CardUnion,
  ChatInvokeResponse,
  ChatStreamEvent,
  DesktopSystemState,
  ErrorStreamEvent,
  MessageEndStreamEvent,
  MessageStartStreamEvent,
  ProgressStreamEvent,
  SystemActionResponse,
  TextDeltaStreamEvent,
} from "../types/api";

export type BackendStatus = "unknown" | "online" | "offline";

interface BaseMessage {
  id: string;
  createdAt: number;
}

export interface UserMessage extends BaseMessage {
  kind: "user";
  text: string;
}

export interface AssistantPartialMessage extends BaseMessage {
  kind: "assistant_partial";
  requestId: string;
  text: string;
  cards: CardUnion[];
  progressText: string;
}

export interface AssistantFinalMessage extends BaseMessage {
  kind: "assistant_final";
  requestId: string;
  text: string;
  cards: CardUnion[];
  errors: ApiErrorModel[];
  meta?: Record<string, unknown>;
}

export interface ErrorMessage extends BaseMessage {
  kind: "error";
  text: string;
  errors: ApiErrorModel[];
  requestId?: string;
}

export type ChatUiMessage = UserMessage | AssistantPartialMessage | AssistantFinalMessage | ErrorMessage;

export interface StreamTimelineEntry {
  id: string;
  requestId: string;
  sessionId: string;
  event: string;
  sequence: number;
  timestampMs: number;
  summary: string;
  detail?: string;
}

interface ChatState {
  sessionId: string;
  backendStatus: BackendStatus;
  capabilities: CapabilitiesResponse | null;
  systemState: DesktopSystemState | null;
  systemPanelError: string;
  backendControlBusy: boolean;
  systemPanelOpen: boolean;
  desktopNotification: string;
  messages: ChatUiMessage[];
  isSending: boolean;
  isStreaming: boolean;
  streamRequestId: string;
  partialAssistantText: string;
  streamedCards: CardUnion[];
  streamError: string;
  didFallbackToInvoke: boolean;
  wasCancelled: boolean;
  streamTimeline: StreamTimelineEntry[];
  lastError: string;
}

class ChatStore {
  private state: ChatState = {
    sessionId: "default",
    backendStatus: "unknown",
    capabilities: null,
    systemState: null,
    systemPanelError: "",
    backendControlBusy: false,
    systemPanelOpen: false,
    desktopNotification: "",
    messages: [],
    isSending: false,
    isStreaming: false,
    streamRequestId: "",
    partialAssistantText: "",
    streamedCards: [],
    streamError: "",
    didFallbackToInvoke: false,
    wasCancelled: false,
    streamTimeline: [],
    lastError: "",
  };

  private readonly listeners = new Set<() => void>();
  private streamAbortController: AbortController | null = null;
  private partialMessageId = "";

  getState(): ChatState {
    return this.state;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private setState(patch: Partial<ChatState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) {
      listener();
    }
  }

  private appendMessage(message: ChatUiMessage): void {
    this.setState({ messages: [...this.state.messages, message] });
  }

  private appendTimeline(entry: Omit<StreamTimelineEntry, "id">): void {
    const item: StreamTimelineEntry = { id: `${entry.requestId || "timeline"}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`, ...entry };
    const nextTimeline = [...this.state.streamTimeline, item].slice(-250);
    this.setState({ streamTimeline: nextTimeline });
  }

  private replaceMessage(messageId: string, updater: (message: ChatUiMessage) => ChatUiMessage): void {
    this.setState({
      messages: this.state.messages.map((message) => (message.id === messageId ? updater(message) : message)),
    });
  }

  async probeBackend(): Promise<void> {
    try {
      await getHealth();
      const capabilities = await getCapabilities();
      this.setState({ backendStatus: "online", capabilities, lastError: "", streamError: "" });
      await this.loadSystemState();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Backend unavailable.";
      this.setState({ backendStatus: "offline", lastError: message, capabilities: null, systemState: null });
    }
  }

  async loadSystemState(): Promise<void> {
    try {
      const systemState = await getSystemState();
      this.setState({
        systemState,
        systemPanelError: "",
        backendStatus:
          systemState.bridge_status === "ready" || systemState.bridge_status === "reused" ? "online" : this.state.backendStatus,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load system state.";
      this.setState({ systemPanelError: message });
    }
  }

  async runSystemAction(action: string): Promise<SystemActionResponse | null> {
    this.setState({ backendControlBusy: true, systemPanelError: "" });
    try {
      const response = await performSystemAction(action, {
        request_id: this.state.streamRequestId || undefined,
      });
      this.appendTimeline({
        requestId: this.state.streamRequestId,
        sessionId: this.state.sessionId,
        event: "system_action",
        sequence: 0,
        timestampMs: Date.now(),
        summary: `system action: ${action}`,
        detail: response.message,
      });
      if (action === "open_panel" && response.ok) {
        this.setState({ systemPanelOpen: true });
      }
      if (response.system_state) {
        this.setState({ systemState: response.system_state });
      } else {
        await this.loadSystemState();
      }
      if (!response.ok) {
        this.setState({ systemPanelError: response.message || "System action failed." });
      }
      return response;
    } catch (error) {
      const message = error instanceof Error ? error.message : "System action failed.";
      this.setState({ systemPanelError: message });
      return null;
    } finally {
      this.setState({ backendControlBusy: false });
    }
  }

  markBackendOffline(message: string): void {
    this.appendTimeline({
      requestId: "",
      sessionId: this.state.sessionId,
      event: "backend_status",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "backend offline",
      detail: message,
    });
    this.setState({ backendStatus: "offline", lastError: message, systemState: null });
  }

  recordBackendLifecycle(summary: string, detail = ""): void {
    this.appendTimeline({
      requestId: "",
      sessionId: this.state.sessionId,
      event: "backend_status",
      sequence: 0,
      timestampMs: Date.now(),
      summary,
      detail,
    });
    void this.loadSystemState();
  }

  setSystemPanelOpen(open: boolean): void {
    this.setState({ systemPanelOpen: open });
  }

  showDesktopNotification(message: string): void {
    this.setState({ desktopNotification: message });
    this.appendTimeline({
      requestId: this.state.streamRequestId,
      sessionId: this.state.sessionId,
      event: "desktop_notification",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "desktop notification",
      detail: message,
    });
  }

  clearDesktopNotification(): void {
    this.setState({ desktopNotification: "" });
  }

  async sendMessage(input: string): Promise<void> {
    const message = input.trim();
    if (!message) {
      return;
    }
    if (this.state.isStreaming) {
      this.interruptStreaming("Interrupted by a new request.");
    }
    if (this.state.isSending) {
      return;
    }

    const createdAt = Date.now();
    this.appendMessage({
      id: `user-${createdAt}`,
      kind: "user",
      text: message,
      createdAt,
    });

    this.setState({
      isSending: true,
      lastError: "",
      streamError: "",
      partialAssistantText: "",
      streamedCards: [],
      didFallbackToInvoke: false,
      wasCancelled: false,
    });

    const controller = new AbortController();
    this.streamAbortController = controller;
    let receivedStreamEvent = false;

    try {
      await streamChat(
        {
          message,
          session_id: this.state.sessionId,
          attachments: null,
        },
        {
          signal: controller.signal,
          onEvent: (event) => {
            receivedStreamEvent = true;
            this.handleStreamEvent(event);
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      if (!receivedStreamEvent) {
        await this.fallbackInvoke(message);
      } else {
        const errorText = error instanceof Error ? error.message : "Streaming request failed.";
        this.handleStreamFailure(errorText);
      }
    } finally {
      if (this.streamAbortController === controller) {
        this.streamAbortController = null;
      }
      this.setState({ isSending: false });
    }
  }

  interruptStreaming(reason = "Streaming interrupted."): void {
    if (this.streamAbortController) {
      this.streamAbortController.abort();
      this.streamAbortController = null;
    }
    if (this.partialMessageId) {
      const currentText = this.state.partialAssistantText;
      const currentCards = [...this.state.streamedCards];
      this.replaceMessage(this.partialMessageId, (message) => {
        if (message.kind !== "assistant_partial") {
          return message;
        }
        return {
          id: message.id,
          kind: "assistant_final",
          requestId: message.requestId,
          text: currentText,
          cards: currentCards,
          errors: [{ code: "interrupted", message: reason }],
          meta: { interrupted: true },
          createdAt: message.createdAt,
        };
      });
    } else {
      this.appendMessage({
        id: `error-${Date.now()}`,
        kind: "error",
        text: reason,
        errors: [{ code: "interrupted", message: reason }],
        createdAt: Date.now(),
      });
    }
    this.appendTimeline({
      requestId: this.state.streamRequestId,
      sessionId: this.state.sessionId,
      event: "cancelled",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "stream cancelled",
      detail: reason,
    });
    this.setState({ wasCancelled: true });
    this.clearStreamState(reason);
    void this.loadSystemState();
  }

  private async fallbackInvoke(message: string): Promise<void> {
    this.setState({ didFallbackToInvoke: true });
    this.appendTimeline({
      requestId: this.state.streamRequestId,
      sessionId: this.state.sessionId,
      event: "invoke_fallback",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "fallback to /chat/invoke",
      detail: "No stream event was received.",
    });
    try {
      const response = await invokeChat({
        message,
        session_id: this.state.sessionId,
        attachments: null,
      });
      this.pushInvokeResponse(response);
      this.appendTimeline({
        requestId: response.request_id,
        sessionId: response.session_id,
        event: "invoke_response",
        sequence: 0,
        timestampMs: Date.now(),
        summary: "invoke response received",
        detail: `${response.cards.length} card(s), ${response.errors.length} error(s)`,
      });
      this.setState({
        backendStatus: "online",
        lastError: response.errors[0]?.message ?? "",
        streamError: "",
      });
    } catch (error) {
      const messageText =
        error instanceof ApiRequestError || error instanceof Error ? error.message : "Request failed.";
      const backendStatus: BackendStatus = error instanceof ApiRequestError ? "online" : "offline";
      this.appendMessage({
        id: `error-${Date.now()}`,
        kind: "error",
        text: messageText,
        errors: [{ code: "request_failed", message: messageText }],
        createdAt: Date.now(),
      });
      this.setState({ backendStatus, lastError: messageText, streamError: messageText });
    }
  }

  private handleStreamEvent(event: ChatStreamEvent): void {
    switch (event.event) {
      case "message_start":
        this.handleMessageStart(event);
        return;
      case "progress":
        this.handleProgress(event);
        return;
      case "text_delta":
        this.handleTextDelta(event);
        return;
      case "card":
        this.handleCard(event);
        return;
      case "message_end":
        this.handleMessageEnd(event);
        return;
      case "error":
        this.handleStreamError(event);
        return;
      default:
        return;
    }
  }

  private handleMessageStart(event: MessageStartStreamEvent): void {
    const messageId = `assistant-${event.request_id}`;
    this.partialMessageId = messageId;
    this.appendMessage({
      id: messageId,
      kind: "assistant_partial",
      requestId: event.request_id,
      text: "",
      cards: [],
      progressText: "",
      createdAt: Date.now(),
    });
    this.setState({
      backendStatus: "online",
      isStreaming: true,
      streamRequestId: event.request_id,
      partialAssistantText: "",
      streamedCards: [],
      streamError: "",
      lastError: "",
    });
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary: "message started",
      detail: JSON.stringify(event.meta),
    });
    void this.loadSystemState();
  }

  private handleProgress(event: ProgressStreamEvent): void {
    if (!this.partialMessageId) {
      return;
    }
    this.replaceMessage(this.partialMessageId, (message) => {
      if (message.kind !== "assistant_partial") {
        return message;
      }
      return { ...message, progressText: event.text };
    });
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary:
        event.stage === "desktop_action_dispatch"
          ? "desktop_action_dispatch"
          : event.stage === "desktop_action_result"
            ? "desktop_action_result"
            : event.stage === "legacy_surface_dispatch" || event.stage === "legacy_surface_result"
              ? "Legacy Desktop Action"
            : event.stage.startsWith("resolution") ||
                event.stage === "contract_loaded" ||
                event.stage === "candidate_generated" ||
                event.stage === "semantic_consistency_checked" ||
                event.stage === "candidate_rejected" ||
                event.stage === "capability_arbitrated" ||
                event.stage === "arbitration_complete" ||
                event.stage === "slot_extracted" ||
                event.stage === "slot_normalized" ||
                event.stage === "slot_validated" ||
                event.stage === "slot_validation_failed" ||
                event.stage === "slot_default_applied" ||
                event.stage === "carryover_applied" ||
                event.stage === "carryover_rejected" ||
                event.stage === "missing_required_slot" ||
                event.stage === "clarification_required"
              ? event.stage
              : `progress: ${event.stage}`,
      detail: event.text,
    });
  }

  private handleTextDelta(event: TextDeltaStreamEvent): void {
    if (!this.partialMessageId) {
      this.handleMessageStart({
        event: "message_start",
        request_id: event.request_id,
        session_id: event.session_id,
        sequence: 0,
        timestamp_ms: Date.now(),
        meta: {},
      });
    }
    const nextText = `${this.state.partialAssistantText}${event.text}`;
    this.setState({ partialAssistantText: nextText, isStreaming: true, streamRequestId: event.request_id });
    if (this.partialMessageId) {
      this.replaceMessage(this.partialMessageId, (message) => {
        if (message.kind !== "assistant_partial") {
          return message;
        }
        return { ...message, text: nextText };
      });
    }
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary: "text delta",
      detail: event.text,
    });
  }

  private handleCard(event: CardStreamEvent): void {
    if (!this.partialMessageId) {
      this.handleMessageStart({
        event: "message_start",
        request_id: event.request_id,
        session_id: event.session_id,
        sequence: 0,
        timestamp_ms: Date.now(),
        meta: {},
      });
    }
    const nextCards = [...this.state.streamedCards, event.card];
    this.setState({ streamedCards: nextCards, isStreaming: true, streamRequestId: event.request_id });
    if (this.partialMessageId) {
      this.replaceMessage(this.partialMessageId, (message) => {
        if (message.kind !== "assistant_partial") {
          return message;
        }
        return { ...message, cards: nextCards };
      });
    }
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary: `card: ${event.card.type}`,
      detail: event.card.layout,
    });
  }

  private handleMessageEnd(event: MessageEndStreamEvent): void {
    const finalText = event.text || this.state.partialAssistantText;
    const finalCards = event.cards.length > 0 ? event.cards : this.state.streamedCards;
    const runtimeMeta = (event.meta?.runtime as Record<string, unknown> | undefined) ?? {};
    const desktopBridgeResult = (runtimeMeta.desktop_bridge_result as Record<string, unknown> | undefined) ?? undefined;
    if (
      !this.partialMessageId &&
      this.state.messages.some(
        (message) =>
          message.kind === "assistant_final" &&
          message.requestId === event.request_id,
      )
    ) {
      this.clearStreamState(event.errors[0]?.message ?? "");
      return;
    }
    if (this.partialMessageId) {
      this.replaceMessage(this.partialMessageId, (message) => {
        if (message.kind !== "assistant_partial") {
          return message;
        }
        return {
          id: message.id,
          kind: "assistant_final",
          requestId: event.request_id,
          text: finalText,
          cards: finalCards,
          errors: event.errors,
          meta: event.meta,
          createdAt: message.createdAt,
        };
      });
    } else {
      this.appendMessage({
        id: `assistant-${event.request_id}`,
        kind: "assistant_final",
        requestId: event.request_id,
        text: finalText,
        cards: finalCards,
        errors: event.errors,
        meta: event.meta,
        createdAt: Date.now(),
      });
    }
    this.setState({
      backendStatus: "online",
      lastError: event.errors[0]?.message ?? "",
    });
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary: "message completed",
      detail: `${finalCards.length} card(s), ${event.errors.length} error(s)`,
    });
    if (desktopBridgeResult && Object.keys(desktopBridgeResult).length > 0) {
      this.appendTimeline({
        requestId: event.request_id,
        sessionId: event.session_id,
        event: "desktop_action_result",
        sequence: event.sequence,
        timestampMs: event.timestamp_ms,
        summary: "desktop_action_result",
        detail: JSON.stringify(desktopBridgeResult),
      });
    }
    if (runtimeMeta.legacy_surface_automation === true) {
      this.appendTimeline({
        requestId: event.request_id,
        sessionId: event.session_id,
        event: "legacy_surface",
        sequence: event.sequence,
        timestampMs: event.timestamp_ms,
        summary: "Legacy Desktop Action",
        detail: "This request touched the isolated legacy surface automation path.",
      });
    }
    this.clearStreamState(event.errors[0]?.message ?? "");
    void this.loadSystemState();
  }

  private handleStreamError(event: ErrorStreamEvent): void {
    if (this.partialMessageId) {
      this.replaceMessage(this.partialMessageId, (message) => {
        if (message.kind !== "assistant_partial") {
          return message;
        }
        return {
          id: message.id,
          kind: "assistant_final",
          requestId: event.request_id,
          text: message.text,
          cards: message.cards,
          errors: [{ code: event.code, message: event.message }],
          meta: { stream_error: true },
          createdAt: message.createdAt,
        };
      });
    } else {
      this.appendMessage({
        id: `error-${Date.now()}`,
        kind: "error",
        text: event.message,
        errors: [{ code: event.code, message: event.message }],
        requestId: event.request_id,
        createdAt: Date.now(),
      });
    }
    this.setState({
      backendStatus: "online",
      lastError: event.message,
      streamError: event.message,
    });
    this.appendTimeline({
      requestId: event.request_id,
      sessionId: event.session_id,
      event: event.event,
      sequence: event.sequence,
      timestampMs: event.timestamp_ms,
      summary: `error: ${event.code}`,
      detail: event.message,
    });
    this.clearStreamState(event.message);
    void this.loadSystemState();
  }

  private handleStreamFailure(message: string): void {
    this.appendMessage({
      id: `error-${Date.now()}`,
      kind: "error",
      text: message,
      errors: [{ code: "stream_failed", message }],
      requestId: this.state.streamRequestId || undefined,
      createdAt: Date.now(),
    });
    this.appendTimeline({
      requestId: this.state.streamRequestId,
      sessionId: this.state.sessionId,
      event: "stream_failure",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "stream failed",
      detail: message,
    });
    this.setState({ backendStatus: "online", lastError: message, streamError: message });
    this.clearStreamState(message);
  }

  private pushInvokeResponse(response: ChatInvokeResponse): void {
    const responseText = response.text.trim();
    const hasContent = Boolean(responseText) || response.cards.length > 0 || response.errors.length > 0;
    if (!hasContent) {
      this.appendMessage({
        id: `error-${Date.now()}`,
        kind: "error",
        text: "Runtime returned an empty response.",
        errors: [{ code: "empty_response", message: "Runtime returned an empty response." }],
        requestId: response.request_id,
        createdAt: Date.now(),
      });
      return;
    }
    this.appendMessage({
      id: `assistant-${response.request_id || Date.now()}`,
      kind: "assistant_final",
      requestId: response.request_id,
      text: responseText,
      cards: response.cards,
      errors: response.errors,
      meta: response.meta,
      createdAt: Date.now(),
    });
    this.appendTimeline({
      requestId: response.request_id,
      sessionId: response.session_id,
      event: "invoke_message",
      sequence: 0,
      timestampMs: Date.now(),
      summary: "invoke response rendered",
      detail: `${response.cards.length} card(s), ${response.errors.length} error(s)`,
    });
  }

  private clearStreamState(lastError: string): void {
    this.partialMessageId = "";
    this.setState({
      isStreaming: false,
      streamRequestId: "",
      partialAssistantText: "",
      streamedCards: [],
      streamError: lastError,
    });
  }
}

export const chatStore = new ChatStore();

export const chatActions = {
  probeBackend: (): Promise<void> => chatStore.probeBackend(),
  loadSystemState: (): Promise<void> => chatStore.loadSystemState(),
  runSystemAction: (action: string): Promise<SystemActionResponse | null> => chatStore.runSystemAction(action),
  sendMessage: (message: string): Promise<void> => chatStore.sendMessage(message),
  interruptStreaming: (reason?: string): void => chatStore.interruptStreaming(reason),
  markBackendOffline: (message: string): void => chatStore.markBackendOffline(message),
  recordBackendLifecycle: (summary: string, detail?: string): void => chatStore.recordBackendLifecycle(summary, detail),
  setSystemPanelOpen: (open: boolean): void => chatStore.setSystemPanelOpen(open),
  showDesktopNotification: (message: string): void => chatStore.showDesktopNotification(message),
  clearDesktopNotification: (): void => chatStore.clearDesktopNotification(),
};

export function useChatStore<T>(selector: (state: ChatState) => T): T {
  return useSyncExternalStore(
    chatStore.subscribe,
    () => selector(chatStore.getState()),
    () => selector(chatStore.getState()),
  );
}
