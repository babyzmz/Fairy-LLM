use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::ipc::{Channel, Response};
use tauri::{AppHandle, Emitter};
use thiserror::Error;

const HANDSHAKE_PROTOCOL: &str = "fairy-voice-worker-v1";
pub const VOICE_WORKER_LIFECYCLE_EVENT: &str = "voice-worker-lifecycle";
const MAX_HEADER_BYTES: usize = 32 * 1024;
const STREAM_CHUNK_BYTES: usize = 16 * 1024;
const MAX_QUEUED_PLAYBACKS: usize = 8;
const MAX_QUEUED_CHARACTERS: usize = 8_000;
const VOICE_MODEL_REPOSITORY: &str = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512";
const VOICE_MODEL_DIRECTORY: &str = "Fun-CosyVoice3-0.5B-2512";
const VOICE_MODEL_FILES: &[&str] = &[
    "campplus.onnx",
    "cosyvoice3.yaml",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
    "flow.decoder.estimator.fp32.onnx",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "speech_tokenizer_v3.onnx",
];
type WorkerResponse = (u16, HashMap<String, String>, BufReader<TcpStream>);

#[derive(Clone, Debug)]
pub struct VoiceWorkerLaunch {
    pub program: PathBuf,
    pub arguments: Vec<String>,
    pub worker_python_path: PathBuf,
    pub cosyvoice_root: PathBuf,
    pub assets_dir: PathBuf,
    pub data_dir: PathBuf,
    pub log_path: PathBuf,
}

