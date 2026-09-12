use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use fairy_realtime_worker::{
    read_frame, validate_backend_start, write_frame, BackendStartRequest, HostCommand,
    LocalOmniLaunch, RealtimeActivityProfile, RealtimeBackendKind, RealtimeCloudProviderKind,
    RealtimeContextCarryover, RealtimeInteractionIntensity, RealtimeResourceLevel,
    RealtimeResourcePolicy, RealtimeVoiceOutput, SecretString, WorkerEvent,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::hardware_probe::sample_realtime_gpu_memory;
use crate::omni_model_manifest::OmniModelManifest;
use crate::realtime_assistance::{RealtimeAssistancePublicState, RealtimeAssistanceRouter};
use crate::realtime_context::RealtimeContextAuthority;
use crate::realtime_coordinator::{
    ContextEpochIdentity, ContextRotationReason, PendingContextRotation, RealtimeCoordinatorAction,
    RealtimeCoordinatorEvent, RealtimeCoordinatorStart, RealtimeCoordinatorState,
    RealtimePresenceProjection, RealtimePresenceState, RealtimeWakeTransition,
};
use crate::realtime_dialogue::{
    RealtimeDialogueDecision, RealtimeDialogueDirector, RealtimeEnvironmentGates,
};
use crate::realtime_privacy::{
    inspect_window, normalize_excluded_applications, sample_foreground_window, RealtimeCaptureMode,
    RealtimeSensitiveCategory,
};
use crate::realtime_resource_governor::{
    RealtimeResourceGovernor, RealtimeResourceSample, RealtimeResourceSnapshot,
};
use crate::realtime_sidecar_supervisor::{
    RealtimeSidecarDecision, RealtimeSidecarSnapshot, RealtimeSidecarSupervisor,
    RealtimeSidecarSupervisorError,
};

pub const REALTIME_WORKER_EVENT: &str = "fairy-realtime-event";
const REALTIME_PROTOCOL: &str = "fairy-realtime-worker-v2";
const WORKER_STOP_GRACE: Duration = Duration::from_millis(80);

/// Host-validated resource policy and the ownership token for this startup.
pub struct RealtimeStartResources {
    pub presence_max_minutes: u16,
    pub local_keep_warm_minutes: u8,
    pub reservation: Option<crate::model_resources::ModelReservation>,
}

#[derive(Clone, Debug)]
pub struct RealtimeWorkerLaunch {
    pub program: PathBuf,
    pub log_path: PathBuf,
    pub quarantine_path: PathBuf,
    pub local_omni: LocalOmniLaunch,
}

trait RealtimeCredentialProvider {
    fn credential_provider(&self) -> &'static str;
}

impl RealtimeCredentialProvider for RealtimeCloudProviderKind {
    fn credential_provider(&self) -> &'static str {
        match self {
            RealtimeCloudProviderKind::GeminiLive => "gemini",
            RealtimeCloudProviderKind::GlmRealtimeFlash
            | RealtimeCloudProviderKind::GlmRealtimeAir => "zhipu",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerStartInput {
    pub session_id: String,
    pub resolution_token: String,
    pub locale: String,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub activity_profile: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
    pub voice_output: RealtimeVoiceOutput,
    pub source_id: Option<u64>,
    pub microphone_enabled: bool,
    pub screen_enabled: bool,
    pub application_audio_enabled: bool,
    pub online_assistance_enabled: bool,
    pub cloud_microphone_upload_consent: bool,
    pub cloud_screen_upload_consent: bool,
    #[serde(default)]
    pub capture_mode: RealtimeCaptureMode,
    #[serde(default)]
    pub excluded_applications: Vec<String>,
}

impl RealtimeWorkerStartInput {
    pub fn credential_provider(&self) -> Option<&'static str> {
        self.cloud_provider
            .as_ref()
            .map(RealtimeCredentialProvider::credential_provider)
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerStopInput {
    pub session_id: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerToolResultInput {
    pub session_id: String,
    pub call_id: String,
    pub public_summary: String,
    pub succeeded: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerSetInputInput {
    pub session_id: String,
    pub microphone: bool,
    pub video: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerRetryMediaInput {
    pub session_id: String,
    pub channel: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerReplaceSourceInput {
    pub session_id: String,
    pub source_id: u64,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerSetPolicyInput {
    pub session_id: String,
    pub activity_profile: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerWakeInput {
    pub session_id: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerExtendInput {
    pub session_id: String,
    pub additional_minutes: u16,
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerSpeechStateInput {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub speech_generation: u64,
    pub speaking: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct RealtimeWorkerStatus {
    pub running: bool,
    pub session_id: Option<String>,
    pub segment_id: Option<String>,
    pub context_epoch: Option<u64>,
    pub backend: Option<RealtimeBackendKind>,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub action_required: bool,
    pub presence_projection: Option<RealtimePresenceProjection>,
    pub assistance: Vec<RealtimeAssistancePublicState>,
    pub resource: Option<RealtimeResourceSnapshot>,
    pub sidecar: RealtimeSidecarSnapshot,
    pub capture_scope: Option<RealtimeCaptureScopePublicState>,
    pub media_channels: Vec<RealtimeMediaChannelPublicState>,
    #[serde(flatten)]
    pub usage: RealtimeWorkerUsage,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeCaptureScopePublicState {
    pub mode: RealtimeCaptureMode,
    pub source_sequence: u64,
    pub source_available: bool,
    pub privacy_paused: bool,
    pub sensitive_category: Option<RealtimeSensitiveCategory>,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeMediaChannelPublicState {
    pub channel: String,
    pub sequence: u64,
    pub status: String,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct RealtimeWorkerUsage {
    pub audio_input_ms: u64,
    pub audio_output_ms: u64,
    pub video_frame_count: u64,
    pub interruption_count: u64,
    pub tool_call_count: u64,
}

#[derive(Debug, Error)]
pub enum RealtimeWorkerError {
    #[error("realtime worker is unavailable")]
    Unavailable,
    #[error("realtime worker is already running a session")]
    Busy,
    #[error("realtime worker protocol failed")]
    Protocol,
    #[error("realtime worker process failed: {0}")]
    Io(#[from] std::io::Error),
}

struct WorkerProcess {
    audio_lease: Option<crate::audio_focus::RealtimeAudioLease>,
    model_reservation: Arc<Mutex<Option<crate::model_resources::ModelReservation>>>,
    child: Child,
    input: Arc<Mutex<ChildStdin>>,
    session_id: Option<String>,
    expected_shutdown: Arc<AtomicBool>,
    terminal: Arc<AtomicBool>,
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        if let Ok(mut reservation) = self.model_reservation.lock() {
            reservation.take();
        }
    }
}

struct WorkerGovernanceHandles {
    resources: Arc<crate::model_resources::ModelResources>,
    model_reservation: Arc<Mutex<Option<crate::model_resources::ModelReservation>>>,
    assistance: Arc<RealtimeAssistanceRouter>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
    context: Arc<Mutex<RealtimeContextAuthority>>,
    resource_governor: Arc<Mutex<Option<RealtimeResourceGovernor>>>,
    sidecar_supervisor: Arc<Mutex<RealtimeSidecarSupervisor>>,
    recovery: Arc<Mutex<Option<RealtimeRecoveryEnvelope>>>,
    capture_scope: Arc<Mutex<Option<RealtimeCaptureScopeState>>>,
    media_channels: Arc<Mutex<BTreeMap<String, RealtimeMediaChannelPublicState>>>,
    local_omni: LocalOmniLaunch,
}

#[derive(Clone)]
struct RealtimeRecoveryEnvelope {
    locale: String,
    activity_profile: RealtimeActivityProfile,
    interaction_intensity: RealtimeInteractionIntensity,
    voice_output: RealtimeVoiceOutput,
    desktop_host_process_id: u32,
    source_id: Option<u64>,
    microphone_enabled: bool,
    screen_enabled: bool,
    application_audio_enabled: bool,
    online_assistance_enabled: bool,
    persona_snapshot: Zeroizing<String>,
}

impl RealtimeRecoveryEnvelope {
    fn from_start(
        input: &RealtimeWorkerStartInput,
        persona_snapshot: Zeroizing<String>,
    ) -> Result<Self, RealtimeWorkerError> {
        if persona_snapshot.len() > 64 * 1024
            || input.locale.trim().is_empty()
            || input.locale.len() > 64
        {
            return Err(RealtimeWorkerError::Protocol);
        }
        Ok(Self {
            locale: input.locale.clone(),
            activity_profile: input.activity_profile,
            interaction_intensity: input.interaction_intensity,
            voice_output: input.voice_output,
            desktop_host_process_id: std::process::id(),
            source_id: input.source_id,
            microphone_enabled: input.microphone_enabled,
            screen_enabled: input.screen_enabled,
            application_audio_enabled: input.application_audio_enabled,
            online_assistance_enabled: input.online_assistance_enabled,
            persona_snapshot,
        })
    }
}

#[derive(Clone)]
struct RealtimeCaptureScopeState {
    mode: RealtimeCaptureMode,
    requested_source_id: Option<u64>,
    effective_source_id: Option<u64>,
    source_sequence: u64,
    pending_source_id: Option<u64>,
    pending_source_sequence: Option<u64>,
    privacy_paused: bool,
    sensitive_category: Option<RealtimeSensitiveCategory>,
    error_code: Option<String>,
    excluded_applications: Vec<String>,
    screen_enabled: bool,
    application_audio_enabled: bool,
}

impl RealtimeCaptureScopeState {
    fn public(&self) -> RealtimeCaptureScopePublicState {
        RealtimeCaptureScopePublicState {
            mode: self.mode,
            source_sequence: self.source_sequence,
            source_available: self.effective_source_id.is_some(),
            privacy_paused: self.privacy_paused,
            sensitive_category: self.sensitive_category,
            error_code: self.error_code.clone(),
        }
    }
}

enum DialogueGovernance {
    Projection(Value),
    Rotate(PendingContextRotation),
    Suppress,
}

pub struct RealtimeWorkerManager {
    closed: AtomicBool,
    audio_focus: Arc<crate::audio_focus::AudioFocus>,
    launch: RealtimeWorkerLaunch,
    assistance: Arc<RealtimeAssistanceRouter>,
    process: Mutex<Option<WorkerProcess>>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
    context: Arc<Mutex<RealtimeContextAuthority>>,
    resource_governor: Arc<Mutex<Option<RealtimeResourceGovernor>>>,
    sidecar_supervisor: Arc<Mutex<RealtimeSidecarSupervisor>>,
    recovery: Arc<Mutex<Option<RealtimeRecoveryEnvelope>>>,
    capture_scope: Arc<Mutex<Option<RealtimeCaptureScopeState>>>,
    media_channels: Arc<Mutex<BTreeMap<String, RealtimeMediaChannelPublicState>>>,
}

impl RealtimeWorkerManager {
    pub fn new(launch: RealtimeWorkerLaunch, assistance: Arc<RealtimeAssistanceRouter>) -> Self {
        let sidecar_supervisor = RealtimeSidecarSupervisor::load(
            launch.quarantine_path.clone(),
            launch.local_omni.model_version.clone(),
            launch.local_omni.manifest_digest.clone(),
        );
        Self {
            audio_focus: Arc::new(crate::audio_focus::AudioFocus::default()),
            closed: AtomicBool::new(false),
            launch,
            assistance,
            process: Mutex::new(None),
            usage: Arc::new(Mutex::new(RealtimeWorkerUsage::default())),
            active_identity: Arc::new(Mutex::new(None)),
            coordinator: Arc::new(Mutex::new(None)),
            dialogue: Arc::new(Mutex::new(None)),
            context: Arc::new(Mutex::new(RealtimeContextAuthority::default())),
            resource_governor: Arc::new(Mutex::new(None)),
            sidecar_supervisor: Arc::new(Mutex::new(sidecar_supervisor)),
            recovery: Arc::new(Mutex::new(None)),
            capture_scope: Arc::new(Mutex::new(None)),
            media_channels: Arc::new(Mutex::new(BTreeMap::new())),
        }
    }

    pub fn status(&self) -> RealtimeWorkerStatus {
        let mut guard = self.process.lock().expect("realtime worker lock poisoned");
        if guard.as_mut().is_some_and(reap_finished_process) {
            let finished_session = guard
                .as_ref()
                .and_then(|process| process.session_id.as_deref())
                .map(str::to_owned);
            *guard = None;
            if let Some(session_id) = finished_session {
                self.assistance.end_session(&session_id);
                let action_required = self
                    .coordinator
                    .lock()
                    .ok()
                    .and_then(|state| {
                        state
                            .as_ref()
                            .map(RealtimeCoordinatorState::action_required)
                    })
                    .unwrap_or(false);
                if !action_required {
                    if let Ok(mut context) = self.context.lock() {
                        context.end_session(&session_id);
                    }
                }
            }
            self.cleanup_finished_governance();
        }
        let projection = self.governance_projection();
        let presence_projection = self.current_presence_projection();
        let session_id = guard
            .as_ref()
            .and_then(|process| process.session_id.clone());
        RealtimeWorkerStatus {
            running: guard.is_some(),
            assistance: self.assistance.snapshot(session_id.as_deref()),
            resource: self.resource_snapshot(),
            sidecar: self.sidecar_snapshot(),
            capture_scope: self.capture_scope_snapshot(),
            media_channels: self.media_channels_snapshot(),
            session_id,
            segment_id: projection.as_ref().map(|value| value.0.clone()),
            context_epoch: projection.as_ref().map(|value| value.1),
            backend: projection.as_ref().map(|value| value.2),
            cloud_provider: projection.as_ref().and_then(|value| value.3),
            action_required: projection.is_some_and(|value| value.4),
            presence_projection,
            usage: self.usage_snapshot(),
        }
    }

    pub fn with_audio_focus(mut self, focus: Arc<crate::audio_focus::AudioFocus>) -> Self {
        self.audio_focus = focus;
        self
    }

    pub fn wake_requirements(
        &self,
    ) -> Option<(RealtimeBackendKind, RealtimeActivityProfile, bool)> {
        self.coordinator.lock().ok()?.as_ref().map(|coordinator| {
            (
                coordinator.backend(),
                coordinator.requested_activity_profile(),
                coordinator.local_backend_unloaded(),
            )
        })
    }

    pub fn start(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        resources: RealtimeStartResources,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(app, input, credential, persona_snapshot, false, resources)
    }

    pub fn continue_session(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        resources: RealtimeStartResources,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(app, input, credential, persona_snapshot, true, resources)
    }

    fn start_segment(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        continuation_approved: bool,
        resources: RealtimeStartResources,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let RealtimeStartResources {
            presence_max_minutes,
            local_keep_warm_minutes,
            reservation,
        } = resources;
        validate_capture_scope(&input)?;
        if input.backend == RealtimeBackendKind::CloudLive && credential.is_none() {
            return Err(RealtimeWorkerError::Protocol);
        }
        let persona: Value =
            serde_json::from_str(&persona_snapshot).map_err(|_| RealtimeWorkerError::Protocol)?;
        let persona_digest = persona
            .get("persona_digest")
            .and_then(Value::as_str)
            .ok_or(RealtimeWorkerError::Protocol)?
            .to_owned();
        if input.backend == RealtimeBackendKind::LocalMiniCpmO45 {
            let mut supervisor = self
                .sidecar_supervisor
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            if supervisor.snapshot().quarantined {
                return Err(RealtimeWorkerError::Unavailable);
            }
            if !continuation_approved {
                supervisor.begin_session();
            }
        }
        let recovery = (input.backend == RealtimeBackendKind::LocalMiniCpmO45)
            .then(|| RealtimeRecoveryEnvelope::from_start(&input, persona_snapshot.clone()))
            .transpose()?;
        let mut guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        if self.closed.load(Ordering::Acquire) {
            return Err(RealtimeWorkerError::Unavailable);
        }
        if let Some(process) = guard.as_mut() {
            if !reap_finished_process(process) {
                return Err(RealtimeWorkerError::Busy);
            }
            *guard = None;
        }
        // This guard covers startup failures too; only the running process retains it.
        let audio_lease = self
            .audio_focus
            .reserve_realtime()
            .map_err(|_| RealtimeWorkerError::Busy)?;
        if input.backend == RealtimeBackendKind::LocalMiniCpmO45 && reservation.is_none() {
            return Err(RealtimeWorkerError::Unavailable);
        }
        let model_reservation = Arc::new(Mutex::new(reservation));
        if let Ok(mut usage) = self.usage.lock() {
            *usage = RealtimeWorkerUsage::default();
        }
        let segment_id = Uuid::new_v4().to_string();
        let existing = self
            .coordinator
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone();
        let previous_governance = existing.clone();
        let coordinator = if let Some(mut coordinator) = existing {
            if !continuation_approved
                || !coordinator.action_required()
                || coordinator.active_identity().session_id != input.session_id
                || coordinator.persona_digest() != persona_digest
            {
                return Err(RealtimeWorkerError::Protocol);
            }
            coordinator
                .apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                    segment_id: segment_id.clone(),
                    backend: input.backend,
                    cloud_provider: input.cloud_provider,
                    persona_digest,
                })
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            coordinator
        } else {
            if continuation_approved {
                return Err(RealtimeWorkerError::Protocol);
            }
            RealtimeCoordinatorState::start(RealtimeCoordinatorStart {
                session_id: input.session_id.clone(),
                segment_id: segment_id.clone(),
                persona_digest,
                backend: input.backend,
                cloud_provider: input.cloud_provider,
                activity_profile: input.activity_profile,
                interaction_intensity: input.interaction_intensity,
                presence_max_minutes,
                local_keep_warm_minutes,
            })
            .map_err(|_| RealtimeWorkerError::Protocol)?
        };
        let identity = coordinator.active_identity().clone();
        let excluded_applications = normalize_excluded_applications(&input.excluded_applications)
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let source_decision = input
            .source_id
            .map(|source_id| inspect_window(source_id, &excluded_applications));
        let source_sensitive = source_decision.is_some_and(|decision| decision.sensitive.is_some());
        let ambient = crate::ambient_context::sample_ambient_device_facts();
        let mut director = RealtimeDialogueDirector::new(
            identity.session_id.clone(),
            identity.segment_id.clone(),
            identity.epoch,
            coordinator.persona_digest().to_owned(),
            input.voice_output,
        )
        .ok_or(RealtimeWorkerError::Protocol)?;
        director.set_environment(RealtimeEnvironmentGates {
            locked: ambient.locked,
            do_not_disturb: ambient.do_not_disturb,
            sensitive_window: source_sensitive,
            privacy_paused: false,
        });
        let capture_scope = RealtimeCaptureScopeState {
            mode: input.capture_mode,
            requested_source_id: input.source_id,
            effective_source_id: (!source_sensitive).then_some(input.source_id).flatten(),
            source_sequence: 1,
            pending_source_id: None,
            pending_source_sequence: None,
            privacy_paused: source_sensitive,
            sensitive_category: source_decision.and_then(|decision| decision.sensitive),
            error_code: source_decision
                .and_then(|decision| decision.sensitive)
                .map(|category| category.public_code().to_owned()),
            excluded_applications,
            screen_enabled: input.screen_enabled,
            application_audio_enabled: input.application_audio_enabled,
        };
        let previous_dialogue = self
            .dialogue
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone();
        let previous_context = self
            .context
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone();
        let previous_resource_governor = self
            .resource_governor
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone();
        let previous_recovery = self
            .recovery
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone();
        *self
            .resource_governor
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)? = (input.backend
            == RealtimeBackendKind::LocalMiniCpmO45)
            .then(|| RealtimeResourceGovernor::new(0));
        self.context
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .attach_session(&input.session_id, &persona)
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        if let Ok(mut dialogue) = self.dialogue.lock() {
            *dialogue = Some(director);
        } else {
            self.restore_context(previous_context);
            self.restore_resource_governor(previous_resource_governor);
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = Some(identity.clone());
        } else {
            self.restore_dialogue(previous_dialogue);
            self.restore_context(previous_context);
            self.restore_resource_governor(previous_resource_governor);
            return Err(RealtimeWorkerError::Protocol);
        }
        *self
            .recovery
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)? = recovery;
        let mut process = match spawn_worker(
            &self.launch,
            app.clone(),
            WorkerGovernanceHandles {
                resources: Arc::clone(&self.audio_focus.model_resources),
                model_reservation: Arc::clone(&model_reservation),
                assistance: Arc::clone(&self.assistance),
                usage: Arc::clone(&self.usage),
                active_identity: Arc::clone(&self.active_identity),
                coordinator: Arc::clone(&self.coordinator),
                dialogue: Arc::clone(&self.dialogue),
                context: Arc::clone(&self.context),
                resource_governor: Arc::clone(&self.resource_governor),
                sidecar_supervisor: Arc::clone(&self.sidecar_supervisor),
                recovery: Arc::clone(&self.recovery),
                capture_scope: Arc::clone(&self.capture_scope),
                media_channels: Arc::clone(&self.media_channels),
                local_omni: self.launch.local_omni.clone(),
            },
        ) {
            Ok(process) => process,
            Err(error) => {
                self.restore_governance(previous_governance);
                self.restore_dialogue(previous_dialogue);
                self.restore_context(previous_context);
                self.restore_resource_governor(previous_resource_governor);
                self.restore_recovery(previous_recovery);
                return Err(error);
            }
        };
        if let Ok(mut state) = self.coordinator.lock() {
            *state = Some(coordinator);
        } else {
            let _ = process.child.kill();
            let _ = process.child.wait();
            self.restore_governance(previous_governance);
            self.restore_dialogue(previous_dialogue);
            self.restore_context(previous_context);
            self.restore_resource_governor(previous_resource_governor);
            self.restore_recovery(previous_recovery);
            return Err(RealtimeWorkerError::Protocol);
        }
        let command = HostCommand::Start {
            session_id: input.session_id.clone(),
            segment_id,
            context_epoch: identity.epoch,
            backend: input.backend,
            cloud_provider: input.cloud_provider,
            cloud_credential: credential.map(SecretString::from),
            local_omni: (input.backend == RealtimeBackendKind::LocalMiniCpmO45)
                .then(|| Box::new(self.launch.local_omni.clone())),
            persona_snapshot: SecretString::from(persona_snapshot),
            locale: input.locale.clone(),
            activity_profile: input.activity_profile,
            interaction_intensity: input.interaction_intensity,
            voice_output: input.voice_output,
            desktop_host_process_id: std::process::id(),
            source_id: (!capture_scope.privacy_paused)
                .then_some(input.source_id)
                .flatten(),
            microphone_enabled: input.microphone_enabled,
            screen_enabled: input.screen_enabled && !capture_scope.privacy_paused,
            application_audio_enabled: input.application_audio_enabled
                && !capture_scope.privacy_paused,
            online_assistance_enabled: input.online_assistance_enabled,
        };
        self.assistance.attach_session(
            &input.session_id,
            &input.locale,
            input.online_assistance_enabled,
            Arc::clone(&process.input),
        );
        if send_command(&process.input, &command).is_err() {
            self.assistance.end_session(&input.session_id);
            let _ = process.child.kill();
            let _ = process.child.wait();
            self.restore_governance(previous_governance);
            self.restore_dialogue(previous_dialogue);
            self.restore_context(previous_context);
            self.restore_resource_governor(previous_resource_governor);
            self.restore_recovery(previous_recovery);
            return Err(RealtimeWorkerError::Protocol);
        }
        if input.backend == RealtimeBackendKind::LocalMiniCpmO45
            && send_command(
                &process.input,
                &HostCommand::SetResourcePolicy {
                    session_id: input.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.epoch,
                    policy: RealtimeResourcePolicy::for_level(RealtimeResourceLevel::Normal),
                },
            )
            .is_err()
        {
            self.assistance.end_session(&input.session_id);
            let _ = process.child.kill();
            let _ = process.child.wait();
            self.restore_governance(previous_governance);
            self.restore_dialogue(previous_dialogue);
            self.restore_context(previous_context);
            self.restore_resource_governor(previous_resource_governor);
            self.restore_recovery(previous_recovery);
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Some(projection) = self.current_presence_projection() {
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(projection),
            );
        }
        *self
            .capture_scope
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)? = Some(capture_scope);
        if let Ok(mut channels) = self.media_channels.lock() {
            channels.clear();
        }
        process.session_id = Some(input.session_id);
        process.audio_lease = Some(audio_lease);
        let status = RealtimeWorkerStatus {
            running: true,
            session_id: process.session_id.clone(),
            segment_id: Some(identity.segment_id),
            context_epoch: Some(identity.epoch),
            backend: Some(input.backend),
            cloud_provider: input.cloud_provider,
            action_required: false,
            presence_projection: self.current_presence_projection(),
            assistance: self.assistance.snapshot(process.session_id.as_deref()),
            resource: self.resource_snapshot(),
            sidecar: self.sidecar_snapshot(),
            capture_scope: self.capture_scope_snapshot(),
            media_channels: self.media_channels_snapshot(),
            usage: self.usage_snapshot(),
        };
        *guard = Some(process);
        Ok(status)
    }

    pub fn stop(
        &self,
        app: &AppHandle,
        session_id: &str,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let Some(mut process) = guard.take() else {
            self.assistance.end_session(session_id);
            if let Ok(mut context) = self.context.lock() {
                context.end_session(session_id);
            }
            self.emit_terminal_presence(app);
            self.finish_governance();
            return Ok(RealtimeWorkerStatus {
                running: false,
                session_id: None,
                segment_id: None,
                context_epoch: None,
                backend: None,
                cloud_provider: None,
                action_required: false,
                presence_projection: None,
                assistance: Vec::new(),
                resource: None,
                sidecar: self.sidecar_snapshot(),
                capture_scope: None,
                media_channels: Vec::new(),
                usage: self.usage_snapshot(),
            });
        };
        if process.session_id.as_deref() != Some(session_id) {
            *guard = Some(process);
            return Err(RealtimeWorkerError::Protocol);
        }
        process.expected_shutdown.store(true, Ordering::Release);
        let _ = send_command(
            &process.input,
            &HostCommand::Stop {
                session_id: session_id.to_owned(),
            },
        );
        let deadline = Instant::now() + WORKER_STOP_GRACE;
        while Instant::now() < deadline {
            if process.child.try_wait()?.is_some() {
                self.assistance.end_session(session_id);
                if let Ok(mut context) = self.context.lock() {
                    context.end_session(session_id);
                }
                self.emit_terminal_presence(app);
                self.finish_governance();
                return Ok(RealtimeWorkerStatus {
                    running: false,
                    session_id: None,
                    segment_id: None,
                    context_epoch: None,
                    backend: None,
                    cloud_provider: None,
                    action_required: false,
                    presence_projection: None,
                    assistance: Vec::new(),
                    resource: None,
                    sidecar: self.sidecar_snapshot(),
                    capture_scope: None,
                    media_channels: Vec::new(),
                    usage: self.usage_snapshot(),
                });
            }
            thread::sleep(Duration::from_millis(4));
        }
        let _ = process.child.kill();
        let _ = process.child.wait();
        self.assistance.end_session(session_id);
        if let Ok(mut context) = self.context.lock() {
            context.end_session(session_id);
        }
        self.emit_terminal_presence(app);
        self.finish_governance();
        Ok(RealtimeWorkerStatus {
            running: false,
            session_id: None,
            segment_id: None,
            context_epoch: None,
            backend: None,
            cloud_provider: None,
            action_required: false,
            presence_projection: None,
            assistance: Vec::new(),
            resource: None,
            sidecar: self.sidecar_snapshot(),
            capture_scope: None,
            media_channels: Vec::new(),
            usage: self.usage_snapshot(),
        })
    }

    pub fn tool_result(
        &self,
        input: RealtimeWorkerToolResultInput,
    ) -> Result<(), RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        send_command(
            &process.input,
            &HostCommand::ToolResult {
                session_id: input.session_id,
                call_id: input.call_id,
                public_summary: input.public_summary,
                succeeded: input.succeeded,
            },
        )
    }

    pub fn set_input(&self, input: RealtimeWorkerSetInputInput) -> Result<(), RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        send_command(
            &process.input,
            &HostCommand::SetInput {
                session_id: input.session_id,
                microphone: input.microphone,
                video: input.video,
            },
        )
    }

    pub fn retry_media_channel(
        &self,
        input: RealtimeWorkerRetryMediaInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        if !matches!(
            input.channel.as_str(),
            "microphone"
                | "selected_window"
                | "selected_application_audio"
                | "fairy_render_reference"
        ) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let identity = self
            .active_identity
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .clone()
            .ok_or(RealtimeWorkerError::Unavailable)?;
        let source_sequence = self
            .capture_scope
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .as_ref()
            .map(|scope| scope.source_sequence)
            .ok_or(RealtimeWorkerError::Unavailable)?;
        send_command(
            &process.input,
            &HostCommand::RetryMediaChannel {
                session_id: identity.session_id,
                segment_id: identity.segment_id,
                context_epoch: identity.epoch,
                channel: input.channel,
                source_sequence,
            },
        )?;
        drop(guard);
        Ok(self.status())
    }

    pub fn replace_capture_source(
        &self,
        input: RealtimeWorkerReplaceSourceInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let decision = {
            let scope = self
                .capture_scope
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let scope = scope.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
            inspect_window(input.source_id, &scope.excluded_applications)
        };
        if !decision.is_safe() {
            return Err(RealtimeWorkerError::Protocol);
        }
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let privacy_paused = self
            .capture_scope
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .as_ref()
            .is_some_and(|scope| scope.privacy_paused);
        let pending = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            if privacy_paused {
                coordinator
                    .apply(RealtimeCoordinatorEvent::ResumePrivacy)
                    .map_err(|_| RealtimeWorkerError::Protocol)?;
                if let Some(pending) = coordinator.pending_context_rotation().cloned() {
                    Some(pending)
                } else if coordinator.local_backend_unloaded() {
                    None
                } else {
                    Some(
                        coordinator
                            .prepare_standby_context_rotation(ContextRotationReason::WindowChanged)
                            .map_err(|_| RealtimeWorkerError::Protocol)?,
                    )
                }
            } else {
                Some(
                    coordinator
                        .prepare_context_rotation(ContextRotationReason::WindowChanged)
                        .map_err(|_| RealtimeWorkerError::Protocol)?,
                )
            }
        };
        let Some(pending) = pending else {
            let restored_media = if let Ok(mut scope) = self.capture_scope.lock() {
                if let Some(scope) = scope.as_mut() {
                    scope.requested_source_id = Some(input.source_id);
                    scope.effective_source_id = Some(input.source_id);
                    scope.source_sequence = scope.source_sequence.saturating_add(1);
                    scope.pending_source_id = None;
                    scope.pending_source_sequence = None;
                    scope.privacy_paused = false;
                    scope.sensitive_category = None;
                    scope.error_code = None;
                    Some((scope.screen_enabled, scope.application_audio_enabled))
                } else {
                    None
                }
            } else {
                None
            };
            if let Ok(mut recovery) = self.recovery.lock() {
                if let Some(recovery) = recovery.as_mut() {
                    recovery.source_id = Some(input.source_id);
                    if let Some((screen_enabled, application_audio_enabled)) = restored_media {
                        recovery.screen_enabled = screen_enabled;
                        recovery.application_audio_enabled = application_audio_enabled;
                    }
                }
            }
            drop(guard);
            return Ok(self.status());
        };
        let carryover = self.build_context_carryover(
            &pending.current,
            &pending.current.segment_id,
            pending.next_epoch,
        )?;
        let (source_sequence, screen_enabled, application_audio_enabled) = {
            let scope = self
                .capture_scope
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let scope = scope.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
            (
                scope.source_sequence.saturating_add(1),
                scope.screen_enabled,
                scope.application_audio_enabled,
            )
        };
        let command = HostCommand::ReplaceCaptureSource {
            session_id: pending.current.session_id.clone(),
            segment_id: pending.current.segment_id.clone(),
            current_context_epoch: pending.current.epoch,
            next_context_epoch: pending.next_epoch,
            source_id: input.source_id,
            source_sequence,
            screen_enabled,
            application_audio_enabled,
            reason: pending.reason.as_str().to_owned(),
            carryover,
        };
        if send_command(&process.input, &command).is_err() {
            let _ = self
                .coordinator
                .lock()
                .ok()
                .and_then(|mut coordinator| coordinator.as_mut()?.fail_context_rotation().ok());
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Ok(mut scope) = self.capture_scope.lock() {
            if let Some(scope) = scope.as_mut() {
                scope.requested_source_id = Some(input.source_id);
                scope.pending_source_id = Some(input.source_id);
                scope.pending_source_sequence = Some(source_sequence);
            }
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = Some(ContextEpochIdentity {
                session_id: pending.current.session_id,
                segment_id: pending.current.segment_id,
                epoch: pending.next_epoch,
            });
        }
        drop(guard);
        Ok(self.status())
    }

    pub fn set_policy(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerSetPolicyInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let pending = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            coordinator
                .apply(RealtimeCoordinatorEvent::SetPolicy {
                    profile: input.activity_profile,
                    interaction_intensity: input.interaction_intensity,
                })
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let pending = coordinator.pending_context_rotation().cloned();
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(coordinator.presence_projection()),
            );
            pending
        };
        let Some(pending) = pending else {
            drop(guard);
            return Ok(self.status());
        };
        if let Ok(mut dialogue) = self.dialogue.lock() {
            if let Some(dialogue) = dialogue.as_mut() {
                dialogue.reset_epoch_policy();
            }
        }
        let profile_command = HostCommand::SetProfile {
            session_id: pending.current.session_id.clone(),
            activity_profile: input.activity_profile,
            interaction_intensity: input.interaction_intensity,
        };
        let carryover = match self.build_context_carryover(
            &pending.current,
            &pending.current.segment_id,
            pending.next_epoch,
        ) {
            Ok(carryover) => carryover,
            Err(error) => {
                if let Some(projection) = fail_context_rotation(&self.coordinator) {
                    let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                }
                return Err(error);
            }
        };
        let rotate_command = HostCommand::RotateContext {
            session_id: pending.current.session_id.clone(),
            segment_id: pending.current.segment_id.clone(),
            current_context_epoch: pending.current.epoch,
            next_context_epoch: pending.next_epoch,
            reason: pending.reason.as_str().to_owned(),
            carryover,
        };
        let send_result = {
            let mut active = self
                .active_identity
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let previous = active.clone();
            *active = Some(ContextEpochIdentity {
                session_id: pending.current.session_id,
                segment_id: pending.current.segment_id,
                epoch: pending.next_epoch,
            });
            let result = send_command(&process.input, &profile_command)
                .and_then(|_| send_command(&process.input, &rotate_command));
            if result.is_err() {
                *active = previous;
            }
            result
        };
        if send_result.is_err() {
            if let Ok(mut coordinator) = self.coordinator.lock() {
                if let Some(coordinator) = coordinator.as_mut() {
                    let _ = coordinator.fail_context_rotation();
                }
            }
            return Err(RealtimeWorkerError::Protocol);
        }
        drop(guard);
        Ok(self.status())
    }

    pub fn wake(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerWakeInput,
        local_ready: bool,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let mut loading = None;
        let transition = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            if coordinator.backend() == RealtimeBackendKind::LocalMiniCpmO45
                && coordinator.local_backend_unloaded()
                && !local_ready
            {
                return Err(RealtimeWorkerError::Unavailable);
            }
            let next_segment = (coordinator.backend() == RealtimeBackendKind::CloudLive
                || coordinator.local_backend_unloaded())
            .then(|| Uuid::new_v4().to_string());
            if coordinator.backend() == RealtimeBackendKind::LocalMiniCpmO45
                && coordinator.local_backend_unloaded()
            {
                loading = Some(
                    self.audio_focus
                        .model_resources
                        .reserve("minicpm", None)
                        .map_err(|_| RealtimeWorkerError::Busy)?,
                );
            }
            coordinator
                .prepare_wake(next_segment)
                .map_err(|_| RealtimeWorkerError::Protocol)?
        };
        let (command, next_identity) = match transition {
            RealtimeWakeTransition::Local(pending) => {
                let carryover = match self.build_context_carryover(
                    &pending.current,
                    &pending.current.segment_id,
                    pending.next_epoch,
                ) {
                    Ok(carryover) => carryover,
                    Err(error) => {
                        if let Some(projection) = fail_context_rotation(&self.coordinator) {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        return Err(error);
                    }
                };
                (
                    HostCommand::Resume {
                        session_id: pending.current.session_id.clone(),
                        carryover,
                    },
                    ContextEpochIdentity {
                        session_id: pending.current.session_id,
                        segment_id: pending.current.segment_id,
                        epoch: pending.next_epoch,
                    },
                )
            }
            RealtimeWakeTransition::LocalSegment(pending) => {
                let carryover = match self.build_context_carryover(
                    &pending.current,
                    &pending.next_segment_id,
                    1,
                ) {
                    Ok(carryover) => carryover,
                    Err(error) => {
                        if let Some(projection) = fail_context_rotation(&self.coordinator) {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        return Err(error);
                    }
                };
                let envelope = self
                    .recovery
                    .lock()
                    .map_err(|_| RealtimeWorkerError::Protocol)?
                    .clone()
                    .ok_or(RealtimeWorkerError::Unavailable)?;
                if let Ok(mut governor) = self.resource_governor.lock() {
                    *governor = Some(RealtimeResourceGovernor::new(0));
                }
                (
                    HostCommand::RecoverLocal {
                        session_id: pending.current.session_id.clone(),
                        current_segment_id: pending.current.segment_id.clone(),
                        current_context_epoch: pending.current.epoch,
                        next_segment_id: pending.next_segment_id.clone(),
                        local_omni: Box::new(self.launch.local_omni.clone()),
                        persona_snapshot: SecretString::from(envelope.persona_snapshot),
                        locale: envelope.locale,
                        activity_profile: envelope.activity_profile,
                        interaction_intensity: envelope.interaction_intensity,
                        voice_output: envelope.voice_output,
                        desktop_host_process_id: envelope.desktop_host_process_id,
                        source_id: envelope.source_id,
                        microphone_enabled: envelope.microphone_enabled,
                        screen_enabled: envelope.screen_enabled,
                        application_audio_enabled: envelope.application_audio_enabled,
                        online_assistance_enabled: envelope.online_assistance_enabled,
                        standby_wake: true,
                        carryover: Some(carryover),
                    },
                    ContextEpochIdentity {
                        session_id: pending.current.session_id,
                        segment_id: pending.next_segment_id,
                        epoch: 1,
                    },
                )
            }
            RealtimeWakeTransition::Cloud(pending) => {
                let carryover = match self.build_context_carryover(
                    &pending.current,
                    &pending.next_segment_id,
                    1,
                ) {
                    Ok(carryover) => carryover,
                    Err(error) => {
                        if let Some(projection) = fail_context_rotation(&self.coordinator) {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        return Err(error);
                    }
                };
                (
                    HostCommand::WakeSegment {
                        session_id: pending.current.session_id.clone(),
                        current_segment_id: pending.current.segment_id.clone(),
                        current_context_epoch: pending.current.epoch,
                        next_segment_id: pending.next_segment_id.clone(),
                        carryover,
                    },
                    ContextEpochIdentity {
                        session_id: pending.current.session_id,
                        segment_id: pending.next_segment_id,
                        epoch: 1,
                    },
                )
            }
        };
        let previous_identity = {
            let mut active = self
                .active_identity
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let previous = active.clone();
            *active = Some(next_identity);
            let mut reservation = process
                .model_reservation
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            if send_command(&process.input, &command).is_err() {
                *active = previous.clone();
                previous
            } else {
                if loading.is_some() {
                    *reservation = loading.take();
                }
                drop(reservation);
                drop(active);
                drop(guard);
                return Ok(self.status());
            }
        };
        if let Ok(mut active) = self.active_identity.lock() {
            *active = previous_identity;
        }
        {
            if let Ok(mut coordinator) = self.coordinator.lock() {
                if let Some(coordinator) = coordinator.as_mut() {
                    let _ = coordinator.fail_context_rotation();
                }
            }
        }
        Err(RealtimeWorkerError::Protocol)
    }

    pub fn pause_privacy(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerWakeInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let projection = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            coordinator
                .apply(RealtimeCoordinatorEvent::PausePrivacy)
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            coordinator.presence_projection()
        };
        if send_command(
            &process.input,
            &HostCommand::Pause {
                session_id: input.session_id,
            },
        )
        .is_err()
        {
            if let Ok(mut coordinator) = self.coordinator.lock() {
                if let Some(coordinator) = coordinator.as_mut() {
                    let _ = coordinator.fail_context_rotation();
                }
            }
            return Err(RealtimeWorkerError::Protocol);
        }
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(projection),
        );
        drop(guard);
        Ok(self.status())
    }

    pub fn resume_privacy(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerWakeInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let (pending, resumed_projection, resumed_identity) = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            coordinator
                .apply(RealtimeCoordinatorEvent::ResumePrivacy)
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            (
                coordinator.pending_context_rotation().cloned(),
                coordinator.presence_projection(),
                coordinator.active_identity().clone(),
            )
        };
        let Some(pending) = pending else {
            send_command(
                &process.input,
                &HostCommand::SetMediaPrivacy {
                    session_id: resumed_identity.session_id,
                    segment_id: resumed_identity.segment_id,
                    context_epoch: resumed_identity.epoch,
                    paused: false,
                },
            )
            .map_err(|_| RealtimeWorkerError::Protocol)?;
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(resumed_projection),
            );
            drop(guard);
            return Ok(self.status());
        };
        let carryover = match self.build_context_carryover(
            &pending.current,
            &pending.current.segment_id,
            pending.next_epoch,
        ) {
            Ok(carryover) => carryover,
            Err(error) => {
                if let Some(projection) = fail_context_rotation(&self.coordinator) {
                    let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                }
                return Err(error);
            }
        };
        let command = HostCommand::RotateContext {
            session_id: pending.current.session_id.clone(),
            segment_id: pending.current.segment_id.clone(),
            current_context_epoch: pending.current.epoch,
            next_context_epoch: pending.next_epoch,
            reason: pending.reason.as_str().to_owned(),
            carryover,
        };
        let next_identity = ContextEpochIdentity {
            session_id: pending.current.session_id,
            segment_id: pending.current.segment_id,
            epoch: pending.next_epoch,
        };
        let send_result = {
            let mut active = self
                .active_identity
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let previous = active.clone();
            *active = Some(next_identity);
            let result = send_command(&process.input, &command);
            if result.is_err() {
                *active = previous;
            }
            result
        };
        if send_result.is_err() {
            if let Ok(mut coordinator) = self.coordinator.lock() {
                if let Some(coordinator) = coordinator.as_mut() {
                    let _ = coordinator.fail_context_rotation();
                }
            }
            return Err(RealtimeWorkerError::Protocol);
        }
        drop(guard);
        Ok(self.status())
    }

    pub fn extend_presence(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerExtendInput,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        {
            let guard = self
                .process
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
            if process.session_id.as_deref() != Some(input.session_id.as_str()) {
                return Err(RealtimeWorkerError::Protocol);
            }
        }
        let projection = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            coordinator
                .apply(RealtimeCoordinatorEvent::ExtendPresence {
                    additional_minutes: input.additional_minutes,
                })
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            coordinator.presence_projection()
        };
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(projection),
        );
        Ok(self.status())
    }

    pub fn speech_state(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerSpeechStateInput,
    ) -> Result<(), RealtimeWorkerError> {
        if let Some(projection) =
            govern_fairy_speech_state(&input, &self.coordinator, &self.dialogue)?
        {
            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
        }
        Ok(())
    }

    fn usage_snapshot(&self) -> RealtimeWorkerUsage {
        self.usage
            .lock()
            .map(|usage| usage.clone())
            .unwrap_or_default()
    }

    fn resource_snapshot(&self) -> Option<RealtimeResourceSnapshot> {
        self.resource_governor
            .lock()
            .ok()
            .and_then(|governor| governor.as_ref().map(RealtimeResourceGovernor::snapshot))
    }

    fn sidecar_snapshot(&self) -> RealtimeSidecarSnapshot {
        self.sidecar_supervisor
            .lock()
            .map(|supervisor| supervisor.snapshot())
            .unwrap_or(RealtimeSidecarSnapshot {
                restart_used: true,
                quarantined: true,
                context_interrupted: true,
                failure_count: 2,
                error_code: Some("SIDECAR_SUPERVISOR_UNAVAILABLE".to_owned()),
            })
    }

    fn capture_scope_snapshot(&self) -> Option<RealtimeCaptureScopePublicState> {
        self.capture_scope
            .lock()
            .ok()
            .and_then(|scope| scope.as_ref().map(RealtimeCaptureScopeState::public))
    }

    fn media_channels_snapshot(&self) -> Vec<RealtimeMediaChannelPublicState> {
        self.media_channels
            .lock()
            .map(|channels| channels.values().cloned().collect())
            .unwrap_or_default()
    }

    pub fn sidecar_quarantined(&self) -> bool {
        self.sidecar_snapshot().quarantined
    }

    pub fn clear_sidecar_quarantine(&self) -> Result<(), RealtimeSidecarSupervisorError> {
        self.sidecar_supervisor
            .lock()
            .map_err(|_| RealtimeSidecarSupervisorError::Store)?
            .clear_for_explicit_verify()
    }

    fn build_context_carryover(
        &self,
        current: &ContextEpochIdentity,
        target_segment_id: &str,
        next_context_epoch: u64,
    ) -> Result<RealtimeContextCarryover, RealtimeWorkerError> {
        build_context_carryover_from_state(
            &self.context,
            &self.assistance,
            &self.coordinator,
            current,
            target_segment_id,
            next_context_epoch,
        )
    }

    fn finish_governance(&self) {
        let ending_session = self.coordinator.lock().ok().and_then(|coordinator| {
            coordinator
                .as_ref()
                .map(|state| state.active_identity().session_id.clone())
        });
        if let Ok(mut coordinator) = self.coordinator.lock() {
            if let Some(state) = coordinator.as_mut() {
                let _ = state.apply(RealtimeCoordinatorEvent::End);
            }
            *coordinator = None;
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = None;
        }
        if let Ok(mut dialogue) = self.dialogue.lock() {
            if let Some(director) = dialogue.as_mut() {
                director.invalidate_speech();
            }
            *dialogue = None;
        }
        if let Some(session_id) = ending_session {
            if let Ok(mut context) = self.context.lock() {
                context.end_session(&session_id);
            }
        }
        if let Ok(mut governor) = self.resource_governor.lock() {
            *governor = None;
        }
        if let Ok(mut recovery) = self.recovery.lock() {
            *recovery = None;
        }
        if let Ok(mut capture_scope) = self.capture_scope.lock() {
            *capture_scope = None;
        }
        if let Ok(mut media_channels) = self.media_channels.lock() {
            media_channels.clear();
        }
    }

    fn current_presence_projection(&self) -> Option<RealtimePresenceProjection> {
        self.coordinator.lock().ok().and_then(|state| {
            state
                .as_ref()
                .map(RealtimeCoordinatorState::presence_projection)
        })
    }

    fn emit_terminal_presence(&self, app: &AppHandle) {
        let projection = self.coordinator.lock().ok().and_then(|mut coordinator| {
            let state = coordinator.as_mut()?;
            let previous_sequence = state.presence_projection().sequence;
            state.apply(RealtimeCoordinatorEvent::End).ok()?;
            let projection = state.presence_projection();
            (projection.sequence != previous_sequence).then_some(projection)
        });
        if let Some(projection) = projection {
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(projection),
            );
        }
    }

    fn restore_governance(&self, coordinator: Option<RealtimeCoordinatorState>) {
        let identity = coordinator
            .as_ref()
            .map(|state| state.active_identity().clone());
        if let Ok(mut state) = self.coordinator.lock() {
            *state = coordinator;
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = identity;
        }
    }

    fn restore_dialogue(&self, director: Option<RealtimeDialogueDirector>) {
        if let Ok(mut dialogue) = self.dialogue.lock() {
            *dialogue = director;
        }
    }

    fn restore_context(&self, authority: RealtimeContextAuthority) {
        if let Ok(mut context) = self.context.lock() {
            *context = authority;
        }
    }

    fn restore_resource_governor(&self, governor: Option<RealtimeResourceGovernor>) {
        if let Ok(mut current) = self.resource_governor.lock() {
            *current = governor;
        }
    }

    fn restore_recovery(&self, envelope: Option<RealtimeRecoveryEnvelope>) {
        if let Ok(mut recovery) = self.recovery.lock() {
            *recovery = envelope;
        }
    }

    fn cleanup_finished_governance(&self) {
        let action_required = self
            .coordinator
            .lock()
            .ok()
            .and_then(|state| {
                state
                    .as_ref()
                    .map(RealtimeCoordinatorState::action_required)
            })
            .unwrap_or(false);
        if !action_required {
            self.finish_governance();
        }
    }

    fn governance_projection(
        &self,
    ) -> Option<(
        String,
        u64,
        RealtimeBackendKind,
        Option<RealtimeCloudProviderKind>,
        bool,
    )> {
        self.coordinator.lock().ok().and_then(|state| {
            state.as_ref().map(|state| {
                (
                    state.active_identity().segment_id.clone(),
                    state.active_identity().epoch,
                    state.active_segment().backend,
                    state.active_segment().cloud_provider,
                    state.action_required(),
                )
            })
        })
    }
}

fn build_context_carryover_from_state(
    context: &Mutex<RealtimeContextAuthority>,
    assistance: &RealtimeAssistanceRouter,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    current: &ContextEpochIdentity,
    target_segment_id: &str,
    next_context_epoch: u64,
) -> Result<RealtimeContextCarryover, RealtimeWorkerError> {
    let activity_profile = coordinator
        .lock()
        .map_err(|_| RealtimeWorkerError::Protocol)?
        .as_ref()
        .ok_or(RealtimeWorkerError::Unavailable)?
        .requested_activity_profile();
    let assistance = assistance.snapshot(Some(&current.session_id));
    let unfinished = assistance
        .iter()
        .find(|state| {
            matches!(
                state.status.as_str(),
                "queued" | "running" | "awaiting_approval"
            )
        })
        .map(|state| (state.request_id.as_str(), state.status.as_str()));
    context
        .lock()
        .map_err(|_| RealtimeWorkerError::Protocol)?
        .build(
            &current.session_id,
            &current.segment_id,
            current.epoch,
            target_segment_id,
            next_context_epoch,
            activity_profile,
            unfinished,
        )
        .map_err(|_| RealtimeWorkerError::Protocol)
}

fn validate_capture_scope(input: &RealtimeWorkerStartInput) -> Result<(), RealtimeWorkerError> {
    normalize_excluded_applications(&input.excluded_applications)
        .map_err(|_| RealtimeWorkerError::Protocol)?;
    if input.capture_mode == RealtimeCaptureMode::FollowForeground
        && input.backend != RealtimeBackendKind::LocalMiniCpmO45
    {
        return Err(RealtimeWorkerError::Protocol);
    }
    validate_backend_start(&BackendStartRequest {
        session_id: input.session_id.clone(),
        segment_id: "tauri-pending-segment".to_owned(),
        context_epoch: 1,
        backend: input.backend,
        cloud_provider: input.cloud_provider,
        cloud_credential_present: input.backend == RealtimeBackendKind::CloudLive,
        persona_snapshot_present: true,
        activity_profile: input.activity_profile,
        interaction_intensity: input.interaction_intensity,
        voice_output: input.voice_output,
        desktop_host_process_id: std::process::id(),
        source_id: input.source_id,
        microphone_enabled: input.microphone_enabled,
        screen_enabled: input.screen_enabled,
        application_audio_enabled: input.application_audio_enabled,
        online_assistance_enabled: input.online_assistance_enabled,
    })
    .map_err(|_| RealtimeWorkerError::Protocol)
}

impl RealtimeWorkerManager {
    pub fn shutdown(&self) {
        self.closed.store(true, Ordering::Release);
        self.assistance.shutdown();
        if let Ok(mut guard) = self.process.lock() {
            if let Some(process) = guard.as_mut() {
                process.expected_shutdown.store(true, Ordering::Release);
                process.terminal.store(true, Ordering::Release);
                if let Some(session_id) = process.session_id.as_ref() {
                    let _ = send_command(
                        &process.input,
                        &HostCommand::Stop {
                            session_id: session_id.clone(),
                        },
                    );
                    // Let the domain owner close audio/capture and its Omni child first.
                    let deadline = Instant::now() + WORKER_STOP_GRACE;
                    while Instant::now() < deadline {
                        if matches!(process.child.try_wait(), Ok(Some(_))) {
                            break;
                        }
                        thread::sleep(Duration::from_millis(4));
                    }
                }
                let _ = process.child.kill();
                let _ = process.child.wait();
            }
            *guard = None;
        }
        self.finish_governance();
    }
}

impl Drop for RealtimeWorkerManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn spawn_worker(
    launch: &RealtimeWorkerLaunch,
    app: AppHandle,
    governance: WorkerGovernanceHandles,
) -> Result<WorkerProcess, RealtimeWorkerError> {
    let WorkerGovernanceHandles {
        resources,
        model_reservation,
        assistance,
        usage,
        active_identity,
        coordinator,
        dialogue,
        context,
        resource_governor,
        sidecar_supervisor,
        recovery,
        capture_scope,
        media_channels,
        local_omni,
    } = governance;
    if !launch.program.is_file() {
        return Err(RealtimeWorkerError::Unavailable);
    }
    if let Some(parent) = launch.log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let log = File::options()
        .create(true)
        .append(true)
        .open(&launch.log_path)?;
    let mut command = Command::new(&launch.program);
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn()?;
    let input = Arc::new(Mutex::new(
        child.stdin.take().ok_or(RealtimeWorkerError::Protocol)?,
    ));
    let reader_input = Arc::clone(&input);
    let output = child.stdout.take().ok_or(RealtimeWorkerError::Protocol)?;
    let mut reader = BufReader::new(output);
    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let started_at = Instant::now();
    let reader_started_at = started_at;
    let expected_shutdown = Arc::new(AtomicBool::new(false));
    let reader_expected_shutdown = Arc::clone(&expected_shutdown);
    let terminal = Arc::new(AtomicBool::new(false));
    let reader_terminal = Arc::clone(&terminal);
    let reader_reservation = Arc::clone(&model_reservation);
    let tick_input = Arc::clone(&input);
    let tick_coordinator = Arc::clone(&coordinator);
    let tick_expected_shutdown = Arc::clone(&expected_shutdown);
    let tick_terminal = Arc::clone(&terminal);
    let tick_app = app.clone();
    let reader_capture_scope = Arc::clone(&capture_scope);
    let reader_media_channels = Arc::clone(&media_channels);
    let monitor_input = Arc::clone(&input);
    let monitor_active_identity = Arc::clone(&active_identity);
    let monitor_coordinator = Arc::clone(&coordinator);
    let monitor_dialogue = Arc::clone(&dialogue);
    let monitor_context = Arc::clone(&context);
    let monitor_assistance = Arc::clone(&assistance);
    let monitor_capture_scope = Arc::clone(&capture_scope);
    let monitor_recovery = Arc::clone(&recovery);
    let monitor_expected_shutdown = Arc::clone(&expected_shutdown);
    let monitor_terminal = Arc::clone(&terminal);
    let monitor_app = app.clone();
    thread::spawn(move || {
        let first = read_frame::<Value>(&mut reader);
        let ready = matches!(
            &first,
            Ok(Some(value))
                if value.get("type").and_then(Value::as_str) == Some("ready")
                    && value.get("protocol").and_then(Value::as_str) == Some(REALTIME_PROTOCOL)
        );
        let _ = ready_tx.send(ready);
        if let Ok(Some(value)) = first {
            let _ = app.emit(REALTIME_WORKER_EVENT, value);
        }
        loop {
            match read_frame::<Value>(&mut reader) {
                Ok(Some(value)) => {
                    if !worker_event_matches_active_identity(&value, &active_identity) {
                        continue;
                    }
                    if !coordinator_allows_worker_event(&value, &coordinator) {
                        continue;
                    }
                    if value.get("type").and_then(Value::as_str) == Some("session_state")
                        && matches!(
                            value.get("status").and_then(Value::as_str),
                            Some("active" | "failed" | "stopped" | "cancelled" | "completed")
                        )
                    {
                        if let Ok(mut reservation) = reader_reservation.lock() {
                            reservation.take();
                        }
                    }
                    observe_media_projection(
                        &value,
                        &reader_capture_scope,
                        &reader_media_channels,
                        &recovery,
                    );
                    if handle_local_sidecar_failure(
                        &value,
                        &app,
                        &reader_input,
                        &active_identity,
                        &coordinator,
                        &dialogue,
                        &resource_governor,
                        &sidecar_supervisor,
                        &recovery,
                        &local_omni,
                        &resources,
                        &reader_reservation,
                    ) {
                        continue;
                    }
                    if handle_local_backend_unloaded(&value, &app, &coordinator) {
                        continue;
                    }
                    if let Ok(mut context) = context.lock() {
                        context.observe_worker_event(&value);
                    }
                    if is_meaningful_worker_activity(&value) {
                        if let Ok(mut coordinator) = coordinator.lock() {
                            if let Some(coordinator) = coordinator.as_mut() {
                                let _ = coordinator
                                    .record_meaningful_activity(elapsed_ms(reader_started_at));
                            }
                        }
                    }
                    let value = govern_speech_state(value, &dialogue);
                    if value.get("type").and_then(Value::as_str) == Some("context_rotated") {
                        if let Some(projection) = commit_context_rotation_event(
                            &value,
                            &coordinator,
                            &dialogue,
                            elapsed_ms(reader_started_at),
                        ) {
                            if let Some(session_id) =
                                value.get("session_id").and_then(Value::as_str)
                            {
                                if let Ok(mut context) = context.lock() {
                                    context.commit_rotation(session_id);
                                }
                            }
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                            let _ = app.emit(REALTIME_WORKER_EVENT, value);
                        } else if coordinator_has_pending_transition(&coordinator) {
                            if let Some(projection) = fail_context_rotation(&coordinator) {
                                let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                            }
                        }
                        continue;
                    }
                    if value.get("type").and_then(Value::as_str) == Some("segment_woken") {
                        if let Some(projection) = commit_segment_wake_event(
                            &value,
                            &coordinator,
                            &dialogue,
                            elapsed_ms(reader_started_at),
                        ) {
                            if let Some(session_id) =
                                value.get("session_id").and_then(Value::as_str)
                            {
                                if let Ok(mut context) = context.lock() {
                                    context.commit_rotation(session_id);
                                }
                            }
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                            let _ = app.emit(REALTIME_WORKER_EVENT, value);
                        } else if coordinator_has_pending_transition(&coordinator) {
                            if let Some(projection) = fail_context_rotation(&coordinator) {
                                let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                            }
                        }
                        continue;
                    }
                    if value.get("type").and_then(Value::as_str) == Some("resource_sample") {
                        if let Some(policy) = resource_policy_for_sample(
                            &value,
                            &resource_governor,
                            elapsed_ms(reader_started_at),
                        ) {
                            let identity = active_identity
                                .lock()
                                .ok()
                                .and_then(|active| active.clone());
                            if let Some(identity) = identity {
                                if policy.level == RealtimeResourceLevel::DeviceRemoved {
                                    if let Ok(mut supervisor) = sidecar_supervisor.lock() {
                                        supervisor.quarantine("GPU_DEVICE_REMOVED");
                                    }
                                    let projection =
                                        coordinator.lock().ok().and_then(|mut coordinator| {
                                            let coordinator = coordinator.as_mut()?;
                                            let _ = coordinator
                                                .apply(RealtimeCoordinatorEvent::BackendFailed);
                                            Some(coordinator.presence_projection())
                                        });
                                    if let Some(projection) = projection {
                                        let _ = app.emit(
                                            REALTIME_WORKER_EVENT,
                                            presence_projection_payload(projection),
                                        );
                                    }
                                    emit_sidecar_state(
                                        &app,
                                        &sidecar_supervisor,
                                        "quarantined",
                                        None,
                                    );
                                }
                                let command = HostCommand::SetResourcePolicy {
                                    session_id: identity.session_id,
                                    segment_id: identity.segment_id,
                                    context_epoch: identity.epoch,
                                    policy,
                                };
                                if send_command(&reader_input, &command).is_err() {
                                    let _ = app.emit(
                                        REALTIME_WORKER_EVENT,
                                        serde_json::json!({
                                            "type": "resource_pressure",
                                            "code": "RESOURCE_POLICY_UNAVAILABLE"
                                        }),
                                    );
                                }
                            }
                        }
                        continue;
                    }
                    if value.get("type").and_then(Value::as_str) == Some("resource_policy_applied")
                        && !resource_policy_acknowledged(&value, &resource_governor)
                    {
                        continue;
                    }
                    if let Some(projection) = govern_presence_event(&value, &coordinator) {
                        let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                    }
                    if value.get("type").and_then(Value::as_str) == Some("perception_candidate") {
                        match govern_dialogue_candidate(&value, &coordinator, &dialogue) {
                            DialogueGovernance::Projection(projected) => {
                                let _ = app.emit(REALTIME_WORKER_EVENT, projected.clone());
                                if projected.get("type").and_then(Value::as_str)
                                    == Some("assistance_request")
                                {
                                    assistance.route(app.clone(), projected);
                                }
                            }
                            DialogueGovernance::Rotate(pending) => {
                                let carryover = match build_context_carryover_from_state(
                                    &context,
                                    &assistance,
                                    &coordinator,
                                    &pending.current,
                                    &pending.current.segment_id,
                                    pending.next_epoch,
                                ) {
                                    Ok(carryover) => carryover,
                                    Err(_) => {
                                        if let Some(projection) =
                                            fail_context_rotation(&coordinator)
                                        {
                                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                                        }
                                        continue;
                                    }
                                };
                                let command = HostCommand::RotateContext {
                                    session_id: pending.current.session_id.clone(),
                                    segment_id: pending.current.segment_id.clone(),
                                    current_context_epoch: pending.current.epoch,
                                    next_context_epoch: pending.next_epoch,
                                    reason: pending.reason.as_str().to_owned(),
                                    carryover,
                                };
                                if send_command(&reader_input, &command).is_ok() {
                                    if let Ok(mut active) = active_identity.lock() {
                                        *active = Some(ContextEpochIdentity {
                                            session_id: pending.current.session_id,
                                            segment_id: pending.current.segment_id,
                                            epoch: pending.next_epoch,
                                        });
                                    }
                                } else if let Some(projection) = fail_context_rotation(&coordinator)
                                {
                                    let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                                }
                            }
                            DialogueGovernance::Suppress => {}
                        }
                        continue;
                    }
                    if is_terminal_worker_event(&value) {
                        reader_terminal.store(true, Ordering::Release);
                    }
                    let backend_failed = value.get("type").and_then(Value::as_str)
                        == Some("session_state")
                        && value.get("status").and_then(Value::as_str) == Some("failed");
                    if backend_failed {
                        let projection = if let Ok(mut state) = coordinator.lock() {
                            if let Some(state) = state.as_mut() {
                                let _ = state.fail_context_rotation();
                                Some(state.presence_projection())
                            } else {
                                None
                            }
                        } else {
                            None
                        };
                        if let Some(projection) = projection {
                            let _ = app.emit(
                                REALTIME_WORKER_EVENT,
                                presence_projection_payload(projection),
                            );
                        }
                    }
                    if value.get("type").and_then(Value::as_str) == Some("usage") {
                        if let Ok(next) =
                            serde_json::from_value::<RealtimeWorkerUsage>(value.clone())
                        {
                            if let Ok(mut current) = usage.lock() {
                                *current = next;
                            }
                        }
                    }
                    if value.get("type").and_then(Value::as_str) == Some("presence") {
                        continue;
                    }
                    let _ = app.emit(REALTIME_WORKER_EVENT, value);
                }
                Ok(None) | Err(_) => {
                    if let Ok(mut reservation) = reader_reservation.lock() {
                        reservation.take();
                    }
                    if !reader_expected_shutdown.load(Ordering::Acquire) {
                        if let Some(projection) =
                            govern_host_presence(RealtimePresenceState::Error, &coordinator)
                        {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        let _ = app.emit(
                            REALTIME_WORKER_EVENT,
                            serde_json::json!({
                                "type": "worker_interrupted",
                                "error_code": "WORKER_INTERRUPTED"
                            }),
                        );
                    }
                    return;
                }
            }
        }
    });
    thread::spawn(move || {
        run_coordinator_ticker(
            tick_app,
            tick_input,
            tick_coordinator,
            tick_expected_shutdown,
            tick_terminal,
            started_at,
        );
    });
    thread::spawn(move || {
        run_realtime_environment_monitor(
            monitor_app,
            monitor_input,
            monitor_active_identity,
            monitor_coordinator,
            monitor_dialogue,
            monitor_context,
            monitor_assistance,
            monitor_capture_scope,
            monitor_recovery,
            monitor_expected_shutdown,
            monitor_terminal,
        );
    });
    match ready_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(true) => Ok(WorkerProcess {
            audio_lease: None,
            model_reservation,
            child,
            input,
            session_id: None,
            expected_shutdown,
            terminal,
        }),
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            Err(RealtimeWorkerError::Protocol)
        }
    }
}

fn observe_media_projection(
    value: &Value,
    capture_scope: &Mutex<Option<RealtimeCaptureScopeState>>,
    media_channels: &Mutex<BTreeMap<String, RealtimeMediaChannelPublicState>>,
    recovery: &Mutex<Option<RealtimeRecoveryEnvelope>>,
) {
    match value.get("type").and_then(Value::as_str) {
        Some("media_channel_state") => {
            let Some(channel) = value.get("channel").and_then(Value::as_str) else {
                return;
            };
            let Some(sequence) = value.get("sequence").and_then(Value::as_u64) else {
                return;
            };
            let Some(status) = value.get("status").and_then(Value::as_str) else {
                return;
            };
            let Ok(mut channels) = media_channels.lock() else {
                return;
            };
            if channels
                .get(channel)
                .is_some_and(|current| current.sequence >= sequence)
            {
                return;
            }
            channels.insert(
                channel.to_owned(),
                RealtimeMediaChannelPublicState {
                    channel: channel.to_owned(),
                    sequence,
                    status: status.to_owned(),
                    error_code: value
                        .get("error_code")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                },
            );
        }
        Some("capture_source_changed") => {
            let Some(source_id) = value.get("source_id").and_then(Value::as_u64) else {
                return;
            };
            let Some(source_sequence) = value.get("source_sequence").and_then(Value::as_u64) else {
                return;
            };
            let status = value.get("status").and_then(Value::as_str);
            let Ok(mut scope) = capture_scope.lock() else {
                return;
            };
            let Some(scope) = scope.as_mut() else {
                return;
            };
            if scope.pending_source_id != Some(source_id)
                || scope.pending_source_sequence != Some(source_sequence)
            {
                return;
            }
            scope.pending_source_id = None;
            scope.pending_source_sequence = None;
            scope.source_sequence = source_sequence;
            if status == Some("active") {
                scope.requested_source_id = Some(source_id);
                scope.effective_source_id = Some(source_id);
                scope.privacy_paused = false;
                scope.sensitive_category = None;
                scope.error_code = None;
                if let Ok(mut recovery) = recovery.lock() {
                    if let Some(recovery) = recovery.as_mut() {
                        recovery.source_id = Some(source_id);
                        recovery.screen_enabled = scope.screen_enabled;
                        recovery.application_audio_enabled = scope.application_audio_enabled;
                    }
                }
            } else {
                scope.effective_source_id = None;
                scope.privacy_paused = true;
                scope.sensitive_category = Some(RealtimeSensitiveCategory::ProtectedContent);
                scope.error_code = Some("CAPTURE_SOURCE_UNAVAILABLE".to_owned());
            }
        }
        _ => {}
    }
}

#[allow(clippy::too_many_arguments)]
fn run_realtime_environment_monitor(
    app: AppHandle,
    input: Arc<Mutex<ChildStdin>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
    context: Arc<Mutex<RealtimeContextAuthority>>,
    assistance: Arc<RealtimeAssistanceRouter>,
    capture_scope: Arc<Mutex<Option<RealtimeCaptureScopeState>>>,
    recovery: Arc<Mutex<Option<RealtimeRecoveryEnvelope>>>,
    expected_shutdown: Arc<AtomicBool>,
    terminal: Arc<AtomicBool>,
) {
    while !expected_shutdown.load(Ordering::Acquire) && !terminal.load(Ordering::Acquire) {
        thread::sleep(Duration::from_millis(350));
        let Some(identity) = active_identity
            .lock()
            .ok()
            .and_then(|identity| identity.clone())
        else {
            continue;
        };
        let Some(scope_snapshot) = capture_scope.lock().ok().and_then(|scope| scope.clone()) else {
            continue;
        };
        let ambient = crate::ambient_context::sample_ambient_device_facts();
        if !scope_snapshot.screen_enabled {
            if let Ok(mut director) = dialogue.lock() {
                if let Some(director) = director.as_mut() {
                    director.set_environment(RealtimeEnvironmentGates {
                        locked: ambient.locked,
                        do_not_disturb: ambient.do_not_disturb,
                        sensitive_window: false,
                        privacy_paused: false,
                    });
                }
            }
            continue;
        }
        let decision = match scope_snapshot.mode {
            RealtimeCaptureMode::SelectedWindow => scope_snapshot
                .requested_source_id
                .or(scope_snapshot.pending_source_id)
                .map(|source_id| inspect_window(source_id, &scope_snapshot.excluded_applications))
                .unwrap_or_else(crate::realtime_privacy::RealtimeWindowDecision::unavailable),
            RealtimeCaptureMode::FollowForeground => {
                sample_foreground_window(&scope_snapshot.excluded_applications)
            }
        };
        let sensitive = decision.sensitive.is_some();
        if let Ok(mut director) = dialogue.lock() {
            if let Some(director) = director.as_mut() {
                director.set_environment(RealtimeEnvironmentGates {
                    locked: ambient.locked,
                    do_not_disturb: ambient.do_not_disturb,
                    sensitive_window: sensitive,
                    privacy_paused: scope_snapshot.privacy_paused || sensitive,
                });
            }
        }
        if sensitive {
            let (coordinator_needs_pause, coordinator_has_pending) = coordinator
                .lock()
                .ok()
                .and_then(|coordinator| {
                    coordinator.as_ref().map(|coordinator| {
                        (
                            coordinator.presence_projection().state
                                != RealtimePresenceState::PrivacyPaused,
                            coordinator.pending_context_rotation().is_some()
                                || coordinator.pending_local_wake().is_some()
                                || coordinator.pending_cloud_wake().is_some(),
                        )
                    })
                })
                .unwrap_or((false, false));
            let changed = capture_scope.lock().ok().is_some_and(|mut scope| {
                let Some(scope) = scope.as_mut() else {
                    return false;
                };
                let changed = (coordinator_needs_pause && !coordinator_has_pending)
                    || !scope.privacy_paused
                    || scope.sensitive_category != decision.sensitive;
                scope.privacy_paused = true;
                scope.sensitive_category = decision.sensitive;
                scope.error_code = decision
                    .sensitive
                    .map(|category| category.public_code().to_owned());
                if decision.source_id.is_none() {
                    scope.effective_source_id = None;
                }
                changed
            });
            if changed {
                if let Ok(mut recovery) = recovery.lock() {
                    if let Some(recovery) = recovery.as_mut() {
                        recovery.source_id = None;
                        recovery.screen_enabled = false;
                        recovery.application_audio_enabled = false;
                    }
                }
                let presence = coordinator.lock().ok().and_then(|mut coordinator| {
                    let coordinator = coordinator.as_mut()?;
                    if coordinator_has_pending {
                        return None;
                    }
                    coordinator
                        .apply(RealtimeCoordinatorEvent::PausePrivacy)
                        .ok()?;
                    Some(coordinator.presence_projection())
                });
                let _ = send_command(
                    &input,
                    &HostCommand::SetMediaPrivacy {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.epoch,
                        paused: true,
                    },
                );
                if let Some(presence) = presence {
                    let _ = app.emit(REALTIME_WORKER_EVENT, presence_projection_payload(presence));
                }
                emit_capture_scope_projection(&app, &identity, &capture_scope);
            }
            continue;
        }
        let Some(next_source_id) = decision.source_id else {
            continue;
        };
        let needs_replacement = scope_snapshot.privacy_paused
            || (scope_snapshot.mode == RealtimeCaptureMode::FollowForeground
                && scope_snapshot.effective_source_id != Some(next_source_id));
        if !needs_replacement || scope_snapshot.pending_source_id.is_some() {
            continue;
        }
        let mut unloaded_resume = None;
        let pending = coordinator.lock().ok().and_then(|mut coordinator| {
            let coordinator = coordinator.as_mut()?;
            if coordinator.pending_context_rotation().is_some()
                || coordinator.pending_local_wake().is_some()
                || coordinator.pending_cloud_wake().is_some()
            {
                return None;
            }
            if scope_snapshot.privacy_paused {
                coordinator
                    .apply(RealtimeCoordinatorEvent::ResumePrivacy)
                    .ok()?;
                if let Some(pending) = coordinator.pending_context_rotation().cloned() {
                    Some(pending)
                } else if coordinator.local_backend_unloaded() {
                    unloaded_resume = Some((
                        coordinator.active_identity().clone(),
                        coordinator.presence_projection(),
                    ));
                    None
                } else {
                    coordinator
                        .prepare_standby_context_rotation(ContextRotationReason::WindowChanged)
                        .ok()
                }
            } else {
                coordinator
                    .prepare_context_rotation(ContextRotationReason::WindowChanged)
                    .ok()
            }
        });
        if let Some((identity, projection)) = unloaded_resume {
            if let Ok(mut scope) = capture_scope.lock() {
                if let Some(scope) = scope.as_mut() {
                    scope.requested_source_id = Some(next_source_id);
                    scope.effective_source_id = Some(next_source_id);
                    scope.source_sequence = scope.source_sequence.saturating_add(1);
                    scope.pending_source_id = None;
                    scope.pending_source_sequence = None;
                    scope.privacy_paused = false;
                    scope.sensitive_category = None;
                    scope.error_code = None;
                }
            }
            if let Ok(mut recovery) = recovery.lock() {
                if let Some(recovery) = recovery.as_mut() {
                    recovery.source_id = Some(next_source_id);
                    recovery.screen_enabled = scope_snapshot.screen_enabled;
                    recovery.application_audio_enabled = scope_snapshot.application_audio_enabled;
                }
            }
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(projection),
            );
            emit_capture_scope_projection(&app, &identity, &capture_scope);
            continue;
        }
        let Some(pending) = pending else {
            continue;
        };
        let carryover = match build_context_carryover_from_state(
            &context,
            &assistance,
            &coordinator,
            &pending.current,
            &pending.current.segment_id,
            pending.next_epoch,
        ) {
            Ok(carryover) => carryover,
            Err(_) => {
                let _ = fail_context_rotation(&coordinator);
                continue;
            }
        };
        let next_source_sequence = scope_snapshot.source_sequence.saturating_add(1);
        let command = HostCommand::ReplaceCaptureSource {
            session_id: pending.current.session_id.clone(),
            segment_id: pending.current.segment_id.clone(),
            current_context_epoch: pending.current.epoch,
            next_context_epoch: pending.next_epoch,
            source_id: next_source_id,
            source_sequence: next_source_sequence,
            screen_enabled: scope_snapshot.screen_enabled,
            application_audio_enabled: scope_snapshot.application_audio_enabled,
            reason: pending.reason.as_str().to_owned(),
            carryover,
        };
        if send_command(&input, &command).is_err() {
            let _ = fail_context_rotation(&coordinator);
            continue;
        }
        if let Ok(mut scope) = capture_scope.lock() {
            if let Some(scope) = scope.as_mut() {
                if scope.mode == RealtimeCaptureMode::SelectedWindow {
                    scope.requested_source_id = Some(next_source_id);
                }
                scope.pending_source_id = Some(next_source_id);
                scope.pending_source_sequence = Some(next_source_sequence);
            }
        }
        if let Ok(mut active) = active_identity.lock() {
            *active = Some(ContextEpochIdentity {
                session_id: pending.current.session_id,
                segment_id: pending.current.segment_id,
                epoch: pending.next_epoch,
            });
        }
    }
}

