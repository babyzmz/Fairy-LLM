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
  SystemStateResponse,
  SystemActionResponse,
  TextDeltaStreamEvent,
} from "../types/api";

export type BackendStatus = "unknown" | "online" | "offline";

interface BaseMessage {
  id: string;
  createdAt: number;
}

export interface ChatAttachment {
  path: string;
  name: string;
}

export interface UserMessage extends BaseMessage {
  kind: "user";
  text: string;
  attachments?: ChatAttachment[];
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

export interface ResolutionDebugState {
  locationSource: string;
  matchedRules: string[];
  arbitrationCorrectionApplied: boolean;
  lastResolutionStage: string;
  lastResolutionCapability: string;
  lastResolutionSummary: string;
}

export interface WebAccessDebugState {
  originalQuery: string;
  resolvedQuery: string;
  effectiveQuery: string;
  queryAuthority: string;
  queryMutationReason: string;
  selectedCapability: string;
  accessMode: string;
  intentType: string;
  sourceDomain: string;
  preferredDomains: string[];
  queryStrategy: string;
  sourceConstraintApplied: boolean;
  topicCarryoverApplied: boolean;
  topicCarryoverReason: string;
  memoryContextApplied: boolean;
  memoryUsageType: string;
  dbContextApplied: boolean;
  continuationApplied: boolean;
  continuationReason: string;
  continuationStrategy: string;
  continuationOfRequestId: string;
  retrievalPlanSummary: string;
  executionLevelReached: string;
  browserInteractionUsed: boolean;
  visualReadUsed: boolean;
  browserAvailable: boolean;
  browserAvailabilityLevel: string;
  browserAvailabilityReason: string;
  browserFallbackMode: string;
  browserExecutablePath: string;
  browserType: string;
  browserBackend: string;
  browserAttachOrigin: string;
  browserLastSmokeResult: string;
  browserLastLaunchError: string;
  postOpenExtractAttempted: boolean;
  postOpenExtractResult: string;
  postOpenExtractFailureReason: string;
  extractionProfile: string;
  renderedItemCount: string;
  pageContextAvailable: boolean;
  screenshotTaken: boolean;
  visualTargetRegion: string;
  visualFailureReason: string;
  browseModeUsed: boolean;
  taskType: string;
  navigationHops: string;
  selectedLinks: string[];
  scoreReasons: string[];
  finalPageType: string;
  stopReason: string;
  stopDetail: string;
  finalPageUrl: string;
  fallbackStage: string;
  failureReason: string;
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
  resolutionDebug: ResolutionDebugState;
  webAccessDebug: WebAccessDebugState;
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
    resolutionDebug: {
      locationSource: "",
      matchedRules: [],
      arbitrationCorrectionApplied: false,
      lastResolutionStage: "",
      lastResolutionCapability: "",
      lastResolutionSummary: "",
    },
    webAccessDebug: {
      originalQuery: "",
      resolvedQuery: "",
      effectiveQuery: "",
      queryAuthority: "",
      queryMutationReason: "",
      selectedCapability: "",
      accessMode: "",
      intentType: "",
      sourceDomain: "",
      preferredDomains: [],
      queryStrategy: "",
      sourceConstraintApplied: false,
      topicCarryoverApplied: false,
      topicCarryoverReason: "",
      memoryContextApplied: false,
      memoryUsageType: "",
      dbContextApplied: false,
      continuationApplied: false,
      continuationReason: "",
      continuationStrategy: "",
      continuationOfRequestId: "",
      retrievalPlanSummary: "",
      executionLevelReached: "",
      browserInteractionUsed: false,
      visualReadUsed: false,
      browserAvailable: false,
      browserAvailabilityLevel: "",
      browserAvailabilityReason: "",
      browserFallbackMode: "",
      browserExecutablePath: "",
      browserType: "",
      browserBackend: "",
      browserAttachOrigin: "",
      browserLastSmokeResult: "",
      browserLastLaunchError: "",
      postOpenExtractAttempted: false,
      postOpenExtractResult: "",
      postOpenExtractFailureReason: "",
      extractionProfile: "",
      renderedItemCount: "",
      pageContextAvailable: false,
      screenshotTaken: false,
      visualTargetRegion: "",
      visualFailureReason: "",
      browseModeUsed: false,
      taskType: "",
      navigationHops: "",
      selectedLinks: [],
      scoreReasons: [],
      finalPageType: "",
      stopReason: "",
      stopDetail: "",
      finalPageUrl: "",
      fallbackStage: "",
      failureReason: "",
    },
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

  private firstString(...values: unknown[]): string {
    for (const value of values) {
      if (typeof value !== "string") {
        continue;
      }
      const text = value.trim();
      if (text) {
        return text;
      }
    }
    return "";
  }