#[derive(Debug, Error)]
pub enum VoiceWorkerError {
    #[error("voice worker is unavailable: {0}")]
    Unavailable(String),
    #[error("voice worker protocol failed: {0}")]
    Protocol(String),
    #[error("voice worker reported: {0}")]
    Worker(String),
    #[error("voice worker request failed: HTTP {0}")]
    Http(u16),
    #[error("voice worker I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("voice worker JSON failed: {0}")]
    Json(#[from] serde_json::Error),
}

impl VoiceWorkerError {
    pub fn public_code(&self) -> &str {
        match self {
            Self::Unavailable(_) => "VOICE_WORKER_UNAVAILABLE",
            Self::Protocol(_) => "VOICE_PROTOCOL_ERROR",
            Self::Worker(error_code) => error_code,
            Self::Http(_) => "VOICE_WORKER_HTTP_ERROR",
            Self::Io(_) => "VOICE_WORKER_IO_ERROR",
            Self::Json(_) => "VOICE_WORKER_PROTOCOL_ERROR",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PreparedVoiceSession {
    pub id: String,
    pub task_id: String,
    pub conversation_id: String,
    pub turn_id: String,
    pub message_id: Option<String>,
    pub start_offset: usize,
    pub end_offset: usize,
    pub validated_text: String,
    pub scope_digest: String,
    pub source_cursor: u64,
    pub status: String,
    pub created_at: String,
    pub cancelled_at: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct VoiceStreamInput {
    pub task_id: String,
    pub turn_id: String,
    pub message_id: Option<String>,
    pub start_offset: usize,
    pub end_offset: usize,
    pub idempotency_key: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum VoiceStreamEvent {
    Started {
        session_id: String,
        sample_rate: u32,
        channels: u16,
        scope_digest: String,
    },
    Progress {
        session_id: String,
        pcm_bytes: u64,
    },
    Completed {
        session_id: String,
        pcm_bytes: u64,
    },
    Cancelled {
        session_id: String,
    },
    Failed {
        session_id: String,
        error_code: String,
        message: String,
    },
}

struct WorkerProcess {
    child: Child,
    address: SocketAddr,
    bootstrap_token: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct VoiceLifecycleSnapshot {
    pub sequence: u64,
    pub lifecycle_state: String,
    pub active_consumer_count: usize,
    pub queued_playback_count: usize,
    pub error_code: Option<String>,
    pub started_at_unix_ms: Option<u64>,
    pub transitioned_at_unix_ms: u64,
}

struct VoiceLifecycleState {
    snapshot: VoiceLifecycleSnapshot,
    queued_characters: usize,
    idle_since: Option<Instant>,
}

pub struct VoiceWorkerManager {
    pub audio_focus: Arc<crate::audio_focus::AudioFocus>,
    launch: VoiceWorkerLaunch,
    app: Option<AppHandle>,
    process: Mutex<Option<WorkerProcess>>,
    prepare_lock: Mutex<()>,
    operation_generation: AtomicU64,
    closed: AtomicBool,
    idle_unloads: AtomicU64,
    lifecycle: Mutex<VoiceLifecycleState>,
    cancellations: Mutex<HashSet<String>>,
}

impl VoiceWorkerManager {
    pub fn new(launch: VoiceWorkerLaunch) -> Self {
        Self::with_app(launch, None)
    }

    pub fn new_with_app(launch: VoiceWorkerLaunch, app: AppHandle) -> Self {
        Self::with_app(launch, Some(app))
    }

    fn with_app(launch: VoiceWorkerLaunch, app: Option<AppHandle>) -> Self {
        let now = unix_time_ms();
        Self {
            audio_focus: Arc::new(
                app.clone()
                    .map(crate::audio_focus::AudioFocus::with_app)
                    .unwrap_or_default(),
            ),
            launch,
            app,
            process: Mutex::new(None),
            prepare_lock: Mutex::new(()),
            operation_generation: AtomicU64::new(0),
            closed: AtomicBool::new(false),
            idle_unloads: AtomicU64::new(0),
            lifecycle: Mutex::new(VoiceLifecycleState {
                snapshot: VoiceLifecycleSnapshot {
                    sequence: 0,
                    lifecycle_state: "stopped".to_owned(),
                    active_consumer_count: 0,
                    queued_playback_count: 0,
                    error_code: None,
                    started_at_unix_ms: None,
                    transitioned_at_unix_ms: now,
                },
                queued_characters: 0,
                idle_since: None,
            }),
            cancellations: Mutex::new(HashSet::new()),
        }
    }

    pub fn status(&self) -> Result<Value, VoiceWorkerError> {
        let health = match self.running_connection()? {
            Some(connection) => request_health(&connection),
            None => Ok(cold_voice_health(&self.launch)),
        }?;
        Ok(self.with_lifecycle(health))
    }

    pub fn health(&self) -> Result<Value, VoiceWorkerError> {
        let connection = self.connection()?;
        Ok(self.with_lifecycle(request_health(&connection)?))
    }

    pub fn prepare(&self) -> Result<Value, VoiceWorkerError> {
        let requested_generation = self.operation_generation.load(Ordering::Acquire);
        let result = self.prepare_for_generation(requested_generation);
        match &result {
            Ok(health)
                if self.operation_generation.load(Ordering::Acquire) == requested_generation
                    && health.get("status").and_then(Value::as_str) == Some("ready") =>
            {
                self.transition_if_current(requested_generation, "ready", None);
            }
            Err(error)
                if self.operation_generation.load(Ordering::Acquire) == requested_generation =>
            {
                self.transition_if_current(
                    requested_generation,
                    "failed",
                    Some(error.public_code()),
                );
            }
            _ => {}
        }
        result.map(|health| self.with_lifecycle(health))
    }

    fn prepare_for_generation(&self, requested_generation: u64) -> Result<Value, VoiceWorkerError> {
        let _prepare_guard = self
            .prepare_lock
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("prepare lock is poisoned".to_owned()))?;
        if self.operation_generation.load(Ordering::Acquire) != requested_generation {
            return Ok(cold_voice_health(&self.launch));
        }
        let connection = match self.running_connection()? {
            Some(connection) => connection,
            None => {
                self.transition_if_current(requested_generation, "starting_worker", None);
                self.connection_for_generation(requested_generation)?
            }
        };
        if self.lifecycle_snapshot().lifecycle_state == "ready" {
            let current = request_health(&connection)?;
            if current.get("status").and_then(Value::as_str) == Some("ready") {
                return Ok(current);
            }
        }
        self.transition_if_current(requested_generation, "checking_runtime", None);
        let current = request_health(&connection)?;
        if current.get("status").and_then(Value::as_str) == Some("ready") {
            return Ok(current);
        }
        self.transition_if_current(requested_generation, "warming", None);
        let _reservation = self
            .audio_focus
            .model_resources
            .reserve("cosyvoice", None)
            .map_err(|code| VoiceWorkerError::Worker(code.to_owned()))?;
        let response = send_request(
            connection.address,
            "POST",
            "/v1/runtime/prepare",
            &connection.bootstrap_token,
            b"",
            Duration::from_secs(10 * 60),
        );
        let (status, _, mut reader) = match response {
            Ok(response) => response,
            Err(_) if self.operation_generation.load(Ordering::Acquire) != requested_generation => {
                return Ok(cold_voice_health(&self.launch));
            }
            Err(error) => return Err(error),
        };
        let mut body = Vec::new();
        reader.read_to_end(&mut body)?;
        if self.operation_generation.load(Ordering::Acquire) != requested_generation {
            return Ok(cold_voice_health(&self.launch));
        }
        if status != 200 {
            return Err(worker_response_error(status, &body));
        }
        let health: Value = serde_json::from_slice(&body)?;
        if health.get("status").and_then(Value::as_str) != Some("ready") {
            return Err(VoiceWorkerError::Worker(
                health
                    .get("error_code")
                    .and_then(Value::as_str)
                    .filter(|code| is_stable_worker_code(code))
                    .unwrap_or("VOICE_WORKER_NOT_READY")
                    .to_owned(),
            ));
        }
        Ok(health)
    }

    pub fn stop(&self) -> Result<Value, VoiceWorkerError> {
        {
            let mut lifecycle = self.lifecycle.lock().map_err(|_| {
                VoiceWorkerError::Unavailable("lifecycle lock is poisoned".to_owned())
            })?;
            self.operation_generation.fetch_add(1, Ordering::AcqRel);
            lifecycle.snapshot.lifecycle_state = "stopping".to_owned();
            lifecycle.idle_since = None;
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
        }
        self.emit_lifecycle(&self.lifecycle_snapshot());
        let mut guard = self
            .process
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("worker lock is poisoned".to_owned()))?;
        if let Some(mut process) = guard.take() {
            let _ = process.child.kill();
            process.child.wait()?;
        }
        if let Ok(mut cancellations) = self.cancellations.lock() {
            cancellations.clear();
        }
        self.reset_playback_counts();
        self.transition("stopped", None);
        Ok(self.with_lifecycle(cold_voice_health(&self.launch)))
    }

    pub fn shutdown(&self) {
        self.closed.store(true, Ordering::Release);
        if let Err(error) = self.stop() {
            eprintln!("voice shutdown: {}", error.public_code());
        }
    }

    pub fn install_model(&self) -> Result<Value, VoiceWorkerError> {
        let _prepare_guard = self
            .prepare_lock
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("prepare lock is poisoned".to_owned()))?;
        let connection = self.connection()?;
        let (status, _, mut reader) = send_request(
            connection.address,
            "POST",
            "/v1/model/install",
            &connection.bootstrap_token,
            b"",
            Duration::from_secs(60 * 60),
        )?;
        let mut body = Vec::new();
        reader.read_to_end(&mut body)?;
        if status != 200 {
            return Err(worker_response_error(status, &body));
        }
        Ok(serde_json::from_slice(&body)?)
    }

    pub fn stream(
        &self,
        session: PreparedVoiceSession,
        audio: Channel<Response>,
        events: Channel<VoiceStreamEvent>,
    ) -> Result<(), VoiceWorkerError> {
        let realtime_audio = session.task_id == "realtime";
        let focus_generation = self
            .audio_focus
            .admit(realtime_audio)
            .map_err(|code| VoiceWorkerError::Worker(code.to_owned()))?;
        if session.status != "prepared" {
            return Err(VoiceWorkerError::Protocol(
                "voice session is not prepared".to_owned(),
            ));
        }
        // An explicit cancellation arriving before the stream must not be erased.
        if self.take_cancellation(&session.id) {
            let _ = events.send(VoiceStreamEvent::Cancelled {
                session_id: session.id,
            });
            return Ok(());
        }
        let mut lease = self.queue_playback(session.validated_text.chars().count())?;
        let health = self.prepare_for_generation(lease.generation)?;
        if health.get("status").and_then(Value::as_str) != Some("ready") {
            return Err(VoiceWorkerError::Worker("VOICE_WORKER_STOPPED".to_owned()));
        }
        self.transition_if_current(lease.generation, "ready", None);
        if self.take_cancellation(&session.id)
            || !self
                .audio_focus
                .is_current(focus_generation, realtime_audio)
            || self.operation_generation.load(Ordering::Acquire) != lease.generation
        {
            let _ = events.send(VoiceStreamEvent::Cancelled {
                session_id: session.id,
            });
            return Ok(());
        }
        let connection = self.connection_for_generation(lease.generation)?;
        let token = secure_token()?;
        register_token(&connection, &token)?;
        let priority = if session.message_id.is_none()
            && session.task_id != "realtime"
            && session.task_id != "settings"
        {
            "automatic"
        } else {
            "manual"
        };
        let body = serde_json::to_vec(&json!({
            "session_id": session.id,
            "scope_digest": session.scope_digest,
            "text": session.validated_text,
            "priority": priority,
        }))?;
        let (status, headers, mut reader) = send_request(
            connection.address,
            "POST",
            "/v1/sessions",
            &token,
            &body,
            Duration::from_secs(10 * 60),
        )?;
        if status != 200 {
            let mut body = Vec::new();
            reader.read_to_end(&mut body)?;
            let error = worker_response_error(status, &body);
            if error.public_code() == "VOICE_PLAYBACK_CANCELLED" {
                let _ = events.send(VoiceStreamEvent::Cancelled {
                    session_id: session.id,
                });
                return Ok(());
            }
            return Err(error);
        }
        let returned_session = required_header(&headers, "x-fairy-session-id")?;
        let returned_scope = required_header(&headers, "x-fairy-scope-digest")?;
        if returned_session != session.id || returned_scope != session.scope_digest {
            return Err(VoiceWorkerError::Protocol(
                "voice worker returned a mismatched scope".to_owned(),
            ));
        }
        let sample_rate = required_header(&headers, "x-fairy-sample-rate")?
            .parse::<u32>()
            .map_err(|_| VoiceWorkerError::Protocol("invalid voice sample rate".to_owned()))?;
        let channels = required_header(&headers, "x-fairy-channels")?
            .parse::<u16>()
            .map_err(|_| VoiceWorkerError::Protocol("invalid voice channel count".to_owned()))?;
        if !(8_000..=48_000).contains(&sample_rate) || channels != 1 {
            return Err(VoiceWorkerError::Protocol(
                "voice PCM format is unsupported".to_owned(),
            ));
        }
        lease.activate();
        let _ = events.send(VoiceStreamEvent::Started {
            session_id: session.id.clone(),
            sample_rate,
            channels,
            scope_digest: session.scope_digest.clone(),
        });
        let mut total = 0_u64;
        let mut trailing_byte = None;
        let mut buffer = vec![0_u8; STREAM_CHUNK_BYTES];
        loop {
            if !self
                .audio_focus
                .is_current(focus_generation, realtime_audio)
                || self.operation_generation.load(Ordering::Acquire) != lease.generation
            {
                let _ = self.cancel(&session.id);
                let _ = events.send(VoiceStreamEvent::Cancelled {
                    session_id: session.id,
                });
                return Ok(());
            }
            let read = reader.read(&mut buffer)?;
            if !self
                .audio_focus
                .is_current(focus_generation, realtime_audio)
                || self.operation_generation.load(Ordering::Acquire) != lease.generation
            {
                let _ = self.cancel(&session.id);
                let _ = events.send(VoiceStreamEvent::Cancelled {
                    session_id: session.id,
                });
                return Ok(());
            }
            if read == 0 {
                break;
            }
            let mut framed = Vec::with_capacity(read + usize::from(trailing_byte.is_some()));
            if let Some(byte) = trailing_byte.take() {
                framed.push(byte);
            }
            framed.extend_from_slice(&buffer[..read]);
            if framed.len() % 2 != 0 {
                trailing_byte = framed.pop();
            }
            if framed.is_empty() {
                continue;
            }
            total += framed.len() as u64;
            audio
                .send(Response::new(framed))
                .map_err(|error| VoiceWorkerError::Protocol(error.to_string()))?;
            let _ = events.send(VoiceStreamEvent::Progress {
                session_id: session.id.clone(),
                pcm_bytes: total,
            });
        }
        if trailing_byte.is_some() {
            return Err(VoiceWorkerError::Protocol(
                "voice worker returned an incomplete PCM16 frame".to_owned(),
            ));
        }
        let cancelled = self
            .cancellations
            .lock()
            .map(|mut cancellations| cancellations.remove(&session.id))
            .unwrap_or(false);
        if cancelled {
            let _ = events.send(VoiceStreamEvent::Cancelled {
                session_id: session.id,
            });
        } else {
            let _ = events.send(VoiceStreamEvent::Completed {
                session_id: session.id,
                pcm_bytes: total,
            });
        }
        Ok(())
    }

    fn queue_playback(
        &self,
        characters: usize,
    ) -> Result<VoicePlaybackLease<'_>, VoiceWorkerError> {
        if characters == 0 || characters > 2_000 {
            return Err(VoiceWorkerError::Worker("VOICE_REQUEST_INVALID".to_owned()));
        }
        let (snapshot, generation) = {
            let mut lifecycle = self.lifecycle.lock().map_err(|_| {
                VoiceWorkerError::Unavailable("lifecycle lock is poisoned".to_owned())
            })?;
            if self.closed.load(Ordering::Acquire)
                || lifecycle.snapshot.lifecycle_state == "stopping"
            {
                return Err(VoiceWorkerError::Worker("VOICE_WORKER_STOPPED".to_owned()));
            }
            if lifecycle.snapshot.queued_playback_count >= MAX_QUEUED_PLAYBACKS
                || lifecycle.queued_characters.saturating_add(characters) > MAX_QUEUED_CHARACTERS
            {
                return Err(VoiceWorkerError::Worker(
                    "VOICE_PLAYBACK_QUEUE_FULL".to_owned(),
                ));
            }
            lifecycle.snapshot.queued_playback_count += 1;
            lifecycle.queued_characters += characters;
            lifecycle.idle_since = None;
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
            (
                lifecycle.snapshot.clone(),
                self.operation_generation.load(Ordering::Acquire),
            )
        };
        self.emit_lifecycle(&snapshot);
        Ok(VoicePlaybackLease {
            manager: self,
            characters,
            active: false,
            released: false,
            generation,
        })
    }

    pub fn cancel(&self, session_id: &str) -> Result<(), VoiceWorkerError> {
        self.cancellations
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("cancellation lock is poisoned".to_owned()))?
            .insert(session_id.to_owned());
        let Some(connection) = self.running_connection()? else {
            return Ok(());
        };
        let token = secure_token()?;
        register_token(&connection, &token)?;
        let path = format!("/v1/sessions/{session_id}");
        let (status, _, mut reader) = send_request(
            connection.address,
            "DELETE",
            &path,
            &token,
            b"",
            Duration::from_secs(2),
        )?;
        let mut body = Vec::new();
        reader.read_to_end(&mut body)?;
        if status != 200 {
            return Err(worker_response_error(status, &body));
        }
        let _: Value = serde_json::from_slice(&body)?;
        Ok(())
    }

    fn take_cancellation(&self, session_id: &str) -> bool {
        self.cancellations
            .lock()
            .map(|mut cancellations| cancellations.remove(session_id))
            .unwrap_or(false)
    }

    fn connection(&self) -> Result<WorkerConnection, VoiceWorkerError> {
        self.connection_for_generation(self.operation_generation.load(Ordering::Acquire))
    }

    fn connection_for_generation(
        &self,
        generation: u64,
    ) -> Result<WorkerConnection, VoiceWorkerError> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("worker lock is poisoned".to_owned()))?;
        if self.closed.load(Ordering::Acquire)
            || self.operation_generation.load(Ordering::Acquire) != generation
            || self.lifecycle_snapshot().lifecycle_state == "stopping"
        {
            return Err(VoiceWorkerError::Worker(
                "VOICE_PLAYBACK_CANCELLED".to_owned(),
            ));
        }
        if let Some(process) = guard.as_mut() {
            if process.child.try_wait()?.is_none() {
                return Ok(WorkerConnection {
                    address: process.address,
                    bootstrap_token: process.bootstrap_token.clone(),
                });
            }
        }
        *guard = None;
        let process = spawn_worker(&self.launch)?;
        let connection = WorkerConnection {
            address: process.address,
            bootstrap_token: process.bootstrap_token.clone(),
        };
        *guard = Some(process);
        Ok(connection)
    }