fn emit_capture_scope_projection(
    app: &AppHandle,
    identity: &ContextEpochIdentity,
    capture_scope: &Mutex<Option<RealtimeCaptureScopeState>>,
) {
    let projection = capture_scope
        .lock()
        .ok()
        .and_then(|scope| scope.as_ref().map(RealtimeCaptureScopeState::public));
    if let Some(projection) = projection {
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            serde_json::json!({
                "type": "capture_scope_state",
                "session_id": identity.session_id,
                "segment_id": identity.segment_id,
                "context_epoch": identity.epoch,
                "mode": projection.mode,
                "source_sequence": projection.source_sequence,
                "source_available": projection.source_available,
                "privacy_paused": projection.privacy_paused,
                "sensitive_category": projection.sensitive_category,
                "error_code": projection.error_code,
            }),
        );
    }
}

#[allow(clippy::too_many_arguments)]
fn handle_local_sidecar_failure(
    value: &Value,
    app: &AppHandle,
    input: &Arc<Mutex<ChildStdin>>,
    active_identity: &Mutex<Option<ContextEpochIdentity>>,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
    resource_governor: &Mutex<Option<RealtimeResourceGovernor>>,
    sidecar_supervisor: &Mutex<RealtimeSidecarSupervisor>,
    recovery: &Mutex<Option<RealtimeRecoveryEnvelope>>,
    local_omni: &LocalOmniLaunch,
    resources: &Arc<crate::model_resources::ModelResources>,
    reservation: &Mutex<Option<crate::model_resources::ModelReservation>>,
) -> bool {
    let Ok(WorkerEvent::LocalSidecarFailure {
        session_id,
        segment_id,
        context_epoch,
        error_code,
        candidate_emitted,
    }) = serde_json::from_value::<WorkerEvent>(value.clone())
    else {
        return false;
    };
    if let Ok(mut previous) = reservation.lock() {
        previous.take();
    }

    let pending_wake_failure = coordinator.lock().ok().and_then(|mut coordinator| {
        let coordinator = coordinator.as_mut()?;
        let pending = coordinator.pending_local_wake()?;
        if pending.current.session_id != session_id || pending.next_segment_id != segment_id {
            return None;
        }
        let previous = pending.current.clone();
        coordinator.fail_context_rotation().ok()?;
        Some((previous, coordinator.presence_projection()))
    });
    if let Some((previous, projection)) = pending_wake_failure {
        if let Ok(mut active) = active_identity.lock() {
            *active = Some(previous);
        }
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(projection),
        );
        let _ = app.emit(REALTIME_WORKER_EVENT, value.clone());
        return true;
    }

    let failure_projection = {
        let Ok(mut coordinator) = coordinator.lock() else {
            return true;
        };
        let Some(coordinator) = coordinator.as_mut() else {
            return true;
        };
        let identity = coordinator.active_identity();
        if identity.session_id != session_id
            || identity.segment_id != segment_id
            || identity.epoch != context_epoch
            || coordinator.backend() != RealtimeBackendKind::LocalMiniCpmO45
            || coordinator
                .apply(RealtimeCoordinatorEvent::BackendFailed)
                .is_err()
        {
            return true;
        }
        coordinator.presence_projection()
    };

    let decision = sidecar_supervisor
        .lock()
        .map(|mut supervisor| supervisor.record_failure(&error_code, candidate_emitted))
        .unwrap_or(RealtimeSidecarDecision::Quarantine);
    if decision == RealtimeSidecarDecision::Quarantine {
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(failure_projection),
        );
        emit_sidecar_state(app, sidecar_supervisor, "quarantined", None);
        return true;
    }

    let Some(envelope) = recovery.lock().ok().and_then(|value| value.clone()) else {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    };
    let persona_digest = serde_json::from_str::<Value>(&envelope.persona_snapshot)
        .ok()
        .and_then(|persona| {
            persona
                .get("persona_digest")
                .and_then(Value::as_str)
                .map(str::to_owned)
        });
    let Some(persona_digest) = persona_digest else {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    };
    let next_segment_id = Uuid::new_v4().to_string();
    let recovery_projection = {
        let Ok(mut coordinator_guard) = coordinator.lock() else {
            quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
            return true;
        };
        let Some(coordinator_state) = coordinator_guard.as_mut() else {
            drop(coordinator_guard);
            quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
            return true;
        };
        if coordinator_state
            .apply(RealtimeCoordinatorEvent::RecoverLocalSegment {
                segment_id: next_segment_id.clone(),
                persona_digest,
            })
            .is_err()
        {
            drop(coordinator_guard);
            quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
            return true;
        }
        coordinator_state.presence_projection()
    };
    let dialogue_ready = dialogue
        .lock()
        .ok()
        .and_then(|mut dialogue| {
            dialogue
                .as_mut()
                .map(|dialogue| dialogue.commit_backend_segment(next_segment_id.clone()))
        })
        .unwrap_or(false);
    if !dialogue_ready {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    }
    if let Ok(mut active) = active_identity.lock() {
        *active = Some(ContextEpochIdentity {
            session_id: session_id.clone(),
            segment_id: next_segment_id.clone(),
            epoch: 1,
        });
    } else {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    }
    if let Ok(mut governor) = resource_governor.lock() {
        *governor = Some(RealtimeResourceGovernor::new(0));
    }
    let command = HostCommand::RecoverLocal {
        session_id,
        current_segment_id: segment_id,
        current_context_epoch: context_epoch,
        next_segment_id: next_segment_id.clone(),
        local_omni: Box::new(local_omni.clone()),
        persona_snapshot: SecretString::from(envelope.persona_snapshot),
        locale: envelope.locale,
        activity_profile: envelope.activity_profile,
        interaction_intensity: envelope.interaction_intensity,
        voice_output: envelope.voice_output,
        desktop_host_process_id: envelope.desktop_host_process_id,
        source_id: envelope.source_id,
        microphone_enabled: envelope.microphone_enabled,
        screen_enabled: envelope.screen_enabled,
        application_audio_enabled: envelope.application_audio_enabled,
        online_assistance_enabled: envelope.online_assistance_enabled,
        standby_wake: false,
        carryover: None,
    };
    let loading = match resources.reserve("minicpm", None) {
        Ok(loading) => loading,
        Err(_) => {
            quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
            return true;
        }
    };
    let Ok(mut retained) = reservation.lock() else {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    };
    if send_command(input, &command).is_err() {
        quarantine_recovery_failure(app, coordinator, sidecar_supervisor);
        return true;
    }
    *retained = Some(loading);
    drop(retained);
    let _ = app.emit(
        REALTIME_WORKER_EVENT,
        presence_projection_payload(recovery_projection),
    );
    emit_sidecar_state(app, sidecar_supervisor, "restarting", Some(next_segment_id));
    true
}

