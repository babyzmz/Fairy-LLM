import {
  Brain,
  CheckCircle2,
  LoaderCircle,
  MessageSquareText,
  Mic,
  MicOff,
  Monitor,
  Pause,
  Play,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Volume2,
  X,
} from "lucide-react";

import type {
  RealtimeBackendResolution,
  RealtimeRetryableMediaChannel,
  RealtimeSession,
  RealtimeWorkerStatus,
} from "../core/client";
import type { DesktopPreferences } from "../settings/client";
import type { CompanionMemoryNotice } from "./realtimeMemoryState";
import type {
  CaptureSurface,
  RealtimeStartupStage,
  RealtimeUsage,
} from "./realtimeCompanionModel";
import {
  activityLabel,
  assistanceStatusLabel,
  assistanceTerminal,
  backendLabel,
  cooldownHelp,
  formatUsageMinutes,
  presenceLabel,
  resolutionGuidance,
  standbyMessage,
  voiceOutputLabel,
} from "./realtimeCompanionSupport";
import type {
  RealtimeAssistanceProjection,
  RealtimePresenceProjection,
  RealtimePresenceState,
} from "./realtimePresence";
import { RealtimeCaptions, RealtimeMediaStatus } from "./RealtimeMediaStatus";
import { realtimeStartupStageLabel } from "./useRealtimeStartup";

export interface RealtimeCompanionViewProps {
  active: boolean;
  applicationAudioAvailable: boolean;
  applicationAudioConsent: boolean;
  assistance: RealtimeAssistanceProjection[];
  assistanceBusy: string | null;
  authoritativeBackend: RealtimeBackendResolution["backend"];
  busy: boolean;
  captions: string[];
  captureMode: DesktopPreferences["realtime_capture_mode"];
  captureScope: RealtimeWorkerStatus["capture_scope"];
  cloudPrivacy: boolean;
  controlsOpen: boolean;
  draftCaption: string;
  effectiveActivity: "game" | "focus";
  error: string | null;
  interactionIntensity: "quiet" | "standard" | "active";
  liveUsage: RealtimeUsage;
  mediaChannels: RealtimeWorkerStatus["media_channels"];
  memoryNotice: CompanionMemoryNotice | null;
  memoryRetrySessionId: string | null;
  microphoneConsent: boolean;
  muted: boolean;
  open: boolean;
  paused: boolean;
  preferences: DesktopPreferences | null;
  presence: RealtimePresenceState;
  presenceProjection: RealtimePresenceProjection | null;
  requestedProfile: "auto" | "game" | "focus";
  resolution: RealtimeBackendResolution | null;
  resolvedLocal: boolean;
  screenConsent: boolean;
  session: RealtimeSession | null;
  sidecar: RealtimeWorkerStatus["sidecar"];
  sourceId: string;
  startupFailureStage: RealtimeStartupStage | null;
  startupInFlight: boolean;
  startupStage: RealtimeStartupStage | null;
  surfaces: CaptureSurface[];
  unsavedCount: number;
  voiceWarning: string | null;
  windowMode: boolean;
  onApplicationAudioConsent(value: boolean): void;
  onCancelAssistance(item: RealtimeAssistanceProjection): void;
  onCaptureMode(value: DesktopPreferences["realtime_capture_mode"]): void;
  onClose(): void;
  onCreateSessionDigest(sessionId: string): void;
  onExtendPresence(): void;
  onMicrophoneConsent(value: boolean): void;
  onOpenAssistanceChat(): void;
  onReplaceObservedWindow(): void;
  onRetryMedia(channel: RealtimeRetryableMediaChannel): void;
  onRetryTranscriptAndDigest(): void;
  onScreenConsent(value: boolean): void;
  onSourceId(value: string): void;
  onStart(): void;
  onStop(): void;
  onToggleControls(): void;
  onToggleMute(): void;
  onTogglePause(): void;
  onUpdateLivePolicy(
    profile: "auto" | "game" | "focus",
    intensity: "quiet" | "standard" | "active",
  ): void;
  onWakeStandby(): void;
}