    fn running_connection(&self) -> Result<Option<WorkerConnection>, VoiceWorkerError> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("worker lock is poisoned".to_owned()))?;
        let Some(process) = guard.as_mut() else {
            return Ok(None);
        };
        if process.child.try_wait()?.is_some() {
            *guard = None;
            drop(guard);
            self.transition("failed", Some("VOICE_WORKER_INTERRUPTED"));
            return Ok(None);
        }
        Ok(Some(WorkerConnection {
            address: process.address,
            bootstrap_token: process.bootstrap_token.clone(),
        }))
    }

    fn with_lifecycle(&self, mut health: Value) -> Value {
        let snapshot = self.lifecycle_snapshot();
        if let Some(object) = health.as_object_mut() {
            object.insert("sequence".to_owned(), json!(snapshot.sequence));
            object.insert(
                "model_reservation".to_owned(),
                json!(self.audio_focus.model_resources.snapshot()),
            );
            object.insert("idle_unload_seconds".to_owned(), json!(300));
            object.insert(
                "idle_unloads".to_owned(),
                json!(self.idle_unloads.load(Ordering::Relaxed)),
            );
            object.insert(
                "lifecycle_state".to_owned(),
                json!(snapshot.lifecycle_state),
            );
            object.insert(
                "active_consumer_count".to_owned(),
                json!(snapshot.active_consumer_count),
            );
            object.insert(
                "queued_playback_count".to_owned(),
                json!(snapshot.queued_playback_count),
            );
            object.insert(
                "started_at_unix_ms".to_owned(),
                json!(snapshot.started_at_unix_ms),
            );
            object.insert(
                "transitioned_at_unix_ms".to_owned(),
                json!(snapshot.transitioned_at_unix_ms),
            );
            if snapshot.lifecycle_state == "failed" {
                object.insert("status".to_owned(), json!("error"));
                object.insert("error_code".to_owned(), json!(snapshot.error_code));
            }
        }
        health
    }

    fn lifecycle_snapshot(&self) -> VoiceLifecycleSnapshot {
        self.lifecycle
            .lock()
            .map(|state| state.snapshot.clone())
            .unwrap_or_else(|_| VoiceLifecycleSnapshot {
                sequence: 0,
                lifecycle_state: "failed".to_owned(),
                active_consumer_count: 0,
                queued_playback_count: 0,
                error_code: Some("VOICE_WORKER_UNAVAILABLE".to_owned()),
                started_at_unix_ms: None,
                transitioned_at_unix_ms: unix_time_ms(),
            })
    }

    fn transition_if_current(
        &self,
        generation: u64,
        lifecycle_state: &str,
        error_code: Option<&str>,
    ) {
        self.transition_generation(Some(generation), lifecycle_state, error_code);
    }

    fn transition(&self, lifecycle_state: &str, error_code: Option<&str>) {
        self.transition_generation(None, lifecycle_state, error_code);
    }

    fn transition_generation(
        &self,
        generation: Option<u64>,
        lifecycle_state: &str,
        error_code: Option<&str>,
    ) {
        let snapshot = {
            let Ok(mut lifecycle) = self.lifecycle.lock() else {
                return;
            };
            if generation
                .is_some_and(|value| self.operation_generation.load(Ordering::Acquire) != value)
            {
                return;
            }
            if lifecycle.snapshot.lifecycle_state == lifecycle_state
                && lifecycle.snapshot.error_code.as_deref() == error_code
            {
                return;
            }
            lifecycle.snapshot.lifecycle_state = lifecycle_state.to_owned();
            lifecycle.snapshot.error_code = error_code.map(str::to_owned);
            if lifecycle_state == "starting_worker" {
                lifecycle.snapshot.started_at_unix_ms = Some(unix_time_ms());
            } else if lifecycle_state == "stopped" {
                lifecycle.snapshot.started_at_unix_ms = None;
            }
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
            lifecycle.snapshot.clone()
        };
        self.emit_lifecycle(&snapshot);
    }

    fn activate_playback(&self, characters: usize, generation: u64) {
        let snapshot = {
            let Ok(mut lifecycle) = self.lifecycle.lock() else {
                return;
            };
            if self.operation_generation.load(Ordering::Acquire) != generation {
                return;
            }
            lifecycle.idle_since = None;
            lifecycle.snapshot.queued_playback_count =
                lifecycle.snapshot.queued_playback_count.saturating_sub(1);
            lifecycle.queued_characters = lifecycle.queued_characters.saturating_sub(characters);
            lifecycle.snapshot.active_consumer_count += 1;
            if self.operation_generation.load(Ordering::Acquire) == generation {
                lifecycle.snapshot.lifecycle_state = "playing".to_owned();
                lifecycle.snapshot.error_code = None;
            }
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
            lifecycle.snapshot.clone()
        };
        self.emit_lifecycle(&snapshot);
    }

    fn release_playback(&self, characters: usize, active: bool, generation: u64) {
        let snapshot = {
            let Ok(mut lifecycle) = self.lifecycle.lock() else {
                return;
            };
            if self.operation_generation.load(Ordering::Acquire) != generation {
                return;
            }
            if active {
                lifecycle.snapshot.active_consumer_count =
                    lifecycle.snapshot.active_consumer_count.saturating_sub(1);
            } else {
                lifecycle.snapshot.queued_playback_count =
                    lifecycle.snapshot.queued_playback_count.saturating_sub(1);
                lifecycle.queued_characters =
                    lifecycle.queued_characters.saturating_sub(characters);
            }
            if self.operation_generation.load(Ordering::Acquire) == generation
                && lifecycle.snapshot.active_consumer_count == 0
                && lifecycle.snapshot.queued_playback_count == 0
                && lifecycle.snapshot.lifecycle_state == "playing"
            {
                lifecycle.snapshot.lifecycle_state = "ready".to_owned();
            }
            if lifecycle.snapshot.active_consumer_count == 0
                && lifecycle.snapshot.queued_playback_count == 0
            {
                lifecycle.idle_since = Some(Instant::now());
            }
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
            lifecycle.snapshot.clone()
        };
        self.emit_lifecycle(&snapshot);
    }

    fn reset_playback_counts(&self) {
        let snapshot = {
            let Ok(mut lifecycle) = self.lifecycle.lock() else {
                return;
            };
            lifecycle.snapshot.active_consumer_count = 0;
            lifecycle.snapshot.queued_playback_count = 0;
            lifecycle.queued_characters = 0;
            lifecycle.idle_since = None;
            advance_lifecycle_sequence(&mut lifecycle.snapshot);
            lifecycle.snapshot.clone()
        };
        self.emit_lifecycle(&snapshot);
    }

    fn emit_lifecycle(&self, snapshot: &VoiceLifecycleSnapshot) {
        if let Some(app) = &self.app {
            let _ = app.emit(VOICE_WORKER_LIFECYCLE_EVENT, snapshot);
        }
    }

    /// Called by one host maintenance loop. Never starts or warms a worker.
    pub fn release_if_idle(&self) -> Result<bool, VoiceWorkerError> {
        let Ok(_prepare) = self.prepare_lock.try_lock() else {
            return Ok(false);
        };
        let mut lifecycle = self
            .lifecycle
            .lock()
            .map_err(|_| VoiceWorkerError::Unavailable("lifecycle lock is poisoned".to_owned()))?;
        if lifecycle.snapshot.active_consumer_count != 0
            || lifecycle.snapshot.queued_playback_count != 0
            || !matches!(
                lifecycle.snapshot.lifecycle_state.as_str(),
                "ready" | "failed" | "stopped"
            )
        {
            lifecycle.idle_since = None;
            return Ok(false);
        }
        let since = lifecycle.idle_since.get_or_insert_with(Instant::now);
        if since.elapsed() < Duration::from_secs(300) {
            return Ok(false);
        }
        // Use try_lock: connection failure reports can take the locks in reverse order.
        let Ok(mut process) = self.process.try_lock() else {
            return Ok(false);
        };
        if process.is_none() {
            lifecycle.idle_since = None;
            return Ok(false);
        }
        if let Some(mut worker) = process.take() {
            self.operation_generation.fetch_add(1, Ordering::AcqRel);
            if let Err(error) = worker.child.kill() {
                if worker.child.try_wait()?.is_none() {
                    *process = Some(worker);
                    return Err(error.into());
                }
            }
            worker.child.wait()?;
            self.idle_unloads.fetch_add(1, Ordering::Relaxed);
        }
        lifecycle.idle_since = None;
        lifecycle.snapshot.lifecycle_state = "stopped".to_owned();
        lifecycle.snapshot.started_at_unix_ms = None;
        lifecycle.snapshot.error_code = None;
        advance_lifecycle_sequence(&mut lifecycle.snapshot);
        let snapshot = lifecycle.snapshot.clone();
        drop(process);
        drop(lifecycle);
        self.emit_lifecycle(&snapshot);
        Ok(true)
    }

    pub fn diagnostics(&self) -> Value {
        let snapshot = self.lifecycle_snapshot();
        json!({
            "state": snapshot.lifecycle_state,
            "generation": self.operation_generation.load(Ordering::Acquire),
            "active_consumers": snapshot.active_consumer_count,
            "queued_requests": snapshot.queued_playback_count,
            "idle_unload_seconds": 300,
            "idle_unloads": self.idle_unloads.load(Ordering::Relaxed),
            "realtime_audio": self.audio_focus.snapshot(),
            "model_reservation": self.audio_focus.model_resources.snapshot(),
        })
    }
}