fn handle_local_backend_unloaded(
    value: &Value,
    app: &AppHandle,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
) -> bool {
    let Ok(WorkerEvent::LocalBackendUnloaded {
        session_id,
        segment_id,
        context_epoch,
    }) = serde_json::from_value::<WorkerEvent>(value.clone())
    else {
        return false;
    };
    let projection = coordinator.lock().ok().and_then(|mut coordinator| {
        let coordinator = coordinator.as_mut()?;
        let identity = coordinator.active_identity();
        if identity.session_id != session_id
            || identity.segment_id != segment_id
            || identity.epoch != context_epoch
            || coordinator
                .apply(RealtimeCoordinatorEvent::LocalBackendUnloaded)
                .is_err()
        {
            return None;
        }
        Some(coordinator.presence_projection())
    });
    if let Some(projection) = projection {
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(projection),
        );
        let _ = app.emit(REALTIME_WORKER_EVENT, value.clone());
    }
    true
}

fn quarantine_recovery_failure(
    app: &AppHandle,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    sidecar_supervisor: &Mutex<RealtimeSidecarSupervisor>,
) {
    if let Ok(mut supervisor) = sidecar_supervisor.lock() {
        supervisor.quarantine("LOCAL_SIDECAR_RECOVERY_FAILED");
    }
    let projection = coordinator.lock().ok().and_then(|mut coordinator| {
        let coordinator = coordinator.as_mut()?;
        if !coordinator.action_required() {
            let _ = coordinator.apply(RealtimeCoordinatorEvent::BackendFailed);
        }
        Some(coordinator.presence_projection())
    });
    if let Some(projection) = projection {
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            presence_projection_payload(projection),
        );
    }
    emit_sidecar_state(app, sidecar_supervisor, "quarantined", None);
}

