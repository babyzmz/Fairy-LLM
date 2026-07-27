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
    RealtimeInteractionIntensity, RealtimeVoiceOutput, SecretString, WorkerEvent,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::omni_model_manifest::OmniModelManifest;
use crate::realtime_coordinator::{
    ContextEpochIdentity, RealtimeCoordinatorEvent, RealtimeCoordinatorStart,
    RealtimeCoordinatorState, RealtimePresenceProjection, RealtimePresenceState,
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

pub struct RealtimeWorkerManager {
    launch: RealtimeWorkerLaunch,
    process: Mutex<Option<WorkerProcess>>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
}

impl RealtimeWorkerManager {
    pub fn new(launch: RealtimeWorkerLaunch) -> Self {
        Self {
            launch,
            process: Mutex::new(None),
            usage: Arc::new(Mutex::new(RealtimeWorkerUsage::default())),
            active_identity: Arc::new(Mutex::new(None)),
            coordinator: Arc::new(Mutex::new(None)),
            dialogue: Arc::new(Mutex::new(None)),
        }
    }

    pub fn status(&self) -> RealtimeWorkerStatus {
        let mut guard = self.process.lock().expect("realtime worker lock poisoned");
        if guard.as_mut().is_some_and(reap_finished_process) {
            *guard = None;
            self.cleanup_finished_governance();
        }
        let projection = self.governance_projection();
        RealtimeWorkerStatus {
            running: guard.is_some(),
            session_id: guard
                .as_ref()
                .and_then(|process| process.session_id.clone()),
            segment_id: projection.as_ref().map(|value| value.0.clone()),
            context_epoch: projection.as_ref().map(|value| value.1),
            backend: projection.as_ref().map(|value| value.2),
            cloud_provider: projection.as_ref().and_then(|value| value.3),
            action_required: projection.is_some_and(|value| value.4),
            usage: self.usage_snapshot(),
        }
    }

    pub fn start(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(app, input, credential, persona_snapshot, false)
    }

    pub fn continue_session(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        self.start_segment(app, input, credential, persona_snapshot, true)
    }

    fn start_segment(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Option<Zeroizing<String>>,
        persona_snapshot: Zeroizing<String>,
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
                presence_max_minutes: 0,
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
        if let Ok(mut dialogue) = self.dialogue.lock() {
            *dialogue = Some(director);
        } else {
            return Err(RealtimeWorkerError::Protocol);
        }
        if let Ok(mut active) = self.active_identity.lock() {
            *active = Some(identity.clone());
        } else {
            self.restore_dialogue(previous_dialogue);
            return Err(RealtimeWorkerError::Protocol);
        }
        let mut process = match spawn_worker(
            &self.launch,
            app.clone(),
            Arc::clone(&self.usage),
            Arc::clone(&self.active_identity),
            Arc::clone(&self.coordinator),
            Arc::clone(&self.dialogue),
        ) {
            Ok(process) => process,
            Err(error) => {
                self.restore_governance(previous_governance);
                self.restore_dialogue(previous_dialogue);
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
        if send_command(&process.input, &command).is_err() {
            let _ = process.child.kill();
            let _ = process.child.wait();
            self.restore_governance(previous_governance);
            self.restore_dialogue(previous_dialogue);
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
                    usage: self.usage_snapshot(),
                });
            }
            thread::sleep(Duration::from_millis(4));
        }
        let _ = process.child.kill();
        let _ = process.child.wait();
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

    fn finish_governance(&self) {
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
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
    active_identity: Arc<Mutex<Option<ContextEpochIdentity>>>,
    coordinator: Arc<Mutex<Option<RealtimeCoordinatorState>>>,
    dialogue: Arc<Mutex<Option<RealtimeDialogueDirector>>>,
) -> Result<WorkerProcess, RealtimeWorkerError> {
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
    let output = child.stdout.take().ok_or(RealtimeWorkerError::Protocol)?;
    let mut reader = BufReader::new(output);
    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let expected_shutdown = Arc::new(AtomicBool::new(false));
    let reader_expected_shutdown = Arc::clone(&expected_shutdown);
    let terminal = Arc::new(AtomicBool::new(false));
    let reader_terminal = Arc::clone(&terminal);
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
                    let value = govern_speech_state(value, &dialogue);
                    if let Some(projection) = govern_presence_event(&value, &coordinator) {
                        let _ = app.emit(REALTIME_WORKER_EVENT, projection);
                    }
                    if value.get("type").and_then(Value::as_str) == Some("perception_candidate") {
                        if let Some(projected) =
                            govern_dialogue_candidate(&value, &coordinator, &dialogue)
                        {
                            let _ = app.emit(REALTIME_WORKER_EVENT, projected);
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
                        if let Ok(mut state) = coordinator.lock() {
                            if let Some(state) = state.as_mut() {
                                let _ = state.apply(RealtimeCoordinatorEvent::BackendFailed);
                            }
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

fn govern_dialogue_candidate(
    value: &Value,
    coordinator: &Mutex<Option<RealtimeCoordinatorState>>,
    dialogue: &Mutex<Option<RealtimeDialogueDirector>>,
) -> Option<Value> {
    let event = serde_json::from_value::<WorkerEvent>(value.clone()).ok()?;
    let WorkerEvent::PerceptionCandidate {
        session_id,
        segment_id,
        context_epoch,
        sequence,
        candidate,
    } = event
    else {
        return None;
    };
    let (effective_activity, interaction_intensity, switched) = {
        let mut coordinator = coordinator.lock().ok()?;
        let coordinator = coordinator.as_mut()?;
        if candidate.persona_digest != coordinator.persona_digest() || !candidate.is_valid() {
            return None;
        }
        let observation = coordinator.observe_activity_candidate(sequence, &candidate)?;
        (
            observation.effective_activity,
            coordinator.interaction_intensity(),
            observation.switched,
        )
    };
    let mut director = dialogue.lock().ok()?;
    let director = director.as_mut()?;
    if switched {
        director.reset_epoch_policy();
        return None;
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
        RealtimeDialogueDecision::Speak(projection) => Some(serde_json::json!({
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
        })),
        RealtimeDialogueDecision::RequestAssistance(candidate) => Some(serde_json::json!({
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
        })),
        RealtimeDialogueDecision::Listen | RealtimeDialogueDecision::Suppress(_) => None,
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
    if matches!(event_type, Some("ready" | "pong" | "worker_interrupted")) {
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

    fn coordinator() -> Mutex<Option<RealtimeCoordinatorState>> {
        Mutex::new(Some(
            RealtimeCoordinatorState::start(RealtimeCoordinatorStart {
                session_id: "session-1".to_owned(),
                segment_id: "segment-1".to_owned(),
                persona_digest: "a".repeat(64),
                backend: RealtimeBackendKind::CloudLive,
                cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
                activity_profile: RealtimeActivityProfile::Game,
                interaction_intensity: RealtimeInteractionIntensity::Standard,
                presence_max_minutes: 240,
            })
            .expect("coordinator"),
        ))
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

    #[test]
    fn grounded_candidate_becomes_an_approved_assistant_caption() {
        let projected = govern_dialogue_candidate(
            &candidate_event(serde_json::json!(["current_user_utterance"])),
            &coordinator(),
            &dialogue(),
        )
        .expect("approved projection");
        assert_eq!(projected["type"], "public_caption");
        assert_eq!(projected["speaker"], "assistant");
        assert_eq!(projected["speech_output"], "fairy_voice");
        assert_eq!(projected["speech_generation"], 1);
        assert_eq!(projected["persona_digest"], "a".repeat(64));
    }

    #[test]
    fn ungrounded_candidate_is_suppressed_without_reemitting_its_body() {
        let event = candidate_event(serde_json::json!([]));
        assert!(govern_dialogue_candidate(&event, &coordinator(), &dialogue()).is_none());
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
        let projected = govern_dialogue_candidate(&event, &coordinator(), &dialogue())
            .expect("assistance projection");
        assert_eq!(projected["type"], "assistance_request");
        assert_eq!(projected["needs_online_assistance"], true);
        assert!(projected.get("tool_name").is_none());
        assert!(projected.get("arguments").is_none());
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