struct VoicePlaybackLease<'a> {
    manager: &'a VoiceWorkerManager,
    characters: usize,
    active: bool,
    released: bool,
    generation: u64,
}

impl VoicePlaybackLease<'_> {
    fn activate(&mut self) {
        if self.active || self.released {
            return;
        }
        self.manager
            .activate_playback(self.characters, self.generation);
        self.active = true;
    }
}

impl Drop for VoicePlaybackLease<'_> {
    fn drop(&mut self) {
        if self.released {
            return;
        }
        self.manager
            .release_playback(self.characters, self.active, self.generation);
        self.released = true;
    }
}

fn advance_lifecycle_sequence(snapshot: &mut VoiceLifecycleSnapshot) {
    snapshot.sequence = snapshot.sequence.saturating_add(1);
    snapshot.transitioned_at_unix_ms = unix_time_ms();
}

fn unix_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

pub fn prepared_test_session() -> Result<PreparedVoiceSession, VoiceWorkerError> {
    let id = secure_uuid()?;
    let scope_digest = format!("{:x}", Sha256::digest(b"fairy-voice-settings-test-v1"));
    Ok(PreparedVoiceSession {
        id,
        task_id: "settings".to_owned(),
        conversation_id: "settings".to_owned(),
        turn_id: "settings".to_owned(),
        message_id: None,
        start_offset: 0,
        end_offset: 12,
        validated_text: "你好，我是 Fairy。".to_owned(),
        scope_digest,
        source_cursor: 0,
        status: "prepared".to_owned(),
        created_at: "1970-01-01T00:00:00Z".to_owned(),
        cancelled_at: None,
    })
}

