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
    RealtimeContextCarryover, RealtimeInteractionIntensity, RealtimeVoiceOutput, SecretString,
    WorkerEvent,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::omni_model_manifest::OmniModelManifest;
use crate::realtime_assistance::{RealtimeAssistancePublicState, RealtimeAssistanceRouter};
use crate::realtime_context::RealtimeContextAuthority;
use crate::realtime_coordinator::{
    ContextEpochIdentity, ContextRotationReason, PendingContextRotation, RealtimeCoordinatorAction,
    RealtimeCoordinatorEvent, RealtimeCoordinatorStart, RealtimeCoordinatorState,
    RealtimePresenceProjection, RealtimePresenceState, RealtimeWakeTransition,
};
use crate::realtime_dialogue::{RealtimeDialogueDecision, RealtimeDialogueDirector};

pub const REALTIME_WORKER_EVENT: &str = "fairy-realtime-event";
const REALTIME_PROTOCOL: &str = "fairy-realtime-worker-v2";
const WORKER_STOP_GRACE: Duration = Duration::from_millis(80);

#[derive(Clone, Debug)]
pub struct RealtimeWorkerLaunch {
    pub program: PathBuf,
    pub log_path: PathBuf,
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
    #[serde(flatten)]
    pub usage: RealtimeWorkerUsage,
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
    child: Child,
    input: Arc<Mutex<ChildStdin>>,
    session_id: Option<String>,
    expected_shutdown: Arc<AtomicBool>,
    terminal: Arc<AtomicBool>,
}

struct WorkerGovernanceHandles {
    assistance: Arc<RealtimeAssistanceRouter>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
    context: Arc<Mutex<RealtimeContextAuthority>>,
}

enum DialogueGovernance {
    Projection(Value),
    Rotate(PendingContextRotation),
    Suppress,
}

pub struct RealtimeWorkerManager {
    launch: RealtimeWorkerLaunch,
    assistance: Arc<RealtimeAssistanceRouter>,
    process: Mutex<Option<WorkerProcess>>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
    context: Arc<Mutex<RealtimeContextAuthority>>,
}