fn emit_sidecar_state(
    app: &AppHandle,
    sidecar_supervisor: &Mutex<RealtimeSidecarSupervisor>,
    status: &'static str,
    segment_id: Option<String>,
) {
    let snapshot = sidecar_supervisor
        .lock()
        .ok()
        .map(|supervisor| supervisor.snapshot());
    if let Some(snapshot) = snapshot {
        let _ = app.emit(
            REALTIME_WORKER_EVENT,
            serde_json::json!({
                "type": "sidecar_recovery",
                "status": status,
                "segment_id": segment_id,
                "restart_used": snapshot.restart_used,
                "quarantined": snapshot.quarantined,
                "context_interrupted": snapshot.context_interrupted,
                "failure_count": snapshot.failure_count,
                "error_code": snapshot.error_code,
            }),
        );
    }
}

fn govern_presence_event(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
) -> Option<Value> {
    let event = serde_json::from_value::<WorkerEvent>(value.clone()).ok()?;
    let projection = coordinator
        .lock()
        .ok()?
        .as_mut()?
        .project_worker_event(&event)?;
    Some(presence_projection_payload(projection))
}

fn govern_host_presence(
    state: RealtimePresenceState,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
) -> Option<Value> {
    let projection = coordinator
        .lock()
        .ok()?
        .as_mut()?
        .project_host_state(state)?;
    Some(presence_projection_payload(projection))
}