pub fn prepared_realtime_session(text: &str) -> Result<PreparedVoiceSession, VoiceWorkerError> {
    let text = text.trim();
    if text.is_empty() || text.chars().count() > 2_000 || text.chars().any(|char| char == '\0') {
        return Err(VoiceWorkerError::Protocol(
            "realtime voice text is invalid".to_owned(),
        ));
    }
    let id = secure_token()?;
    let scope_digest = format!("{:x}", Sha256::digest(format!("{id}:{text}").as_bytes()));
    Ok(PreparedVoiceSession {
        id,
        task_id: "realtime".to_owned(),
        conversation_id: "realtime".to_owned(),
        turn_id: "realtime".to_owned(),
        message_id: None,
        start_offset: 0,
        end_offset: text.chars().count(),
        validated_text: text.to_owned(),
        scope_digest,
        source_cursor: 0,
        status: "prepared".to_owned(),
        created_at: "transient".to_owned(),
        cancelled_at: None,
    })
}

impl Drop for VoiceWorkerManager {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.process.lock() {
            if let Some(process) = guard.as_mut() {
                let _ = process.child.kill();
                let _ = process.child.wait();
            }
            *guard = None;
        }
    }
}

#[derive(Clone)]
struct WorkerConnection {
    address: SocketAddr,
    bootstrap_token: String,
}