impl RealtimeWorkerManager {
    pub fn new(launch: RealtimeWorkerLaunch, assistance: Arc<RealtimeAssistanceRouter>) -> Self {
        Self {
            launch,
            assistance,
            process: Mutex::new(None),
            usage: Arc::new(Mutex::new(RealtimeWorkerUsage::default())),
            active_identity: Arc::new(Mutex::new(None)),
            coordinator: Arc::new(Mutex::new(None)),
            dialogue: Arc::new(Mutex::new(None)),
            context: Arc::new(Mutex::new(RealtimeContextAuthority::default())),
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

    pub fn start(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        presence_max_minutes: u16,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(
            app,
            input,
            credential,
            persona_snapshot,
            presence_max_minutes,
            false,
        )
    }

    pub fn continue_session(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        presence_max_minutes: u16,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(
            app,
            input,
            credential,
            persona_snapshot,
            presence_max_minutes,
            true,
        )
    }

    fn start_segment(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
        presence_max_minutes: u16,
        continuation_approved: bool,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
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
        let mut guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        if let Some(process) = guard.as_mut() {
            if !reap_finished_process(process) {
                return Err(RealtimeWorkerError::Busy);
            }
            *guard = None;
        }
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
            })
            .map_err(|_| RealtimeWorkerError::Protocol)?
        };
        let identity = coordinator.active_identity().clone();
        let director = RealtimeDialogueDirector::new(
            identity.session_id.clone(),
            identity.segment_id.clone(),
            identity.epoch,
            coordinator.persona_digest().to_owned(),
            input.voice_output,
        )
        .ok_or(RealtimeWorkerError::Protocol)?;
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
        self.context
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?
            .attach_session(&input.session_id, &persona)
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        if let Ok(mut dialogue) = self.dialogue.lock() {
            *dialogue = Some(director);
        } else {
            self.restore_context(previous_context);
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = Some(identity.clone());
        } else {
            self.restore_dialogue(previous_dialogue);
            self.restore_context(previous_context);
            return Err(RealtimeWorkerError::Protocol);
        }
        let mut process = match spawn_worker(
            &self.launch,
            app.clone(),
            WorkerGovernanceHandles {
                assistance: Arc::clone(&self.assistance),
                usage: Arc::clone(&self.usage),
                active_identity: Arc::clone(&self.active_identity),
                coordinator: Arc::clone(&self.coordinator),
                dialogue: Arc::clone(&self.dialogue),
                context: Arc::clone(&self.context),
            },
        ) {
            Ok(process) => process,
            Err(error) => {
                self.restore_governance(previous_governance);
                self.restore_dialogue(previous_dialogue);
                self.restore_context(previous_context);
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
            source_id: input.source_id,
            microphone_enabled: input.microphone_enabled,
            screen_enabled: input.screen_enabled,
            application_audio_enabled: input.application_audio_enabled,
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
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Some(projection) = self.current_presence_projection() {
            let _ = app.emit(
                REALTIME_WORKER_EVENT,
                presence_projection_payload(projection),
            );
        }
        process.session_id = Some(input.session_id);
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
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let process = guard.as_ref().ok_or(RealtimeWorkerError::Unavailable)?;
        if process.session_id.as_deref() != Some(input.session_id.as_str()) {
            return Err(RealtimeWorkerError::Protocol);
        }
        let transition = {
            let mut coordinator = self
                .coordinator
                .lock()
                .map_err(|_| RealtimeWorkerError::Protocol)?;
            let coordinator = coordinator
                .as_mut()
                .ok_or(RealtimeWorkerError::Unavailable)?;
            let next_segment = (coordinator.backend() == RealtimeBackendKind::CloudLive)
                .then(|| Uuid::new_v4().to_string());
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
            if send_command(&process.input, &command).is_err() {
                *active = previous.clone();
                previous
            } else {
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
        let pending = {
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
            coordinator
                .pending_context_rotation()
                .cloned()
                .ok_or(RealtimeWorkerError::Protocol)?
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
        source_id: input.source_id,
        microphone_enabled: input.microphone_enabled,
        screen_enabled: input.screen_enabled,
        application_audio_enabled: input.application_audio_enabled,
        online_assistance_enabled: input.online_assistance_enabled,
    })
    .map_err(|_| RealtimeWorkerError::Protocol)
}

impl Drop for RealtimeWorkerManager {
    fn drop(&mut self) {
        self.assistance.shutdown();
        if let Ok(mut guard) = self.process.lock() {
            if let Some(process) = guard.as_mut() {
                process.expected_shutdown.store(true, Ordering::Release);
                let _ = process.child.kill();
                let _ = process.child.wait();
            }
            *guard = None;
        }
        self.finish_governance();
    }
}

fn spawn_worker(
    launch: &RealtimeWorkerLaunch,
    app: AppHandle,
    governance: WorkerGovernanceHandles,
) -> Result<WorkerProcess, RealtimeWorkerError> {
    let WorkerGovernanceHandles {
        assistance,
        usage,
        active_identity,
        coordinator,
        dialogue,
        context,
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
    let tick_input = Arc::clone(&input);
    let tick_coordinator = Arc::clone(&coordinator);
    let tick_expected_shutdown = Arc::clone(&expected_shutdown);
    let tick_terminal = Arc::clone(&terminal);
    let tick_app = app.clone();
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
                        if let Some(projection) = commit_cloud_wake_event(
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
    match ready_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(true) => Ok(WorkerProcess {
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
                    let _ = app.emit(
                        REALTIME_WORKER_EVENT,
                        serde_json::json!({
                            "type": "resource_pressure",
                            "session_id": identity.session_id,
                            "segment_id": identity.segment_id,
                            "context_epoch": identity.epoch,
                            "code": "LOCAL_UNLOAD_ELIGIBLE"
                        }),
                    );
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

fn commit_cloud_wake_event(
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
        let pending = coordinator.pending_cloud_wake()?;
        if pending.current.session_id != session_id || pending.next_segment_id != segment_id {
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
        coordinator.commit_cloud_wake(&segment_id, now_ms).ok()?;
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
        Some("ready" | "pong" | "worker_interrupted" | "context_rotated" | "segment_woken")
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
        Some("ready" | "pong" | "worker_interrupted" | "context_rotated" | "segment_woken")
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
        };
        assert!(validate_capture_scope(&input).is_err());

        input.source_id = Some(42);
        input.screen_enabled = false;
        input.application_audio_enabled = true;
        assert!(validate_capture_scope(&input).is_err());

        input.screen_enabled = true;
        assert!(validate_capture_scope(&input).is_ok());
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
        let projection = commit_cloud_wake_event(
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