fn presence_projection_payload(projection: RealtimePresenceProjection) -> Value {
    serde_json::json!({
        "type": "presence_projection",
        "session_id": projection.session_id,
        "segment_id": projection.segment_id,
        "context_epoch": projection.context_epoch,
        "sequence": projection.sequence,
        "state": projection.state,
        "level": projection.level,
        "persona_digest": projection.persona_digest,
        "requested_activity_profile": projection.requested_activity_profile,
        "effective_activity": projection.effective_activity,
        "interaction_intensity": projection.interaction_intensity,
        "backend": projection.backend,
        "cloud_provider": projection.cloud_provider,
        "standby_reason": projection.standby_reason,
        "wake_available": projection.wake_available,
        "duration_extension_required": projection.duration_extension_required,
    })
}

fn govern_fairy_speech_state(
    input: &RealtimeWorkerSpeechStateInput,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
) -> Result<Option<Value>, RealtimeWorkerError> {
    let director = dialogue.lock().map_err(|_| RealtimeWorkerError::Protocol)?;
    let Some(director) = director.as_ref() else {
        return Err(RealtimeWorkerError::Unavailable);
    };
    if input.speech_generation != director.speech_generation() {
        return Ok(None);
    }
    let mut coordinator = coordinator
        .lock()
        .map_err(|_| RealtimeWorkerError::Protocol)?;
    let Some(coordinator) = coordinator.as_mut() else {
        return Err(RealtimeWorkerError::Unavailable);
    };
    let identity = coordinator.active_identity();
    if input.session_id != identity.session_id
        || input.segment_id != identity.segment_id
        || input.context_epoch != identity.epoch
    {
        return Err(RealtimeWorkerError::Protocol);
    }
    Ok(coordinator
        .project_fairy_speech(input.speaking)
        .map(presence_projection_payload))
}