fn request_health(connection: &WorkerConnection) -> Result<Value, VoiceWorkerError> {
    let (status, _, mut reader) = send_request(
        connection.address,
        "GET",
        "/v1/health",
        &connection.bootstrap_token,
        b"",
        Duration::from_secs(5),
    )?;
    let mut body = Vec::new();
    reader.read_to_end(&mut body)?;
    if status != 200 {
        return Err(VoiceWorkerError::Http(status));
    }
    Ok(serde_json::from_slice(&body)?)
}

fn cold_voice_health(launch: &VoiceWorkerLaunch) -> Value {
    let model_dir = launch.data_dir.join("models").join(VOICE_MODEL_DIRECTORY);
    let model_installed = VOICE_MODEL_FILES
        .iter()
        .all(|relative| model_dir.join(relative).is_file());
    let prompt_ready = launch.assets_dir.join("fairy_clone_core.wav").is_file()
        && launch.assets_dir.join("fairy_clone_core.txt").is_file();
    let (status, error_code) = if !model_installed {
        ("model_missing", Some("VOICE_MODEL_MISSING"))
    } else if !prompt_ready {
        ("prompt_missing", Some("VOICE_PROMPT_MISSING"))
    } else {
        ("idle", None)
    };
    json!({
        "status": status,
        "model_repository": VOICE_MODEL_REPOSITORY,
        "model_installed": model_installed,
        "model_ready": false,
        "model_digest": Value::Null,
        "prompt_ready": prompt_ready,
        "cuda_available": false,
        "tensorrt_available": false,
        "onnx_cuda_available": false,
        "backend": Value::Null,
        "device_name": Value::Null,
        "sample_rate": 24_000,
        "error_code": error_code,
    })
}

#[derive(Deserialize)]
struct WorkerHandshake {
    protocol: String,
    port: u16,
}

fn spawn_worker(launch: &VoiceWorkerLaunch) -> Result<WorkerProcess, VoiceWorkerError> {
    validate_launch(launch)?;
    if let Some(parent) = launch.log_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::create_dir_all(&launch.data_dir)?;
    let bootstrap_token = secure_token()?;
    let log = File::options()
        .create(true)
        .append(true)
        .open(&launch.log_path)?;
    let mut command = Command::new(&launch.program);
    command
        .args(&launch.arguments)
        .env("FAIRY_VOICE_BOOTSTRAP_TOKEN", &bootstrap_token)
        .env("FAIRY_VOICE_DATA_DIR", &launch.data_dir)
        .env("FAIRY_COSYVOICE_ROOT", &launch.cosyvoice_root)
        .env("FAIRY_VOICE_ASSETS_DIR", &launch.assets_dir)
        .env("PYTHONPATH", &launch.worker_python_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn()?;
    let handshake = read_worker_handshake(&mut child);
    let handshake = match handshake {
        Ok(handshake) => handshake,
        Err(error) => {
            // JSON/IO/protocol failures need the same cleanup as timeouts.
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    Ok(WorkerProcess {
        child,
        address: SocketAddr::from(([127, 0, 0, 1], handshake.port)),
        bootstrap_token,
    })
}

fn read_worker_handshake(child: &mut Child) -> Result<WorkerHandshake, VoiceWorkerError> {
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| VoiceWorkerError::Protocol("worker stdout is unavailable".to_owned()))?;
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout.take(8_193));
        let mut line = String::new();
        let result = reader.read_line(&mut line).map(|_| line);
        let _ = sender.send(result);
    });
    let line = receiver
        .recv_timeout(Duration::from_secs(30))
        .map_err(|_| VoiceWorkerError::Unavailable("worker handshake timed out".to_owned()))??;
    if line.len() > 8_192 {
        return Err(VoiceWorkerError::Protocol(
            "worker handshake is too large".to_owned(),
        ));
    }
    let handshake: WorkerHandshake = serde_json::from_str(line.trim())?;
    if handshake.protocol != HANDSHAKE_PROTOCOL || handshake.port == 0 {
        return Err(VoiceWorkerError::Protocol(
            "worker handshake is invalid".to_owned(),
        ));
    }
    Ok(handshake)
}

fn validate_launch(launch: &VoiceWorkerLaunch) -> Result<(), VoiceWorkerError> {
    for (label, path) in [
        ("program", &launch.program),
        ("worker", &launch.worker_python_path),
        ("CosyVoice", &launch.cosyvoice_root),
        ("assets", &launch.assets_dir),
    ] {
        if !path.exists() {
            return Err(VoiceWorkerError::Unavailable(format!(
                "{label} path does not exist: {}",
                path.display()
            )));
        }
    }
    Ok(())
}

fn register_token(connection: &WorkerConnection, token: &str) -> Result<(), VoiceWorkerError> {
    let body = serde_json::to_vec(&json!({ "token": token }))?;
    let (status, _, mut reader) = send_request(
        connection.address,
        "POST",
        "/v1/tokens",
        &connection.bootstrap_token,
        &body,
        Duration::from_secs(2),
    )?;
    let mut response = Vec::new();
    reader.read_to_end(&mut response)?;
    if status != 201 {
        return Err(worker_response_error(status, &response));
    }
    Ok(())
}

fn send_request(
    address: SocketAddr,
    method: &str,
    path: &str,
    bearer: &str,
    body: &[u8],
    timeout: Duration,
) -> Result<WorkerResponse, VoiceWorkerError> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    write!(
        stream,
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {bearer}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        address.port(),
        body.len()
    )?;
    stream.write_all(body)?;
    stream.flush()?;
    let mut reader = BufReader::new(stream);
    let mut status_line = String::new();
    reader.read_line(&mut status_line)?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| VoiceWorkerError::Protocol("invalid HTTP status line".to_owned()))?;
    let mut headers = HashMap::new();
    let mut header_bytes = status_line.len();
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line)?;
        if read == 0 {
            return Err(VoiceWorkerError::Protocol(
                "voice worker closed before HTTP headers completed".to_owned(),
            ));
        }
        header_bytes += read;
        if header_bytes > MAX_HEADER_BYTES {
            return Err(VoiceWorkerError::Protocol(
                "voice worker HTTP headers are too large".to_owned(),
            ));
        }
        if line == "\r\n" {
            break;
        }
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| VoiceWorkerError::Protocol("invalid HTTP header".to_owned()))?;
        headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_owned());
    }
    Ok((status, headers, reader))
}