  private toStringList(value: unknown): string[] {
    if (!Array.isArray(value)) {
      return [];
    }
    return value
      .map((item) => (typeof item === "string" ? item.trim() : ""))
      .filter((item) => item.length > 0);
  }

  private extractLocationSourceFromCards(cards: CardUnion[] | undefined): string {
    if (!Array.isArray(cards)) {
      return "";
    }
    for (const card of cards) {
      if (!card || card.type !== "weather") {
        continue;
      }
      const data = card.data as Record<string, unknown>;
      const locationSource = this.firstString(data.location_source, data.locationSource);
      if (locationSource) {
        return locationSource;
      }
    }
    return "";
  }

  private updateResolutionDebug(
    meta: Record<string, unknown> | undefined,
    fallbackSummary = "",
    fallbackStage = "",
    cards?: CardUnion[],
  ): void {
    if (!meta) {
      return;
    }
    const resolution = (meta.resolution as Record<string, unknown> | undefined) ?? undefined;
    if (!resolution) {
      return;
    }
    const slotSources = (resolution.slot_sources as Record<string, unknown> | undefined) ?? {};
    const trace = Array.isArray(resolution.trace) ? resolution.trace : [];
    const traceStages = trace
      .map((item) => (item && typeof item === "object" ? String((item as Record<string, unknown>).stage || "").trim() : ""))
      .filter((item) => item.length > 0);
    const matchedRules = this.toStringList(resolution.matched_rules);
    const locationSource = this.firstString(
      resolution.location_source,
      slotSources.location,
      slotSources.weather_location,
      this.extractLocationSourceFromCards(cards),
      (meta.runtime as Record<string, unknown> | undefined)?.location_source,
    );
    const lastResolutionStage = this.firstString(traceStages[traceStages.length - 1], fallbackStage);
    const lastResolutionCapability = this.firstString(
      resolution.final_capability_decision,
      resolution.selected_capability,
      resolution.capability,
    );
    const summary = this.firstString(
      resolution.clarification_message,
      resolution.normalized_query,
      fallbackSummary,
    );
    const arbitrationCorrectionApplied = Boolean(resolution.arbitration_correction_applied);
    const previous = this.state.resolutionDebug;
    this.setState({
      resolutionDebug: {
        locationSource: locationSource || previous.locationSource,
        matchedRules: matchedRules.length > 0 ? matchedRules : previous.matchedRules,
        arbitrationCorrectionApplied: arbitrationCorrectionApplied || previous.arbitrationCorrectionApplied,
        lastResolutionStage: lastResolutionStage || previous.lastResolutionStage,
        lastResolutionCapability: lastResolutionCapability || previous.lastResolutionCapability,
        lastResolutionSummary: summary || previous.lastResolutionSummary,
      },
    });
  }