fn run_coordinator_ticker(
    app: AppHandle,
    input: Arc<Mutex<ChildStdin>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    expected_shutdown: Arc<AtomicBool>,
    terminal: Arc<AtomicBool>,
    started_at: Instant,
) {
    while !expected_shutdown.load(Ordering::Acquire) && !terminal.load(Ordering::Acquire) {
        thread::sleep(Duration::from_millis(250));
        let (actions, projection, identity) = {
            let Ok(mut coordinator) = coordinator.lock() else {
                return;
            };
            let Some(coordinator) = coordinator.as_mut() else {
                drop(coordinator);
                continue;
            };
            let actions = coordinator.tick(elapsed_ms(started_at));
            let projection = (!actions.is_empty()).then(|| coordinator.presence_projection());
            let identity = coordinator.active_identity().clone();
            (actions, projection, identity)
        };
        if let Some(projection) = projection {
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(projection),
            );
        }
        for action in actions {
            match action {
                RealtimeCoordinatorAction::EnterLocalStandby
                | RealtimeCoordinatorAction::EnterCloudStandby
                | RealtimeCoordinatorAction::RequireDurationExtension => {
                    if send_command(
                        &input,
                        &HostCommand::Pause {
                            session_id: identity.session_id.clone(),
                        },
                    )
                    .is_err()
                    {
                        if let Some(projection) = fail_context_rotation(&coordinator) {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        return;
                    }
                }
                RealtimeCoordinatorAction::RequestLocalUnload => {
                    if send_command(
                        &input,
                        &HostCommand::UnloadLocalBackend {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.epoch,
                        },
                    )
                    .is_err()
                    {
                        if let Some(projection) = fail_context_rotation(&coordinator) {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                        }
                        return;
                    }
                }
            }
        }
    }
}

fn is_meaningful_worker_activity(value: &Value) -> bool {
    match value.get("type").and_then(Value::as_str) {
        Some("barge_in") => true,
        Some("public_caption") => {
            value.get("speaker").and_then(Value::as_str) == Some("user")
                && value.get("stable").and_then(Value::as_bool) == Some(true)
        }
        Some("perception_candidate") => value
            .get("grounding")
            .and_then(Value::as_array)
            .is_some_and(|items| {
                items.iter().any(|item| {
                    item.as_str().is_some_and(|grounding| {
                        grounding.starts_with("current_window:")
                            || grounding == "current_user_utterance"
                    })
                })
            }),
        _ => false,
    }
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
}