fn required_header<'a>(
    headers: &'a HashMap<String, String>,
    name: &str,
) -> Result<&'a str, VoiceWorkerError> {
    headers
        .get(name)
        .map(String::as_str)
        .ok_or_else(|| VoiceWorkerError::Protocol(format!("missing voice header: {name}")))
}

fn worker_error_message(status: u16, body: &[u8]) -> String {
    serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|value| {
            value
                .get("error_code")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .unwrap_or_else(|| format!("voice worker HTTP {status}"))
}

fn worker_response_error(status: u16, body: &[u8]) -> VoiceWorkerError {
    let message = worker_error_message(status, body);
    if is_stable_worker_code(&message) {
        VoiceWorkerError::Worker(message)
    } else {
        VoiceWorkerError::Http(status)
    }
}

fn is_stable_worker_code(value: &str) -> bool {
    value.len() <= 64
        && value.starts_with("VOICE_")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
}

fn secure_token() -> Result<String, VoiceWorkerError> {
    let mut bytes = [0_u8; 32];
    fill_secure_random(&mut bytes)?;
    Ok(bytes.iter().map(|value| format!("{value:02x}")).collect())
}

fn secure_uuid() -> Result<String, VoiceWorkerError> {
    let mut bytes = [0_u8; 16];
    fill_secure_random(&mut bytes)?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    ))
}