export function RealtimeCompanionView(props: RealtimeCompanionViewProps) {
  if (!props.open) return null;
  const {
    active,
    applicationAudioAvailable,
    applicationAudioConsent,
    assistance,
    assistanceBusy,
    authoritativeBackend,
    busy,
    captions,
    captureMode,
    captureScope,
    cloudPrivacy,
    controlsOpen,
    draftCaption,
    effectiveActivity,
    error,
    interactionIntensity,
    liveUsage,
    mediaChannels,
    memoryNotice,
    memoryRetrySessionId,
    microphoneConsent,
    muted,
    paused,
    preferences,
    presence,
    presenceProjection,
    requestedProfile,
    resolution,
    resolvedLocal,
    screenConsent,
    session,
    sidecar,
    sourceId,
    startupFailureStage,
    startupInFlight,
    startupStage,
    surfaces,
    unsavedCount,
    voiceWarning,
    windowMode,
  } = props;
  return (
    <div className={`realtime-backdrop${windowMode ? " is-window" : ""}`} role="presentation">
      <section className="realtime-panel" role="dialog" aria-modal={!windowMode} aria-label="Realtime Companion Beta">
        <header><div><span>Fairy</span><h2>Realtime Companion Beta</h2></div><button type="button" aria-label="Close" onClick={props.onClose}><X size={17} /></button></header>
        <div className={`realtime-privacy ${cloudPrivacy ? "is-cloud" : "is-local"}`}><ShieldCheck size={16} /><span>{cloudPrivacy ? "Cloud Live sends only this session's enabled microphone and explicitly selected-window frames to the configured provider. Application audio is unavailable because the provider transport cannot preserve a separate track. Raw media is transient and never stored by Fairy." : "Local MiniCPM processes enabled microphone, observed-window frames, and optional selected-application audio on this device. Raw media stays in transient local memory."} Spoken captions remain on this device in the linked conversation.</span></div>
        {startupStage !== null ? <div className="realtime-note" role="status" aria-label="Realtime startup">{realtimeStartupStageLabel(startupStage)}</div> : null}
        {!active ? (
          <div className="realtime-config">
            <label><span><Monitor size={15} /> {captureMode === "follow_foreground" && resolvedLocal ? "Starting observed window" : "Observed window"}</span><select value={sourceId} onChange={(event) => props.onSourceId(event.target.value)} disabled={busy}>{surfaces.map((surface) => <option key={surface.source_id} value={surface.source_id}>{surface.label} · {surface.width}×{surface.height}</option>)}</select></label>
            {resolvedLocal ? <label><span><SlidersHorizontal size={15} /> Observation scope</span><select value={captureMode} onChange={(event) => props.onCaptureMode(event.target.value as DesktopPreferences["realtime_capture_mode"])} disabled={busy}><option value="selected_window">Keep selected window</option><option value="follow_foreground">Follow foreground locally</option></select></label> : null}
            <div className="realtime-policy realtime-backend-card"><span>Backend</span><strong>{backendLabel(resolution?.backend, resolution?.requires_cloud_upload_consent, preferences?.realtime_cloud_provider)}</strong><span>Voice</span><strong>{voiceOutputLabel(preferences?.realtime_voice_output)}</strong></div>
            {resolution?.available === false ? <div className="realtime-error" role="alert">{resolutionGuidance(resolution.reason)}</div> : null}
            {resolution === null ? <div className="realtime-note" role="status">Checking backend readiness…</div> : null}
            <label className="realtime-consent"><input type="checkbox" checked={microphoneConsent} onChange={(event) => props.onMicrophoneConsent(event.target.checked)} /><Mic size={15} /><span>{cloudPrivacy ? "Upload microphone for this Cloud session" : "Use microphone for this Local session"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={screenConsent} onChange={(event) => props.onScreenConsent(event.target.checked)} /><Monitor size={15} /><span>{cloudPrivacy ? "Upload only the explicitly selected window" : captureMode === "follow_foreground" ? "Observe the foreground window locally, excluding protected apps" : "Process only the selected window locally"}</span></label>
            <label className="realtime-consent"><input type="checkbox" checked={applicationAudioConsent} disabled={!applicationAudioAvailable} onChange={(event) => props.onApplicationAudioConsent(event.target.checked)} /><Volume2 size={15} /><span>{applicationAudioAvailable ? "Process selected application audio locally" : "Application audio unavailable for this Cloud provider"}</span></label>
            <p className="realtime-note">System-wide audio is never captured. Each enabled source remains scoped to this session.</p>
            <button className="realtime-primary" type="button" disabled={busy || resolution?.available !== true || !microphoneConsent || !screenConsent || sourceId === ""} onClick={props.onStart}>{busy ? <LoaderCircle className="spin" size={15} /> : <Mic size={15} />} Start Realtime</button>
          </div>
        ) : (
          <div className="realtime-live">
            <div className="realtime-live-status"><span className={`realtime-pulse ${presence}`} /><div><strong>{presenceLabel(presence)}</strong><small>{backendLabel(authoritativeBackend, false, presenceProjection?.cloud_provider ?? preferences?.realtime_cloud_provider)}</small></div><button type="button" disabled={busy && !startupInFlight} onClick={props.onStop}><Square size={14} /> Stop</button></div>
            <div className="realtime-session-policy" aria-label="Realtime activity policy">
              <label><span>Profile</span><select value={requestedProfile} disabled={busy || presence === "privacy_paused"} onChange={(event) => props.onUpdateLivePolicy(event.target.value as "auto" | "game" | "focus", interactionIntensity)}><option value="auto">Auto</option><option value="game">Game</option><option value="focus">Focus</option></select></label>
              <label><span>Intensity</span><select value={interactionIntensity} disabled={busy || presence === "privacy_paused"} onChange={(event) => props.onUpdateLivePolicy(requestedProfile, event.target.value as "quiet" | "standard" | "active")}><option value="quiet">Quiet</option><option value="standard">Standard</option><option value="active">Active</option></select></label>
              <div><span>Effective</span><strong>{activityLabel(effectiveActivity)}</strong></div>
              <div><span>Backend</span><strong>{authoritativeBackend === "local_mini_cpm_o45" ? "Local" : "Cloud"}</strong></div>
              {captureScope !== null ? <div><span>Observation</span><strong>{captureScope.mode === "follow_foreground" ? "Follow foreground" : captureScope.source_available ? "Selected window" : "Source unavailable"}</strong></div> : null}
              {captureScope?.privacy_paused ? <div><span>Privacy</span><strong>Observation paused</strong></div> : null}
            </div>
            <p className="realtime-policy-help">{cooldownHelp(effectiveActivity, interactionIntensity)}</p>
            <RealtimeMediaStatus busy={busy} captureScope={captureScope} channels={mediaChannels} onReplaceSource={props.onReplaceObservedWindow} onRetry={props.onRetryMedia} onSourceChange={props.onSourceId} sourceId={sourceId} surfaces={surfaces} />
            {assistance.length > 0 ? <section className="realtime-assistance" aria-label="Core assistance">{assistance.slice(-3).map((item) => {
              const terminal = assistanceTerminal(item.status);
              return <article key={item.request_id} data-status={item.status}><div className="realtime-assistance-heading"><Search size={14} aria-hidden="true" /><strong>Core assistance</strong><span>{assistanceStatusLabel(item.status)}</span></div><p>{item.public_intent}</p>{item.public_summary ? <small>{item.public_summary}</small> : item.status === "awaiting_approval" ? <small>Approval is waiting in the main Fairy workspace.</small> : null}<div className="realtime-assistance-actions"><button type="button" disabled={assistanceBusy !== null} onClick={props.onOpenAssistanceChat}><MessageSquareText size={13} /> Open main chat</button>{!terminal ? <button type="button" disabled={assistanceBusy !== null} onClick={() => props.onCancelAssistance(item)}>Cancel</button> : null}</div></article>;
            })}</section> : null}
            {presence === "standby" ? <div className="realtime-standby" role="status"><span>{standbyMessage(presenceProjection?.standby_reason ?? null)}</span>{presenceProjection?.duration_extension_required ? <button type="button" disabled={busy} onClick={props.onExtendPresence}>Extend 30 min</button> : presenceProjection?.wake_available ? <button type="button" disabled={busy} onClick={props.onWakeStandby}>Wake</button> : null}</div> : null}
            <div className="realtime-usage" aria-label="Session usage"><span>Voice {formatUsageMinutes(liveUsage.audio_input_ms + liveUsage.audio_output_ms)}</span><span>Frames {liveUsage.video_frame_count}</span></div>
            <RealtimeCaptions captions={captions} draftCaption={draftCaption} />
          </div>
        )}
        {sidecar.restart_used ? <div className={`realtime-sidecar-state${sidecar.quarantined ? " is-quarantined" : ""}`} role={sidecar.quarantined ? "alert" : "status"}><RotateCcw size={14} aria-hidden="true" /><span><strong>{sidecar.quarantined ? "Local runtime needs verification" : "Local runtime recovered"}</strong><small>{sidecar.quarantined ? "Automatic recovery is disabled until Verify succeeds in main Settings." : sidecar.context_interrupted ? "The Sidecar restarted once in a new segment; prior context was interrupted." : "The Sidecar restarted once in a new segment."}</small></span></div> : null}
        {memoryNotice !== null && memoryNotice.sessionId === session?.id ? <div className="realtime-memory-notice" role="status">{memoryNotice.pendingCount > 0 ? <Brain size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" />}<span><strong>{memoryNotice.pendingCount > 0 ? "Memory review available" : "Session summary saved"}</strong><small>{memoryNotice.pendingCount > 0 ? `${memoryNotice.pendingCount} suggestion${memoryNotice.pendingCount === 1 ? "" : "s"} require review in the main window.` : memoryNotice.savedCount > 0 ? `${memoryNotice.savedCount} explicit low-risk fact${memoryNotice.savedCount === 1 ? "" : "s"} saved.` : "No durable facts were saved."}</small></span><button type="button" disabled={assistanceBusy !== null} onClick={props.onOpenAssistanceChat}><MessageSquareText size={13} /> Open main chat</button></div> : null}
        {unsavedCount > 0 ? <div className="realtime-transcript-warning" role="status"><span>{unsavedCount} {unsavedCount === 1 ? "caption" : "captions"} unsaved</span><button type="button" onClick={props.onRetryTranscriptAndDigest}><RotateCcw size={13} /> Retry saving</button></div> : null}
        {memoryRetrySessionId !== null && unsavedCount === 0 ? <div className="realtime-transcript-warning" role="status"><span>Session summary deferred</span><button type="button" disabled={busy} onClick={() => props.onCreateSessionDigest(memoryRetrySessionId)}><RotateCcw size={13} /> Retry summary</button></div> : null}
        {voiceWarning ? <div className="realtime-warning" role="status">{voiceWarning}</div> : null}
        {error ? <div className="realtime-error" role="alert"><span>{error}</span>{startupFailureStage !== null ? <small>Failed while {realtimeStartupStageLabel(startupFailureStage).toLocaleLowerCase()}.</small> : null}</div> : null}
      </section>
      {active ? <div className={`realtime-controls${controlsOpen ? " is-open" : ""}`}>{controlsOpen ? <div className="realtime-controls-cluster" role="group" aria-label="Session controls"><button type="button" disabled={busy} onClick={props.onTogglePause} aria-pressed={paused} title={paused ? "恢复" : "暂停"}>{paused ? <Play size={16} /> : <Pause size={16} />}<span>{paused ? "恢复" : "暂停"}</span></button><button type="button" className="realtime-controls-stop" disabled={busy} onClick={props.onStop} title="停止"><Square size={16} /><span>停止</span></button><button type="button" onClick={props.onToggleMute} aria-pressed={muted} title={muted ? "开麦" : "闭麦"}>{muted ? <MicOff size={16} /> : <Mic size={16} />}<span>{muted ? "开麦" : "闭麦"}</span></button></div> : null}<button type="button" className="realtime-controls-toggle" aria-expanded={controlsOpen} aria-label={controlsOpen ? "收起控制" : "展开控制"} onClick={props.onToggleControls}>{controlsOpen ? <X size={16} /> : <SlidersHorizontal size={16} />}</button></div> : null}
    </div>
  );
}