fn resource_policy_for_sample(
    value: &Value,
    governor: &Mutex<Option<RealtimeResourceGovernor>>,
    now_ms: u64,
) -> Option<RealtimeResourcePolicy> {
    let event = serde_json::from_value::<WorkerEvent>(value.clone()).ok()?;
    let WorkerEvent::ResourceSample {
        allocation_failure_count,
        inference_latency_ms,
        capture_frame_backlog,
        renderer_healthy,
        target_changed,
        ..
    } = event
    else {
        return None;
    };
    let memory = sample_realtime_gpu_memory();
    let sample = RealtimeResourceSample {
        budget_bytes: memory.budget_bytes,
        current_usage_bytes: memory.current_usage_bytes,
        allocation_failure_count,
        inference_latency_ms,
        capture_frame_backlog,
        device_removed: memory.device_removed,
        renderer_healthy,
        target_changed,
    };
    governor
        .lock()
        .ok()?
        .as_mut()?
        .observe(sample, now_ms)
        .ok()
        .flatten()
}

fn resource_policy_acknowledged(
    value: &Value,
    governor: &Mutex<Option<RealtimeResourceGovernor>>,
) -> bool {
    let Ok(WorkerEvent::ResourcePolicyApplied { policy, .. }) =
        serde_json::from_value::<WorkerEvent>(value.clone())
    else {
        return false;
    };
    governor
        .lock()
        .ok()
        .and_then(|governor| governor.as_ref().map(RealtimeResourceGovernor::snapshot))
        .is_some_and(|snapshot| snapshot.policy == policy)
}

fn commit_context_rotation_event(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
    now_ms: u64,
) -> Option<Value> {
    let event = serde_json::from_value::<WorkerEvent>(value.clone()).ok()?;
    let WorkerEvent::ContextRotated {
        session_id,
        segment_id,
        context_epoch,
        reason,
    } = event
    else {
        return None;
    };
    {
        let coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_ref()?;
        let pending = coordinator.pending_context_rotation()?;
        if pending.current.session_id != session_id
            || pending.current.segment_id != segment_id
            || pending.next_epoch != context_epoch
            || pending.reason.as_str() != reason
        {
            return None;
        }
    }
    {
        let mut director = dialogue.lock().ok()?;
        let director = director.as_mut()?;
        if director
            .context_epoch()
            .checked_add(1)
            .is_none_or(|next| next != context_epoch)
            || !director.commit_context_epoch(context_epoch)
        {
            return None;
        }
    }
    let projection = {
        let mut coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_mut()?;
        coordinator
            .commit_context_rotation_at(context_epoch, now_ms)
            .ok()?;
        coordinator.presence_projection()
    };
    Some(presence_projection_payload(projection))
}

fn commit_segment_wake_event(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
    now_ms: u64,
) -> Option<Value> {
    let event = serde_json::from_value::<WorkerEvent>(value.clone()).ok()?;
    let WorkerEvent::SegmentWoken {
        session_id,
        segment_id,
        context_epoch,
    } = event
    else {
        return None;
    };
    if context_epoch != 1 {
        return None;
    }
    {
        let coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_ref()?;
        let pending_matches = coordinator.pending_cloud_wake().is_some_and(|pending| {
            pending.current.session_id == session_id && pending.next_segment_id == segment_id
        }) || coordinator.pending_local_wake().is_some_and(|pending| {
            pending.current.session_id == session_id && pending.next_segment_id == segment_id
        });
        if !pending_matches {
            return None;
        }
    }
    {
        let mut director = dialogue.lock().ok()?;
        let director = director.as_mut()?;
        if !director.commit_backend_segment(segment_id.clone()) {
            return None;
        }
    }
    let projection = {
        let mut coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_mut()?;
        if coordinator.pending_local_wake().is_some() {
            coordinator.commit_local_wake(&segment_id, now_ms).ok()?;
        } else {
            coordinator.commit_cloud_wake(&segment_id, now_ms).ok()?;
        }
        coordinator.presence_projection()
    };
    Some(presence_projection_payload(projection))
}

fn fail_context_rotation(coordinator: &Mutex<Option<RealtimeCoordinatorState>>) -> Option<Value> {
    let projection = {
        let mut coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_mut()?;
        coordinator.fail_context_rotation().ok()?;
        coordinator.presence_projection()
    };
    Some(presence_projection_payload(projection))
}

fn coordinator_has_pending_transition(
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
) -> bool {
    coordinator
        .lock()
        .ok()
        .and_then(|coordinator| {
            coordinator.as_ref().map(|coordinator| {
                coordinator.pending_context_rotation().is_some()
                    || coordinator.pending_local_wake().is_some()
                    || coordinator.pending_cloud_wake().is_some()
            })
        })
        .unwrap_or(false)
}

fn govern_dialogue_candidate(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
) -> DialogueGovernance {
    let Ok(event) = serde_json::from_value::<WorkerEvent>(value.clone()) else {
        return DialogueGovernance::Suppress;
    };
    let WorkerEvent::PerceptionCandidate {
        session_id,
        segment_id,
        context_epoch,
        sequence,
        candidate,
    } = event
    else {
        return DialogueGovernance::Suppress;
    };
    let (effective_activity, interaction_intensity, pending_rotation) = {
        let Ok(mut coordinator) = coordinator.lock() else {
            return DialogueGovernance::Suppress;
        };
        let Some(coordinator) = coordinator.as_mut() else {
            return DialogueGovernance::Suppress;
        };
        let identity = coordinator.active_identity();
        if session_id != identity.session_id
            || segment_id != identity.segment_id
            || context_epoch != identity.epoch
            || coordinator.pending_context_rotation().is_some()
            || candidate.persona_digest != coordinator.persona_digest()
            || !candidate.is_valid()
        {
            return DialogueGovernance::Suppress;
        }
        let Some(observation) = coordinator.observe_activity_candidate(sequence, &candidate) else {
            return DialogueGovernance::Suppress;
        };
        let pending = if observation.switched {
            let Ok(pending) =
                coordinator.prepare_context_rotation(ContextRotationReason::ProfileChanged)
            else {
                return DialogueGovernance::Suppress;
            };
            Some(pending)
        } else {
            None
        };
        (
            observation.effective_activity,
            coordinator.interaction_intensity(),
            pending,
        )
    };
    let Ok(mut director) = dialogue.lock() else {
        return DialogueGovernance::Suppress;
    };
    let Some(director) = director.as_mut() else {
        return DialogueGovernance::Suppress;
    };
    if let Some(pending) = pending_rotation {
        director.reset_epoch_policy();
        return DialogueGovernance::Rotate(pending);
    }
    let decision = director.evaluate_with_activity(
        &session_id,
        &segment_id,
        context_epoch,
        sequence,
        candidate,
        effective_activity,
        interaction_intensity,
    );
    match decision {
        RealtimeDialogueDecision::Speak(projection) => {
            DialogueGovernance::Projection(serde_json::json!({
                "type": "public_caption",
                "session_id": projection.session_id,
                "segment_id": projection.segment_id,
                "context_epoch": projection.context_epoch,
                "sequence": projection.sequence,
                "text": projection.text,
                "stable": projection.stable,
                "speaker": "assistant",
                "activity": projection.activity,
                "intent": projection.intent,
                "response_to_user": projection.response_to_user,
                "speech_output": projection.speech_output,
                "speech_generation": projection.speech_generation,
                "persona_digest": projection.persona_digest,
            }))
        }
        RealtimeDialogueDecision::RequestAssistance(candidate) => {
            DialogueGovernance::Projection(serde_json::json!({
                "type": "assistance_request",
                "session_id": candidate.session_id,
                "segment_id": candidate.segment_id,
                "context_epoch": candidate.context_epoch,
                "request_id": format!(
                    "realtime-assistance-{}-{}",
                    candidate.segment_id,
                    candidate.sequence
                ),
                "public_intent": candidate.public_question,
                "activity": candidate.activity,
                "intent": candidate.intent,
                "needs_online_assistance": candidate.needs_online_assistance,
                "persona_digest": candidate.persona_digest,
            }))
        }
        RealtimeDialogueDecision::Listen | RealtimeDialogueDecision::Suppress(_) => {
            DialogueGovernance::Suppress
        }
    }
}

fn govern_speech_state(
    mut value: Value,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
) -> Value {
    let event_type = value.get("type").and_then(Value::as_str);
    let state = value.get("state").and_then(Value::as_str);
    let Ok(mut director) = dialogue.lock() else {
        return value;
    };
    let Some(director) = director.as_mut() else {
        return value;
    };
    if event_type == Some("barge_in") {
        let generation = director.barge_in();
        if let Some(object) = value.as_object_mut() {
            object.insert("speech_generation".to_owned(), generation.into());
        }
    } else if event_type == Some("presence") && state == Some("analyzing") {
        director.user_speech_stopped();
    } else if event_type == Some("presence") && state == Some("speaking") {
        director.set_fairy_speaking(true);
    } else if event_type == Some("session_state")
        && value
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|status| {
                matches!(status, "completed" | "failed" | "cancelled" | "interrupted")
            })
    {
        director.invalidate_speech();
    }
    value
}

fn worker_event_matches_active_identity(
    value: &Value,
    active_identity: &Mutex<Option<ContextEpochIdentity>>,
) -> bool {
    let event_type = value.get("type").and_then(Value::as_str);
    if matches!(
        event_type,
        Some(
            "ready"
                | "pong"
                | "worker_interrupted"
                | "context_rotated"
                | "segment_woken"
                | "local_backend_unloaded"
        )
    ) {
        return true;
    }
    let Ok(active) = active_identity.lock() else {
        return false;
    };
    let Some(active) = active.as_ref() else {
        return false;
    };
    value.get("session_id").and_then(Value::as_str) == Some(active.session_id.as_str())
        && value.get("segment_id").and_then(Value::as_str) == Some(active.segment_id.as_str())
        && value.get("context_epoch").and_then(Value::as_u64) == Some(active.epoch)
}

fn coordinator_allows_worker_event(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
) -> bool {
    let event_type = value.get("type").and_then(Value::as_str);
    if matches!(
        event_type,
        Some(
            "ready"
                | "pong"
                | "worker_interrupted"
                | "context_rotated"
                | "segment_woken"
                | "local_sidecar_failure"
                | "local_backend_unloaded"
        )
    ) || (event_type == Some("session_state")
        && value.get("status").and_then(Value::as_str) == Some("failed"))
    {
        return true;
    }
    let Ok(coordinator) = coordinator.lock() else {
        return false;
    };
    let Some(coordinator) = coordinator.as_ref() else {
        return false;
    };
    let Some(session_id) = value.get("session_id").and_then(Value::as_str) else {
        return false;
    };
    let Some(segment_id) = value.get("segment_id").and_then(Value::as_str) else {
        return false;
    };
    let Some(context_epoch) = value.get("context_epoch").and_then(Value::as_u64) else {
        return false;
    };
    coordinator.accepts_result(session_id, segment_id, context_epoch)
}

fn reap_finished_process(process: &mut WorkerProcess) -> bool {
    if process.child.try_wait().ok().flatten().is_some() {
        return true;
    }
    if !process.terminal.load(Ordering::Acquire) {
        return false;
    }
    process.expected_shutdown.store(true, Ordering::Release);
    let _ = process.child.kill();
    let _ = process.child.wait();
    true
}

fn is_terminal_worker_event(value: &Value) -> bool {
    value.get("type").and_then(Value::as_str) == Some("session_state")
        && value
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|status| {
                matches!(status, "completed" | "failed" | "cancelled" | "interrupted")
            })
}

fn send_command(
    input: &Arc<Mutex<ChildStdin>>,
    command: &HostCommand,
) -> Result<(), RealtimeWorkerError> {
    let mut input = input.lock().map_err(|_| RealtimeWorkerError::Protocol)?;
    write_frame(&mut *input, command).map_err(|_| RealtimeWorkerError::Protocol)?;
    input.flush()?;
    Ok(())
}

pub fn development_realtime_launch(
    data_dir: &Path,
    resource_dir: &Path,
    manifest: &OmniModelManifest,
) -> RealtimeWorkerLaunch {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    RealtimeWorkerLaunch {
        program: crate_root.join("target/debug/fairy-realtime-worker.exe"),
        log_path: data_dir.join("logs/realtime-worker.log"),
        quarantine_path: data_dir.join("realtime/omni-quarantine.json"),
        local_omni: local_omni_launch(data_dir, resource_dir, manifest),
    }
}

pub fn bundled_realtime_launch(
    data_dir: &Path,
    resource_dir: &Path,
    manifest: &OmniModelManifest,
) -> RealtimeWorkerLaunch {
    RealtimeWorkerLaunch {
        program: resource_dir.join("runtime/realtime-worker/fairy-realtime-worker.exe"),
        log_path: data_dir.join("logs/realtime-worker.log"),
        quarantine_path: data_dir.join("realtime/omni-quarantine.json"),
        local_omni: local_omni_launch(data_dir, resource_dir, manifest),
    }
}