#[cfg(windows)]
fn fill_secure_random(bytes: &mut [u8]) -> Result<(), VoiceWorkerError> {
    use windows_sys::Win32::Security::Cryptography::{
        BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG,
    };

    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            bytes.as_mut_ptr(),
            bytes.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    };
    if status < 0 {
        Err(VoiceWorkerError::Unavailable(format!(
            "Windows RNG failed with NTSTATUS {status}"
        )))
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn fill_secure_random(bytes: &mut [u8]) -> Result<(), VoiceWorkerError> {
    File::open("/dev/urandom")?.read_exact(bytes)?;
    Ok(())
}

pub fn development_voice_launch(data_dir: &Path) -> VoiceWorkerLaunch {
    let v3_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let repository_root = v3_root
        .ancestors()
        .find(|path| path.join("cosyvoice_env/Scripts/python.exe").is_file())
        .unwrap_or(&v3_root)
        .to_path_buf();
    VoiceWorkerLaunch {
        program: std::env::var_os("FAIRY_VOICE_WORKER_PROGRAM")
            .map(PathBuf::from)
            .unwrap_or_else(|| repository_root.join("cosyvoice_env/Scripts/python.exe")),
        arguments: vec![
            "-m".to_owned(),
            "fairy_voice_worker.server".to_owned(),
            "--host".to_owned(),
            "127.0.0.1".to_owned(),
            "--port".to_owned(),
            "0".to_owned(),
        ],
        worker_python_path: v3_root.join("voice-worker/src"),
        cosyvoice_root: std::env::var_os("FAIRY_COSYVOICE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| repository_root.join("third_party/CosyVoice")),
        assets_dir: v3_root.join("voice-worker/assets"),
        data_dir: data_dir.join("voice"),
        log_path: data_dir.join("logs/voice-worker.log"),
    }
}

pub fn bundled_voice_launch(
    data_dir: &Path,
    resource_dir: &Path,
    _desktop_executable: &Path,
) -> VoiceWorkerLaunch {
    let program = std::env::var_os("FAIRY_VOICE_WORKER_PROGRAM")
        .map(PathBuf::from)
        .unwrap_or_else(|| resource_dir.join("runtime/voice-worker/fairy-voice-worker.exe"));
    VoiceWorkerLaunch {
        program,
        arguments: vec![
            "--host".to_owned(),
            "127.0.0.1".to_owned(),
            "--port".to_owned(),
            "0".to_owned(),
        ],
        worker_python_path: resource_dir.to_path_buf(),
        cosyvoice_root: resource_dir.to_path_buf(),
        assets_dir: resource_dir.join("runtime/voice-assets"),
        data_dir: data_dir.join("voice"),
        log_path: data_dir.join("logs/voice-worker.log"),
    }
}

#[cfg(test)]
#[path = "voice_process_tests.rs"]
mod process_tests;

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};
    use std::sync::atomic::Ordering;
    use std::time::{Duration, Instant};

    use super::{
        bundled_voice_launch, fill_secure_random, worker_error_message, worker_response_error,
        VoiceWorkerLaunch, VoiceWorkerManager, MAX_QUEUED_PLAYBACKS,
    };

    #[test]
    #[ignore = "requires the existing local GPU Python runtime and an explicit probe directory"]
    fn live_development_voice_health() {
        let directory = std::env::var_os("FAIRY_VOICE_PROBE_DIR")
            .map(PathBuf::from)
            .expect("set FAIRY_VOICE_PROBE_DIR to an isolated probe directory");
        assert!(directory.is_absolute());
        let manager = VoiceWorkerManager::new(super::development_voice_launch(&directory));
        let prepare = std::env::var("FAIRY_VOICE_PROBE_PREPARE").as_deref() == Ok("1");
        let result = if prepare {
            manager.prepare()
        } else {
            manager.health()
        };
        manager.shutdown();
        match result {
            Ok(health) => {
                assert!(health.get("status").is_some());
                if prepare {
                    assert_eq!(health["status"], "ready");
                }
            }
            Err(error) => panic!("Voice launch/health failed: {error}"),
        }
    }

    #[test]
    fn secure_random_fills_distinct_nonzero_tokens() {
        let mut first = [0_u8; 32];
        let mut second = [0_u8; 32];
        fill_secure_random(&mut first).expect("first token");
        fill_secure_random(&mut second).expect("second token");
        assert_ne!(first, [0_u8; 32]);
        assert_ne!(first, second);
    }

    #[test]
    fn worker_errors_expose_only_stable_codes() {
        assert_eq!(
            worker_error_message(503, br#"{"error_code":"VOICE_MODEL_MISSING"}"#),
            "VOICE_MODEL_MISSING"
        );
        assert_eq!(
            worker_error_message(500, b"private traceback"),
            "voice worker HTTP 500"
        );
        assert_eq!(
            worker_response_error(
                503,
                br#"{"error_code":"VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE"}"#,
            )
            .public_code(),
            "VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE"
        );
        assert_eq!(
            worker_response_error(500, br#"{"error_code":"secret.path"}"#).public_code(),
            "VOICE_WORKER_HTTP_ERROR"
        );
    }

    #[test]
    fn bundled_worker_uses_read_only_runtime_and_voice_assets() {
        let launch = bundled_voice_launch(
            Path::new("C:/FairyData"),
            Path::new("C:/Program Files/Fairy/resources"),
            Path::new("C:/Program Files/Fairy/fairy.exe"),
        );
        assert_eq!(
            launch.program,
            Path::new(
                "C:/Program Files/Fairy/resources/runtime/voice-worker/fairy-voice-worker.exe"
            )
        );
        assert_eq!(
            launch.assets_dir,
            Path::new("C:/Program Files/Fairy/resources/runtime/voice-assets")
        );
        assert_eq!(launch.data_dir, Path::new("C:/FairyData/voice"));
    }

    #[test]
    fn cold_status_never_starts_the_voice_worker() {
        let root =
            std::env::temp_dir().join(format!("fairy-voice-cold-status-{}", std::process::id()));
        let manager = VoiceWorkerManager::new(VoiceWorkerLaunch {
            program: PathBuf::from("missing-worker.exe"),
            arguments: Vec::new(),
            worker_python_path: root.join("worker"),
            cosyvoice_root: root.join("cosyvoice"),
            assets_dir: root.join("assets"),
            data_dir: root.join("data"),
            log_path: root.join("voice.log"),
        });

        let status = manager.status().expect("cold health");
        assert_eq!(status["status"], "model_missing");
        assert_eq!(status["lifecycle_state"], "stopped");
        assert_eq!(status["sequence"], 0);
        assert_eq!(status["queued_playback_count"], 0);
        assert_eq!(status["active_consumer_count"], 0);
        assert!(manager.process.lock().expect("worker lock").is_none());
    }

    #[test]
    fn lifecycle_sequence_fences_a_late_ready_transition_after_stop() {
        let manager = VoiceWorkerManager::new(test_launch("voice-lifecycle-fence"));
        let generation = manager.operation_generation.load(Ordering::Acquire);

        manager.transition_if_current(generation, "starting_worker", None);
        manager.stop().expect("stop");
        manager.transition_if_current(generation, "ready", None);

        let snapshot = manager.lifecycle_snapshot();
        assert_eq!(snapshot.lifecycle_state, "stopped");
        assert!(snapshot.sequence >= 3);
        assert_eq!(snapshot.active_consumer_count, 0);
        assert_eq!(snapshot.queued_playback_count, 0);
    }

    #[test]
    fn playback_queue_rejects_work_beyond_its_item_bound() {
        let manager = VoiceWorkerManager::new(test_launch("voice-queue-bound"));
        let leases = (0..MAX_QUEUED_PLAYBACKS)
            .map(|_| manager.queue_playback(1).expect("bounded queue item"))
            .collect::<Vec<_>>();

        let error = match manager.queue_playback(1) {
            Ok(_) => panic!("queue must reject overflow"),
            Err(error) => error,
        };
        assert_eq!(error.public_code(), "VOICE_PLAYBACK_QUEUE_FULL");
        assert_eq!(
            manager.lifecycle_snapshot().queued_playback_count,
            MAX_QUEUED_PLAYBACKS
        );

        drop(leases);
        assert_eq!(manager.lifecycle_snapshot().queued_playback_count, 0);
    }

    #[test]
    fn cancellation_never_starts_a_cold_worker() {
        let manager = VoiceWorkerManager::new(test_launch("voice-cold-cancel"));

        manager.cancel("session-before-start").expect("cancel");

        assert!(manager.process.lock().expect("worker lock").is_none());
        assert!(manager.take_cancellation("session-before-start"));
    }

    #[test]
    fn old_playback_release_does_not_decrement_a_new_generation() {
        let manager = VoiceWorkerManager::new(test_launch("voice-count-fence"));
        let mut old = manager.queue_playback(12).unwrap();
        old.activate();
        manager.stop().unwrap();
        let new = manager.queue_playback(7).unwrap();
        drop(old);
        assert_eq!(manager.lifecycle_snapshot().queued_playback_count, 1);
        assert_eq!(manager.lifecycle_snapshot().active_consumer_count, 0);
        assert_eq!(manager.lifecycle.lock().unwrap().queued_characters, 7);
        drop(new);
        assert_eq!(manager.lifecycle_snapshot().queued_playback_count, 0);
    }

    #[test]
    fn idle_maintenance_does_not_start_cold_workers_or_interrupt_consumers() {
        let manager = VoiceWorkerManager::new(test_launch("voice-idle"));
        assert!(!manager.release_if_idle().unwrap());
        assert!(manager.process.lock().unwrap().is_none());
        manager.transition("ready", None);
        manager.lifecycle.lock().unwrap().idle_since =
            Some(Instant::now() - Duration::from_secs(301));
        let mut lease = manager.queue_playback(10).unwrap();
        assert!(manager.lifecycle.lock().unwrap().idle_since.is_none());
        assert!(!manager.release_if_idle().unwrap());
        lease.activate();
        assert!(!manager.release_if_idle().unwrap());
        let realtime = manager.audio_focus.reserve_realtime().unwrap();
        drop(lease);
        manager.lifecycle.lock().unwrap().idle_since =
            Some(Instant::now() - Duration::from_secs(301));
        assert!(!manager.release_if_idle().unwrap());
        drop(realtime);
        assert!(manager.process.lock().unwrap().is_none());
    }

    #[test]
    fn stale_prepare_cannot_spawn_after_stop() {
        let manager = VoiceWorkerManager::new(test_launch("voice-stale-prepare"));
        let old = manager.operation_generation.load(Ordering::Acquire);
        manager.stop().unwrap();
        assert!(manager.connection_for_generation(old).is_err());
        assert!(manager.process.lock().unwrap().is_none());
    }

    #[test]
    fn explicit_host_shutdown_rejects_late_requests_without_starting_a_process() {
        let manager = VoiceWorkerManager::new(test_launch("voice-shutdown"));
        manager.shutdown();
        manager.shutdown();
        assert!(manager.queue_playback(1).is_err());
        assert!(manager.connection().is_err());
        assert!(manager.process.lock().unwrap().is_none());
    }

    fn test_launch(label: &str) -> VoiceWorkerLaunch {
        let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
        VoiceWorkerLaunch {
            program: PathBuf::from("missing-worker.exe"),
            arguments: Vec::new(),
            worker_python_path: root.join("worker"),
            cosyvoice_root: root.join("cosyvoice"),
            assets_dir: root.join("assets"),
            data_dir: root.join("data"),
            log_path: root.join("voice.log"),
        }
    }
}