  private updateWebAccessDebug(meta: Record<string, unknown> | undefined): void {
    const emptyState: WebAccessDebugState = {
      originalQuery: "",
      resolvedQuery: "",
      effectiveQuery: "",
      queryAuthority: "",
      queryMutationReason: "",
      selectedCapability: "",
      accessMode: "",
      intentType: "",
      sourceDomain: "",
      preferredDomains: [],
      queryStrategy: "",
      sourceConstraintApplied: false,
      topicCarryoverApplied: false,
      topicCarryoverReason: "",
      memoryContextApplied: false,
      memoryUsageType: "",
      dbContextApplied: false,
      continuationApplied: false,
      continuationReason: "",
      continuationStrategy: "",
      continuationOfRequestId: "",
      retrievalPlanSummary: "",
      executionLevelReached: "",
      browserInteractionUsed: false,
      visualReadUsed: false,
      browserAvailable: false,
      browserAvailabilityLevel: "",
      browserAvailabilityReason: "",
      browserFallbackMode: "",
      browserExecutablePath: "",
      browserType: "",
      browserBackend: "",
      browserAttachOrigin: "",
      browserLastSmokeResult: "",
      browserLastLaunchError: "",
      postOpenExtractAttempted: false,
      postOpenExtractResult: "",
      postOpenExtractFailureReason: "",
      extractionProfile: "",
      renderedItemCount: "",
      pageContextAvailable: false,
      screenshotTaken: false,
      visualTargetRegion: "",
      visualFailureReason: "",
      browseModeUsed: false,
      taskType: "",
      navigationHops: "",
      selectedLinks: [],
      scoreReasons: [],
      finalPageType: "",
      stopReason: "",
      stopDetail: "",
      finalPageUrl: "",
      fallbackStage: "",
      failureReason: "",
    };
    if (!meta) {
      this.setState({ webAccessDebug: emptyState });
      return;
    }
    const runtime = (meta.runtime as Record<string, unknown> | undefined) ?? {};
    const queryDebug = (runtime.query_debug as Record<string, unknown> | undefined) ?? {};
    const webAccess = (runtime.web_access as Record<string, unknown> | undefined) ?? undefined;
    if (!webAccess && !queryDebug) {
      this.setState({ webAccessDebug: emptyState });
      return;
    }
    const summary = (webAccess?.retrieval_plan_summary as Record<string, unknown> | undefined) ?? {};
    const preferredDomains = this.toStringList(webAccess?.preferred_domains);
    const summaryParts = [
      this.toStringList(summary.primary_queries).join(" | "),
      this.toStringList(summary.target_urls).join(" | "),
      this.toStringList(summary.browser_actions).join(" -> "),
      this.toStringList(summary.visual_targets).join(", "),
    ].filter((item) => item.length > 0);
    this.setState({
      webAccessDebug: {
        originalQuery: this.firstString(queryDebug.raw_query, webAccess?.original_query),
        resolvedQuery: this.firstString(queryDebug.resolved_query, webAccess?.resolved_query),
        effectiveQuery: this.firstString(queryDebug.effective_query, webAccess?.effective_query),
        queryAuthority: this.firstString(queryDebug.query_authority),
        queryMutationReason: this.firstString(queryDebug.query_mutation_reason),
        selectedCapability: this.firstString(queryDebug.selected_capability),
        accessMode: this.firstString(webAccess?.selected_access_mode),
        intentType: this.firstString(webAccess?.intent_type),
        sourceDomain: this.firstString(webAccess?.source_domain),
        preferredDomains,
        queryStrategy: this.firstString(webAccess?.query_strategy),
        sourceConstraintApplied: Boolean(queryDebug.source_constraint_applied ?? webAccess?.source_constraint_applied),
        topicCarryoverApplied: Boolean(queryDebug.topic_carryover_applied ?? webAccess?.topic_carryover_applied),
        topicCarryoverReason: this.firstString(queryDebug.topic_carryover_reason, webAccess?.topic_carryover_reason),
        memoryContextApplied: Boolean(queryDebug.memory_context_applied),
        memoryUsageType: this.firstString(queryDebug.memory_usage_type),
        dbContextApplied: Boolean(queryDebug.db_context_applied),
        continuationApplied: Boolean(queryDebug.continuation_applied ?? webAccess?.continuation_applied),
        continuationReason: this.firstString(queryDebug.continuation_reason, webAccess?.continuation_reason),
        continuationStrategy: this.firstString(queryDebug.continuation_strategy, webAccess?.continuation_strategy),
        continuationOfRequestId: this.firstString(queryDebug.continuation_of_request_id, webAccess?.continuation_of_request_id),
        retrievalPlanSummary: summaryParts.join(" / "),
        executionLevelReached: this.firstString(String(webAccess?.execution_level_reached ?? "")),
        browserInteractionUsed: Boolean(webAccess?.browser_interaction_used),
        visualReadUsed: Boolean(webAccess?.visual_read_used),
        browserAvailable: Boolean(webAccess?.browser_available),
        browserAvailabilityLevel: this.firstString(webAccess?.browser_availability_level),
        browserAvailabilityReason: this.firstString(webAccess?.browser_availability_reason),
        browserFallbackMode: this.firstString(webAccess?.browser_fallback_mode),
        browserExecutablePath: this.firstString(webAccess?.browser_executable_path),
        browserType: this.firstString(webAccess?.browser_type),
        browserBackend: this.firstString(webAccess?.browser_backend),
        browserAttachOrigin: this.firstString(webAccess?.browser_attach_origin),
        browserLastSmokeResult: this.firstString(webAccess?.browser_last_smoke_result),
        browserLastLaunchError: this.firstString(webAccess?.browser_last_launch_error),
        postOpenExtractAttempted: Boolean(webAccess?.post_open_extract_attempted),
        postOpenExtractResult: this.firstString(webAccess?.post_open_extract_result),
        postOpenExtractFailureReason: this.firstString(webAccess?.post_open_extract_failure_reason),
        extractionProfile: this.firstString(webAccess?.extraction_profile),
        renderedItemCount: this.firstString(String(webAccess?.rendered_item_count ?? "")),
        pageContextAvailable: Boolean(queryDebug.page_context_available ?? webAccess?.page_context_available),
        screenshotTaken: Boolean(webAccess?.screenshot_taken),
        visualTargetRegion: this.firstString(webAccess?.visual_target_region),
        visualFailureReason: this.firstString(webAccess?.visual_failure_reason),
        browseModeUsed: Boolean(webAccess?.browse_mode_used),
        taskType: this.firstString(webAccess?.task_type),
        navigationHops: this.firstString(String(webAccess?.navigation_hops ?? "")),
        selectedLinks: this.toStringList(
          Array.isArray(webAccess?.selected_links)
            ? (webAccess?.selected_links as Array<Record<string, unknown>>).map((item) =>
                this.firstString(item?.text, item?.url),
              )
            : [],
        ),
        scoreReasons: this.toStringList(
          Array.isArray(webAccess?.score_reasons)
            ? (webAccess?.score_reasons as Array<Record<string, unknown>>).flatMap((item) =>
                Array.isArray(item?.score_reasons)
                  ? [`${this.firstString(item?.text, item?.url)}: ${(item.score_reasons as unknown[]).map((v) => String(v)).join(", ")}`]
                  : [],
              )
            : [],
        ),
        finalPageType: this.firstString(webAccess?.final_page_type),
        stopReason: this.firstString(webAccess?.stop_reason),
        stopDetail: this.firstString(webAccess?.stop_detail),
        finalPageUrl: this.firstString(webAccess?.final_page_url),
        fallbackStage: this.firstString(webAccess?.fallback_stage),
        failureReason: this.firstString(webAccess?.failure_reason),
      },
    });
  }