fn local_omni_launch(
    data_dir: &Path,
    resource_dir: &Path,
    manifest: &OmniModelManifest,
) -> LocalOmniLaunch {
    LocalOmniLaunch {
        runtime_path: resource_dir.join("runtime/omni/fairy-omni-runtime.exe"),
        manifest_path: resource_dir.join("omni/minicpm-o-4.5.json"),
        model_root: data_dir
            .join("models/minicpm-o-4.5")
            .join(&manifest.version),
        manifest_digest: manifest.manifest_digest.clone(),
        model_version: manifest.version.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_map_to_separate_credential_accounts() {
        assert_eq!(
            RealtimeCloudProviderKind::GeminiLive.credential_provider(),
            "gemini"
        );
        assert_eq!(
            RealtimeCloudProviderKind::GlmRealtimeFlash.credential_provider(),
            "zhipu"
        );
        assert_eq!(
            RealtimeCloudProviderKind::GlmRealtimeAir.credential_provider(),
            "zhipu"
        );
    }

    #[test]
    fn development_launch_uses_workspace_debug_binary() {
        let manifest =
            crate::omni_model_catalog::bundled_minicpm_o45_manifest().expect("bundled manifest");
        let launch = development_realtime_launch(
            Path::new("C:/fairy-data"),
            Path::new("C:/fairy-resources"),
            &manifest,
        );
        assert!(launch
            .program
            .ends_with("target/debug/fairy-realtime-worker.exe"));
        assert!(launch
            .local_omni
            .runtime_path
            .ends_with("runtime/omni/fairy-omni-runtime.exe"));
    }

    #[test]
    fn capture_and_process_audio_require_one_selected_window() {
        let mut input = RealtimeWorkerStartInput {
            session_id: "session-1".to_owned(),
            resolution_token: "a".repeat(64),
            locale: "en-AU".to_owned(),
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            activity_profile: RealtimeActivityProfile::Game,
            interaction_intensity: RealtimeInteractionIntensity::Standard,
            voice_output: RealtimeVoiceOutput::ProviderNativeVoice,
            source_id: None,
            microphone_enabled: true,
            screen_enabled: true,
            application_audio_enabled: false,
            online_assistance_enabled: false,
            cloud_microphone_upload_consent: true,
            cloud_screen_upload_consent: true,
            capture_mode: RealtimeCaptureMode::SelectedWindow,
            excluded_applications: Vec::new(),
        };
        assert!(validate_capture_scope(&input).is_err());

        input.source_id = Some(42);
        input.screen_enabled = false;
        input.application_audio_enabled = true;
        assert!(validate_capture_scope(&input).is_err());

        input.screen_enabled = true;
        assert!(validate_capture_scope(&input).is_ok());

        input.capture_mode = RealtimeCaptureMode::FollowForeground;
        assert!(validate_capture_scope(&input).is_err());
        input.backend = RealtimeBackendKind::LocalMiniCpmO45;
        input.cloud_provider = None;
        input.voice_output = RealtimeVoiceOutput::FairyVoice;
        assert!(validate_capture_scope(&input).is_ok());
    }

    #[test]
    fn media_channel_projection_is_monotonic_and_content_free() {
        let channels = Mutex::new(BTreeMap::new());
        let scope = Mutex::new(None);
        let recovery = Mutex::new(None);
        observe_media_projection(
            &serde_json::json!({
                "type": "media_channel_state",
                "channel": "microphone",
                "sequence": 2,
                "status": "unavailable",
                "error_code": "MICROPHONE_UNAVAILABLE"
            }),
            &scope,
            &channels,
            &recovery,
        );
        observe_media_projection(
            &serde_json::json!({
                "type": "media_channel_state",
                "channel": "microphone",
                "sequence": 1,
                "status": "active",
                "error_code": null
            }),
            &scope,
            &channels,
            &recovery,
        );
        let channels = channels.lock().expect("channel projection");
        let microphone = channels.get("microphone").expect("microphone");
        assert_eq!(microphone.sequence, 2);
        assert_eq!(microphone.status, "unavailable");
        assert_eq!(
            microphone.error_code.as_deref(),
            Some("MICROPHONE_UNAVAILABLE")
        );
    }

    #[test]
    fn source_ack_requires_the_exact_pending_sequence() {
        let scope = Mutex::new(Some(RealtimeCaptureScopeState {
            mode: RealtimeCaptureMode::FollowForeground,
            requested_source_id: Some(42),
            effective_source_id: Some(42),
            source_sequence: 1,
            pending_source_id: Some(84),
            pending_source_sequence: Some(2),
            privacy_paused: false,
            sensitive_category: None,
            error_code: None,
            excluded_applications: Vec::new(),
            screen_enabled: true,
            application_audio_enabled: false,
        }));
        let channels = Mutex::new(BTreeMap::new());
        let recovery = Mutex::new(None);
        for sequence in [1, 2] {
            observe_media_projection(
                &serde_json::json!({
                    "type": "capture_source_changed",
                    "source_id": 84,
                    "source_sequence": sequence,
                    "status": "active",
                    "error_code": null
                }),
                &scope,
                &channels,
                &recovery,
            );
        }
        let scope = scope.lock().expect("capture scope");
        let scope = scope.as_ref().expect("active scope");
        assert_eq!(scope.effective_source_id, Some(84));
        assert_eq!(scope.source_sequence, 2);
        assert!(scope.pending_source_id.is_none());
    }

    #[test]
    fn worker_stop_grace_stays_within_the_full_duplex_budget() {
        assert!(WORKER_STOP_GRACE <= Duration::from_millis(80));
    }

    #[test]
    fn only_terminal_session_events_mark_a_worker_for_reaping() {
        assert!(is_terminal_worker_event(&serde_json::json!({
            "type": "session_state",
            "session_id": "session-1",
            "status": "failed"
        })));
        assert!(!is_terminal_worker_event(&serde_json::json!({
            "type": "session_state",
            "session_id": "session-1",
            "status": "active"
        })));
        assert!(!is_terminal_worker_event(&serde_json::json!({
            "type": "presence",
            "session_id": "session-1",
            "state": "completed"
        })));
    }

    #[test]
    fn worker_events_are_fenced_by_tauri_owned_segment_identity() {
        let identity = Mutex::new(Some(ContextEpochIdentity {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            epoch: 2,
        }));
        assert!(worker_event_matches_active_identity(
            &serde_json::json!({
                "type": "presence",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 2
            }),
            &identity,
        ));
        assert!(!worker_event_matches_active_identity(
            &serde_json::json!({
                "type": "public_caption",
                "session_id": "session-1",
                "segment_id": "stale-segment",
                "context_epoch": 2
            }),
            &identity,
        ));
        assert!(!worker_event_matches_active_identity(
            &serde_json::json!({
                "type": "usage",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 1
            }),
            &identity,
        ));
    }

    fn dialogue() -> Mutex<Option<RealtimeDialogueDirector>> {
        Mutex::new(RealtimeDialogueDirector::new(
            "session-1".to_owned(),
            "segment-1".to_owned(),
            1,
            "a".repeat(64),
            RealtimeVoiceOutput::FairyVoice,
        ))
    }

    fn coordinator_with_profile(
        activity_profile: RealtimeActivityProfile,
    ) -> Mutex<Option<RealtimeCoordinatorState>> {
        Mutex::new(Some(
            RealtimeCoordinatorState::start(RealtimeCoordinatorStart {
                session_id: "session-1".to_owned(),
                segment_id: "segment-1".to_owned(),
                persona_digest: "a".repeat(64),
                backend: RealtimeBackendKind::CloudLive,
                cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
                activity_profile,
                interaction_intensity: RealtimeInteractionIntensity::Standard,
                presence_max_minutes: 240,
                local_keep_warm_minutes: 10,
            })
            .expect("coordinator"),
        ))
    }

    fn coordinator() -> Mutex<Option<RealtimeCoordinatorState>> {
        coordinator_with_profile(RealtimeActivityProfile::Game)
    }

    fn candidate_event(grounding: serde_json::Value) -> Value {
        serde_json::json!({
            "type": "perception_candidate",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "context_epoch": 1,
            "sequence": 1,
            "decision": "speak",
            "activity": "game",
            "confidence": 0.9,
            "intent": "answer",
            "grounding": grounding,
            "text": "Move back.",
            "urgency": 0.4,
            "needs_online_assistance": false,
            "response_to_user": true,
            "stable": true,
            "persona_digest": "a".repeat(64)
        })
    }

    fn auto_activity_event(sequence: u64) -> Value {
        serde_json::json!({
            "type": "perception_candidate",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "context_epoch": 1,
            "sequence": sequence,
            "decision": "speak",
            "activity": "game",
            "confidence": 0.9,
            "intent": "comment",
            "grounding": ["current_window: stable game activity"],
            "text": format!("Game event {sequence}."),
            "urgency": 0.4,
            "needs_online_assistance": false,
            "response_to_user": false,
            "stable": true,
            "persona_digest": "a".repeat(64)
        })
    }

    #[test]
    fn grounded_candidate_becomes_an_approved_assistant_caption() {
        let DialogueGovernance::Projection(projected) = govern_dialogue_candidate(
            &candidate_event(serde_json::json!(["current_user_utterance"])),
            &coordinator(),
            &dialogue(),
        ) else {
            panic!("approved projection")
        };
        assert_eq!(projected["type"], "public_caption");
        assert_eq!(projected["speaker"], "assistant");
        assert_eq!(projected["speech_output"], "fairy_voice");
        assert_eq!(projected["speech_generation"], 1);
        assert_eq!(projected["persona_digest"], "a".repeat(64));
    }

    #[test]
    fn ungrounded_candidate_is_suppressed_without_reemitting_its_body() {
        let event = candidate_event(serde_json::json!([]));
        assert!(matches!(
            govern_dialogue_candidate(&event, &coordinator(), &dialogue()),
            DialogueGovernance::Suppress
        ));
    }

    #[test]
    fn barge_in_advances_the_native_speech_generation() {
        let projected = govern_speech_state(
            serde_json::json!({
                "type": "barge_in",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 1
            }),
            &dialogue(),
        );
        assert_eq!(projected["speech_generation"], 2);
    }

    #[test]
    fn assistance_candidate_remains_a_request_and_never_executes_a_tool() {
        let mut event = candidate_event(serde_json::json!(["current_user_utterance"]));
        event["decision"] = serde_json::json!("request_assistance");
        event["intent"] = serde_json::json!("assist");
        event["needs_online_assistance"] = serde_json::json!(true);
        let DialogueGovernance::Projection(projected) =
            govern_dialogue_candidate(&event, &coordinator(), &dialogue())
        else {
            panic!("assistance projection")
        };
        assert_eq!(projected["type"], "assistance_request");
        assert_eq!(projected["needs_online_assistance"], true);
        assert!(projected.get("tool_name").is_none());
        assert!(projected.get("arguments").is_none());
    }

    #[test]
    fn auto_activity_switch_commits_only_after_matching_worker_acknowledgement() {
        let coordinator = coordinator_with_profile(RealtimeActivityProfile::Auto);
        let dialogue = dialogue();
        for sequence in 1..=2 {
            assert!(matches!(
                govern_dialogue_candidate(&auto_activity_event(sequence), &coordinator, &dialogue,),
                DialogueGovernance::Suppress
            ));
        }
        let DialogueGovernance::Rotate(pending) =
            govern_dialogue_candidate(&auto_activity_event(3), &coordinator, &dialogue)
        else {
            panic!("rotation request")
        };
        assert_eq!(pending.current.epoch, 1);
        assert_eq!(pending.next_epoch, 2);
        assert_eq!(pending.reason, ContextRotationReason::ProfileChanged);
        assert_eq!(
            coordinator
                .lock()
                .expect("coordinator")
                .as_ref()
                .expect("state")
                .active_identity()
                .epoch,
            1
        );

        let projection = commit_context_rotation_event(
            &serde_json::json!({
                "type": "context_rotated",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 2,
                "reason": "profile_changed"
            }),
            &coordinator,
            &dialogue,
            42,
        )
        .expect("acknowledged projection");
        assert_eq!(projection["context_epoch"], 2);
        assert_eq!(projection["state"], "standby");
        assert_eq!(
            coordinator
                .lock()
                .expect("coordinator")
                .as_ref()
                .expect("state")
                .active_identity()
                .epoch,
            2
        );
        assert_eq!(
            dialogue
                .lock()
                .expect("dialogue")
                .as_ref()
                .expect("director")
                .context_epoch(),
            2
        );
    }

    #[test]
    fn cloud_standby_wake_commits_only_the_host_prepared_segment() {
        let coordinator = coordinator_with_profile(RealtimeActivityProfile::Game);
        {
            let mut state = coordinator.lock().expect("coordinator");
            let state = state.as_mut().expect("state");
            assert_eq!(
                state.tick(180_000),
                vec![RealtimeCoordinatorAction::EnterCloudStandby]
            );
            let transition = state
                .prepare_wake(Some("segment-2".to_owned()))
                .expect("prepare cloud wake");
            assert!(matches!(transition, RealtimeWakeTransition::Cloud(_)));
        }
        let dialogue = dialogue();
        let projection = commit_segment_wake_event(
            &serde_json::json!({
                "type": "segment_woken",
                "session_id": "session-1",
                "segment_id": "segment-2",
                "context_epoch": 1
            }),
            &coordinator,
            &dialogue,
            180_001,
        )
        .expect("cloud wake acknowledgement");
        assert_eq!(projection["segment_id"], "segment-2");
        assert_eq!(projection["context_epoch"], 1);
        assert_eq!(
            coordinator
                .lock()
                .expect("coordinator")
                .as_ref()
                .expect("state")
                .active_segment()
                .creation_reason,
            crate::realtime_coordinator::BackendSegmentCreationReason::StandbyWake
        );
    }

    #[test]
    fn only_bounded_user_or_current_window_events_reset_native_activity() {
        assert!(is_meaningful_worker_activity(&serde_json::json!({
            "type": "public_caption",
            "speaker": "user",
            "stable": true
        })));
        assert!(is_meaningful_worker_activity(&serde_json::json!({
            "type": "perception_candidate",
            "grounding": ["current_window: material change"]
        })));
        assert!(!is_meaningful_worker_activity(&serde_json::json!({
            "type": "public_caption",
            "speaker": "assistant",
            "stable": true
        })));
        assert!(!is_meaningful_worker_activity(&serde_json::json!({
            "type": "usage",
            "audio_input_ms": 1
        })));
    }

    #[test]
    fn privacy_and_pending_transitions_fence_inflight_worker_content() {
        let coordinator = coordinator();
        let caption = serde_json::json!({
            "type": "public_caption",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "context_epoch": 1,
            "sequence": 4,
            "text": "must not escape",
            "stable": true,
            "speaker": "user"
        });
        assert!(coordinator_allows_worker_event(&caption, &coordinator));
        coordinator
            .lock()
            .expect("coordinator")
            .as_mut()
            .expect("state")
            .apply(RealtimeCoordinatorEvent::PausePrivacy)
            .expect("privacy pause");
        assert!(!coordinator_allows_worker_event(&caption, &coordinator));
    }

    #[test]
    fn raw_worker_presence_becomes_an_identity_bound_projection() {
        let coordinator = coordinator();
        let projected = govern_presence_event(
            &serde_json::json!({
                "type": "presence",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 1,
                "state": "listening",
                "level": 5
            }),
            &coordinator,
        )
        .expect("presence projection");
        assert_eq!(projected["type"], "presence_projection");
        assert_eq!(projected["state"], "listening");
        assert_eq!(projected["sequence"], 2);
        assert_eq!(projected["level"], 5);
        assert_eq!(projected["persona_digest"], "a".repeat(64));
        assert!(govern_presence_event(
            &serde_json::json!({
                "type": "presence",
                "session_id": "session-1",
                "segment_id": "segment-1",
                "context_epoch": 1,
                "state": "listening",
                "level": 5
            }),
            &coordinator,
        )
        .is_none());
    }

    #[test]
    fn host_interruption_projects_only_the_bounded_error_state() {
        let coordinator = coordinator();
        let projected = govern_host_presence(RealtimePresenceState::Error, &coordinator)
            .expect("error projection");
        assert_eq!(projected["type"], "presence_projection");
        assert_eq!(projected["state"], "error");
        assert!(projected.get("error_code").is_none());
        assert!(projected.get("provider_payload").is_none());
    }

    #[test]
    fn fairy_voice_reports_are_generation_and_identity_fenced() {
        let coordinator = coordinator();
        let dialogue = dialogue();
        let speaking = RealtimeWorkerSpeechStateInput {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            speech_generation: 1,
            speaking: true,
        };
        let projected = govern_fairy_speech_state(&speaking, &coordinator, &dialogue)
            .expect("speech report")
            .expect("projection");
        assert_eq!(projected["state"], "speaking");

        let stale = RealtimeWorkerSpeechStateInput {
            speech_generation: 0,
            ..speaking.clone()
        };
        assert!(govern_fairy_speech_state(&stale, &coordinator, &dialogue)
            .expect("stale report")
            .is_none());

        let wrong_segment = RealtimeWorkerSpeechStateInput {
            segment_id: "stale-segment".to_owned(),
            ..speaking
        };
        assert!(matches!(
            govern_fairy_speech_state(&wrong_segment, &coordinator, &dialogue),
            Err(RealtimeWorkerError::Protocol)
        ));
    }
}
