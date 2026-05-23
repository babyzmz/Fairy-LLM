import { chatActions, useChatStore } from "../../lib/stores/chatStore";
import type { FairyVoiceRuntimeDebug } from "../../components/fairy/useFairyVoiceRuntime";

function formatTimestamp(timestampMs?: number): string {
  if (!timestampMs) {
    return "--";
  }
  return new Date(timestampMs).toLocaleTimeString();
}

interface SystemPanelProps {
  voiceDebug?: FairyVoiceRuntimeDebug;
  lastAvatarMode?: string;
}

export function SystemPanel({ voiceDebug, lastAvatarMode }: SystemPanelProps): JSX.Element {
  const systemState = useChatStore((state) => state.systemState);
  const systemPanelError = useChatStore((state) => state.systemPanelError);
  const backendControlBusy = useChatStore((state) => state.backendControlBusy);
  const systemPanelOpen = useChatStore((state) => state.systemPanelOpen);
  const streamTimeline = useChatStore((state) => state.streamTimeline);
  const resolutionDebug = useChatStore((state) => state.resolutionDebug);
  const webAccessDebug = useChatStore((state) => state.webAccessDebug);
  const bridgeEvents = systemState?.bridge_events ?? [];
  const runtimeEvents = systemState?.runtime_state?.recent_events ?? [];
  const latestStreamEntry = streamTimeline[streamTimeline.length - 1] ?? null;
  const recentEvents = [
    ...bridgeEvents.map((event) => ({
      id: `bridge-${event.timestamp_ms}-${event.status}`,
      timestampMs: event.timestamp_ms,
      label: `bridge:${event.status}`,
      detail: event.message,
    })),
    ...runtimeEvents.map((event) => ({
      id: `runtime-${event.timestamp_ms}-${event.event}`,
      timestampMs: event.timestamp_ms,
      label: `runtime:${event.event}`,
      detail: String(event.detail?.message || event.detail?.status || event.request_id || ""),
    })),
  ]
    .sort((left, right) => right.timestampMs - left.timestampMs)
    .slice(0, 12);

  return (
    <details
      className="system-panel"
      open={systemPanelOpen}
      onToggle={(event) => chatActions.setSystemPanelOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="system-panel__summary">System Panel</summary>
      <div className="system-panel__content">
        <div className="system-grid">
          <div>
            <div className="system-label">Bridge</div>
            <div className="system-value">{systemState?.bridge_status || "unknown"}</div>
          </div>
          <div>
            <div className="system-label">Runtime</div>
            <div className="system-value">{systemState?.runtime_state?.backend_status || "unknown"}</div>
          </div>
          <div>
            <div className="system-label">Assistant state</div>
            <div className="system-value">{systemState?.runtime_state?.current_state || "unknown"}</div>
          </div>
          <div>
            <div className="system-label">Avatar mode</div>
            <div className="system-value">{lastAvatarMode || "--"}</div>
          </div>
          <div>
            <div className="system-label">Streaming</div>
            <div className="system-value">{systemState?.runtime_state?.is_streaming ? "active" : "idle"}</div>
          </div>
          <div>
            <div className="system-label">Active Request</div>
            <div className="system-value">{systemState?.runtime_state?.active_stream_request || "--"}</div>
          </div>
        </div>

        <div className="system-grid">
          <div>
            <div className="system-label">Location source</div>
            <div className="system-value">{resolutionDebug.locationSource || "--"}</div>
          </div>
          <div>
            <div className="system-label">Matched rules</div>
            <div className="system-value">{resolutionDebug.matchedRules.length ? resolutionDebug.matchedRules.join(", ") : "--"}</div>
          </div>
          <div>
            <div className="system-label">Arbitration correction</div>
            <div className="system-value">{resolutionDebug.arbitrationCorrectionApplied ? "applied" : "none"}</div>
          </div>
          <div>
            <div className="system-label">Resolution stage</div>
            <div className="system-value">{resolutionDebug.lastResolutionStage || "--"}</div>
          </div>
          <div>
            <div className="system-label">Resolution capability</div>
            <div className="system-value">{resolutionDebug.lastResolutionCapability || "--"}</div>
          </div>
          <div>
            <div className="system-label">Resolution summary</div>
            <div className="system-value">{resolutionDebug.lastResolutionSummary || "--"}</div>
          </div>
        </div>

        <div className="system-grid">
          <div>
            <div className="system-label">Original query</div>
            <div className="system-value">{webAccessDebug.originalQuery || "--"}</div>
          </div>
          <div>
            <div className="system-label">Resolved query</div>
            <div className="system-value">{webAccessDebug.resolvedQuery || "--"}</div>
          </div>
          <div>
            <div className="system-label">Effective query</div>
            <div className="system-value">{webAccessDebug.effectiveQuery || "--"}</div>
          </div>
          <div>
            <div className="system-label">Query authority</div>
            <div className="system-value">{webAccessDebug.queryAuthority || "--"}</div>
          </div>
          <div>
            <div className="system-label">Mutation reason</div>
            <div className="system-value">{webAccessDebug.queryMutationReason || "--"}</div>
          </div>
          <div>
            <div className="system-label">Selected capability</div>
            <div className="system-value">{webAccessDebug.selectedCapability || "--"}</div>
          </div>
          <div>
            <div className="system-label">Web access mode</div>
            <div className="system-value">{webAccessDebug.accessMode || "--"}</div>
          </div>
          <div>
            <div className="system-label">Web intent</div>
            <div className="system-value">{webAccessDebug.intentType || "--"}</div>
          </div>
          <div>
            <div className="system-label">Source domain</div>
            <div className="system-value">{webAccessDebug.sourceDomain || "--"}</div>
          </div>
          <div>
            <div className="system-label">Preferred domains</div>
            <div className="system-value">
              {webAccessDebug.preferredDomains.length ? webAccessDebug.preferredDomains.join(", ") : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Query strategy</div>
            <div className="system-value">{webAccessDebug.queryStrategy || "--"}</div>
          </div>
          <div>
            <div className="system-label">Source constraint</div>
            <div className="system-value">{webAccessDebug.sourceConstraintApplied ? "applied" : "none"}</div>
          </div>
          <div>
            <div className="system-label">Topic carryover</div>
            <div className="system-value">
              {webAccessDebug.topicCarryoverApplied ? webAccessDebug.topicCarryoverReason || "applied" : "none"}
            </div>
          </div>
          <div>
            <div className="system-label">Memory context</div>
            <div className="system-value">
              {webAccessDebug.memoryContextApplied
                ? webAccessDebug.memoryUsageType || "applied"
                : webAccessDebug.dbContextApplied
                  ? "db-only"
                  : "none"}
            </div>
          </div>
          <div>
            <div className="system-label">Continuation</div>
            <div className="system-value">
              {webAccessDebug.continuationApplied
                ? `${webAccessDebug.continuationStrategy || "applied"}${webAccessDebug.continuationReason ? ` / ${webAccessDebug.continuationReason}` : ""}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Continuation req</div>
            <div className="system-value">{webAccessDebug.continuationOfRequestId || "--"}</div>
          </div>
          <div>
            <div className="system-label">Execution level</div>
            <div className="system-value">{webAccessDebug.executionLevelReached || "--"}</div>
          </div>
          <div>
            <div className="system-label">Browser / Visual</div>
            <div className="system-value">
              {webAccessDebug.browserInteractionUsed || webAccessDebug.visualReadUsed
                ? `${webAccessDebug.browserInteractionUsed ? "browser" : "--"} / ${webAccessDebug.visualReadUsed ? "visual" : "--"}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Browser availability</div>
            <div className="system-value">
              {webAccessDebug.browserAvailabilityLevel
                ? `${webAccessDebug.browserAvailable ? "available" : "unavailable"} / ${webAccessDebug.browserAvailabilityLevel}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Browser fallback</div>
            <div className="system-value">
              {webAccessDebug.browserFallbackMode || webAccessDebug.browserAvailabilityReason || "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Browser executable</div>
            <div className="system-value">{webAccessDebug.browserExecutablePath || "--"}</div>
          </div>
          <div>
            <div className="system-label">Browser type</div>
            <div className="system-value">{webAccessDebug.browserType || "--"}</div>
          </div>
          <div>
            <div className="system-label">Browser backend</div>
            <div className="system-value">{webAccessDebug.browserBackend || "--"}</div>
          </div>
          <div>
            <div className="system-label">Attach origin</div>
            <div className="system-value">{webAccessDebug.browserAttachOrigin || "--"}</div>
          </div>
          <div>
            <div className="system-label">Last browser smoke</div>
            <div className="system-value">{webAccessDebug.browserLastSmokeResult || "--"}</div>
          </div>
          <div>
            <div className="system-label">Last browser error</div>
            <div className="system-value">{webAccessDebug.browserLastLaunchError || "--"}</div>
          </div>
          <div>
            <div className="system-label">Post-open extraction</div>
            <div className="system-value">
              {webAccessDebug.postOpenExtractAttempted
                ? `${webAccessDebug.postOpenExtractResult || "attempted"}${webAccessDebug.postOpenExtractFailureReason ? ` / ${webAccessDebug.postOpenExtractFailureReason}` : ""}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Extraction profile</div>
            <div className="system-value">
              {webAccessDebug.extractionProfile
                ? `${webAccessDebug.extractionProfile}${webAccessDebug.renderedItemCount ? ` / ${webAccessDebug.renderedItemCount}` : ""}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Browse mode</div>
            <div className="system-value">{webAccessDebug.browseModeUsed ? "source_constrained_browse" : "--"}</div>
          </div>
          <div>
            <div className="system-label">Task type</div>
            <div className="system-value">{webAccessDebug.taskType || "--"}</div>
          </div>
          <div>
            <div className="system-label">Navigation hops</div>
            <div className="system-value">{webAccessDebug.navigationHops || "--"}</div>
          </div>
          <div>
            <div className="system-label">Selected links</div>
            <div className="system-value">{webAccessDebug.selectedLinks.length ? webAccessDebug.selectedLinks.join(" | ") : "--"}</div>
          </div>
          <div>
            <div className="system-label">Score reasons</div>
            <div className="system-value">{webAccessDebug.scoreReasons.length ? webAccessDebug.scoreReasons.join(" | ") : "--"}</div>
          </div>
          <div>
            <div className="system-label">Final page type</div>
            <div className="system-value">{webAccessDebug.finalPageType || "--"}</div>
          </div>
          <div>
            <div className="system-label">Stop reason</div>
            <div className="system-value">{webAccessDebug.stopReason || "--"}</div>
          </div>
          <div>
            <div className="system-label">Final page URL</div>
            <div className="system-value">{webAccessDebug.finalPageUrl || "--"}</div>
          </div>
          <div>
            <div className="system-label">Page context</div>
            <div className="system-value">{webAccessDebug.pageContextAvailable ? "available" : "--"}</div>
          </div>
          <div>
            <div className="system-label">Screenshot / region</div>
            <div className="system-value">
              {webAccessDebug.screenshotTaken || webAccessDebug.visualTargetRegion
                ? `${webAccessDebug.screenshotTaken ? "captured" : "not captured"}${webAccessDebug.visualTargetRegion ? ` / ${webAccessDebug.visualTargetRegion}` : ""}`
                : "--"}
            </div>
          </div>
          <div>
            <div className="system-label">Visual failure</div>
            <div className="system-value">{webAccessDebug.visualFailureReason || "--"}</div>
          </div>
          <div>
            <div className="system-label">Fallback stage</div>
            <div className="system-value">{webAccessDebug.fallbackStage || "--"}</div>
          </div>
          <div>
            <div className="system-label">Failure reason</div>
            <div className="system-value">{webAccessDebug.failureReason || "--"}</div>
          </div>
          <div>
            <div className="system-label">Retrieval plan</div>
            <div className="system-value">{webAccessDebug.retrievalPlanSummary || "--"}</div>
          </div>
        </div>

        {voiceDebug ? (
          <div className="system-grid">
            <div>
              <div className="system-label">Last voice event</div>
              <div className="system-value">{voiceDebug.lastVoiceEvent || "--"}</div>
            </div>
            <div>
              <div className="system-label">Runtime state (voice)</div>
              <div className="system-value">{voiceDebug.currentRuntimeState || "--"}</div>
            </div>
            <div>
              <div className="system-label">Last voice</div>
              <div className="system-value">{voiceDebug.lastVoiceKey || "--"}</div>
            </div>
            <div>
              <div className="system-label">Speaking source</div>
              <div className="system-value">{voiceDebug.currentSpeakingSource || "--"}</div>
            </div>
            <div>
              <div className="system-label">Voice queue</div>
              <div className="system-value">{voiceDebug.queueLength}</div>
            </div>
            <div>
              <div className="system-label">Cooldowns</div>
              <div className="system-value">
                {voiceDebug.cooldowns.length
                  ? voiceDebug.cooldowns.map((item) => `${item.key}:${Math.ceil(item.remainingMs / 1000)}s`).join(", ")
                  : "--"}
              </div>
            </div>
            <div>
              <div className="system-label">Last transition</div>
              <div className="system-value">{voiceDebug.lastRuntimeTransition || "--"}</div>
            </div>
            <div>
              <div className="system-label">Speech mode</div>
              <div className="system-value">{voiceDebug.lastSpeechMode || "--"}</div>
            </div>
            <div>
              <div className="system-label">Spoken preview</div>
              <div className="system-value">{voiceDebug.lastRenderedSpokenTextPreview || "--"}</div>
            </div>
            <div>
              <div className="system-label">Playback stage</div>
              <div className="system-value">{voiceDebug.lastPlaybackStage || "--"}</div>
            </div>
            <div>
              <div className="system-label">Playback item</div>
              <div className="system-value">{voiceDebug.lastPlaybackItemKey || "--"}</div>
            </div>
            <div>
              <div className="system-label">Playback detail</div>
              <div className="system-value">{voiceDebug.lastPlaybackDetail || "--"}</div>
            </div>
            <div>
              <div className="system-label">Playback error</div>
              <div className="system-value">{voiceDebug.lastPlaybackError || "--"}</div>
            </div>
            <div>
              <div className="system-label">Playback time</div>
              <div className="system-value">{formatTimestamp(voiceDebug.lastPlaybackTimestampMs || undefined)}</div>
            </div>
          </div>
        ) : null}

        <div className="system-panel__events">
          <div className="system-label">Latest stream event</div>
          {latestStreamEntry ? (
            <div className="system-event">
              <span className="system-event__time">{formatTimestamp(latestStreamEntry.timestampMs)}</span>
              <span className="system-event__label">{latestStreamEntry.event}</span>
              <span className="system-event__detail">{latestStreamEntry.summary}</span>
            </div>
          ) : (
            <div className="system-panel__empty">No stream timeline entry recorded yet.</div>
          )}
        </div>

        <div className="system-actions">
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("cancel_current_request")}>
            Cancel
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("restart_backend")}>
            Restart backend
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("refresh_capabilities")}>
            Refresh capabilities
          </button>
          <button type="button" disabled={backendControlBusy} onClick={() => void chatActions.runSystemAction("clear_asset_cache")}>
            Clear asset cache
          </button>
        </div>

        {systemPanelError ? <div className="system-panel__error">{systemPanelError}</div> : null}

        <div className="system-panel__events">
          <div className="system-label">Recent system events</div>
          {recentEvents.length === 0 ? (
            <div className="system-panel__empty">No system events recorded yet.</div>
          ) : (
            recentEvents.map((event) => (
              <div key={event.id} className="system-event">
                <span className="system-event__time">{formatTimestamp(event.timestampMs)}</span>
                <span className="system-event__label">{event.label}</span>
                <span className="system-event__detail">{event.detail || "--"}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </details>
  );
}