  private replaceMessage(messageId: string, updater: (message: ChatUiMessage) => ChatUiMessage): void {
    this.setState({
      messages: this.state.messages.map((message) => (message.id === messageId ? updater(message) : message)),
    });
  }

  private mergeRuntimeState(runtimeState: SystemStateResponse | null | undefined): void {
    if (!runtimeState) {
      return;
    }
    const existing = this.state.systemState;
    const nextSystemState: DesktopSystemState = existing
      ? {
          ...existing,
          runtime_state: runtimeState,
        }
      : {
          bridge_status: this.state.backendStatus === "online" ? "ready" : "unknown",
          backend_url: "",
          bridge_message: "",
          pid: null,
          bridge_events: [],
          runtime_state: runtimeState,
        };
    this.setState({ systemState: nextSystemState });
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

  async sendMessage(input: string, attachments: ChatAttachment[] = []): Promise<void> {
    const message = input.trim();
    if (!message && attachments.length === 0) {
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
      attachments,
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
          attachments: attachments.length > 0 ? attachments.map((attachment) => attachment.path) : null,
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
        await this.fallbackInvoke(message, attachments);
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

  private async fallbackInvoke(message: string, attachments: ChatAttachment[]): Promise<void> {
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
        attachments: attachments.length > 0 ? attachments.map((attachment) => attachment.path) : null,
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
    this.mergeRuntimeState((event.meta?.runtime_state as SystemStateResponse | undefined) ?? undefined);
    this.updateResolutionDebug(event.meta, "message_start", "message_start");
    this.updateWebAccessDebug(event.meta);
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
            : event.stage === "desktop_automation_dispatch" ||
                event.stage === "desktop_automation_result"
              ? "Desktop Automation"
            : event.stage.startsWith("resolution") ||
                event.stage === "contract_loaded" ||
                event.stage === "candidate_generated" ||
                event.stage === "rule_preclassified" ||
                event.stage === "semantic_consistency_checked" ||
                event.stage === "candidate_rejected" ||
                event.stage === "llm_arbitration_requested" ||
                event.stage === "llm_arbitration_received" ||
                event.stage === "capability_arbitrated" ||
                event.stage === "arbitration_correction_applied" ||
                event.stage === "arbitration_complete" ||
                event.stage === "web_access_decided" ||
                event.stage === "retrieval_plan_built" ||
                event.stage === "execution_started" ||
                event.stage === "understanding_request" ||
                event.stage === "opening_page" ||
                event.stage === "understanding_page" ||
                event.stage === "ranking_links" ||
                event.stage === "navigating_deeper" ||
                event.stage === "extracting_answer" ||
                event.stage === "stop_candidate_rejected" ||
                event.stage === "http_fetch_started" ||
                event.stage === "rendered_read_started" ||
                event.stage === "browser_attempted" ||
                event.stage === "browser_interaction_started" ||
                event.stage === "visual_read_started" ||
                event.stage === "fallback_applied" ||
                event.stage === "web_access_fallback_applied" ||
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
    this.mergeRuntimeState((event.meta?.runtime_state as SystemStateResponse | undefined) ?? undefined);
    this.updateResolutionDebug(event.meta, "message_end", "message_end", finalCards);
    this.updateWebAccessDebug(event.meta);
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
    if (runtimeMeta.desktop_automation_compatibility === true) {
      this.appendTimeline({
        requestId: event.request_id,
        sessionId: event.session_id,
        event: "desktop_automation",
        sequence: event.sequence,
        timestampMs: event.timestamp_ms,
        summary: "Desktop Automation",
        detail: "This request used the desktop automation compatibility path.",
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
    this.updateResolutionDebug(response.meta, "invoke_response", "invoke_response", response.cards);
    this.updateWebAccessDebug(response.meta);
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
  sendMessage: (message: string, attachments?: ChatAttachment[]): Promise<void> => chatStore.sendMessage(message, attachments),
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
