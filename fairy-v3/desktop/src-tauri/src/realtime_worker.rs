use std::fs::File;
use std::io::{BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use fairy_realtime_worker::{read_frame, write_frame, HostCommand, ProviderKind};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use thiserror::Error;
use zeroize::Zeroizing;

pub const REALTIME_WORKER_EVENT: &str = "fairy-realtime-event";
const REALTIME_PROTOCOL: &str = "fairy-realtime-worker-v1";

#[derive(Clone, Debug)]
pub struct RealtimeWorkerLaunch {
    pub program: PathBuf,
    pub log_path: PathBuf,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeProvider {
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

impl From<RealtimeProvider> for ProviderKind {
    fn from(value: RealtimeProvider) -> Self {
        match value {
            RealtimeProvider::GeminiLive => Self::GeminiLive,
            RealtimeProvider::GlmRealtimeFlash => Self::GlmRealtimeFlash,
            RealtimeProvider::GlmRealtimeAir => Self::GlmRealtimeAir,
        }
    }
}

impl RealtimeProvider {
    pub fn credential_provider(&self) -> &'static str {
        match self {
            Self::GeminiLive => "gemini",
            Self::GlmRealtimeFlash | Self::GlmRealtimeAir => "zhipu",
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct RealtimeWorkerStartInput {
    pub session_id: String,
    pub provider: RealtimeProvider,
    pub voice_mode: String,
    pub source_id: Option<u64>,
    pub screen_enabled: bool,
    pub game_audio_enabled: bool,
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

#[derive(Clone, Debug, Serialize)]
pub struct RealtimeWorkerStatus {
    pub running: bool,
    pub session_id: Option<String>,
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
}

pub struct RealtimeWorkerManager {
    launch: RealtimeWorkerLaunch,
    process: Mutex<Option<WorkerProcess>>,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
}

impl RealtimeWorkerManager {
    pub fn new(launch: RealtimeWorkerLaunch) -> Self {
        Self {
            launch,
            process: Mutex::new(None),
            usage: Arc::new(Mutex::new(RealtimeWorkerUsage::default())),
        }
    }

    pub fn status(&self) -> RealtimeWorkerStatus {
        let mut guard = self.process.lock().expect("realtime worker lock poisoned");
        if guard
            .as_mut()
            .is_some_and(|process| process.child.try_wait().ok().flatten().is_some())
        {
            *guard = None;
        }
        RealtimeWorkerStatus {
            running: guard.is_some(),
            session_id: guard
                .as_ref()
                .and_then(|process| process.session_id.clone()),
            usage: self.usage_snapshot(),
        }
    }

    pub fn start(
        &self,
        app: &AppHandle,
        input: RealtimeWorkerStartInput,
        credential: Zeroizing<String>,
    ) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        validate_capture_scope(&input)?;
        let mut guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        if let Some(process) = guard.as_mut() {
            if process.child.try_wait()?.is_none() {
                return Err(RealtimeWorkerError::Busy);
            }
            *guard = None;
        }
        if let Ok(mut usage) = self.usage.lock() {
            *usage = RealtimeWorkerUsage::default();
        }
        let mut process = spawn_worker(&self.launch, app.clone(), Arc::clone(&self.usage))?;
        let command = HostCommand::Start {
            session_id: input.session_id.clone(),
            provider: input.provider.into(),
            voice_mode: input.voice_mode,
            source_id: input.source_id,
            screen_enabled: input.screen_enabled,
            game_audio_enabled: input.game_audio_enabled,
            credential: credential.to_string(),
        };
        if send_command(&process.input, &command).is_err() {
            let _ = process.child.kill();
            let _ = process.child.wait();
            return Err(RealtimeWorkerError::Protocol);
        }
        process.session_id = Some(input.session_id);
        let status = RealtimeWorkerStatus {
            running: true,
            session_id: process.session_id.clone(),
            usage: self.usage_snapshot(),
        };
        *guard = Some(process);
        Ok(status)
    }

    pub fn stop(&self, session_id: &str) -> Result<RealtimeWorkerStatus, RealtimeWorkerError> {
        let mut guard = self
            .process
            .lock()
            .map_err(|_| RealtimeWorkerError::Protocol)?;
        let Some(mut process) = guard.take() else {
            return Ok(RealtimeWorkerStatus {
                running: false,
                session_id: None,
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
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if process.child.try_wait()?.is_some() {
                return Ok(RealtimeWorkerStatus {
                    running: false,
                    session_id: None,
                    usage: self.usage_snapshot(),
                });
            }
            thread::sleep(Duration::from_millis(20));
        }
        let _ = process.child.kill();
        let _ = process.child.wait();
        Ok(RealtimeWorkerStatus {
            running: false,
            session_id: None,
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

    fn usage_snapshot(&self) -> RealtimeWorkerUsage {
        self.usage
            .lock()
            .map(|usage| usage.clone())
            .unwrap_or_default()
    }
}

fn validate_capture_scope(input: &RealtimeWorkerStartInput) -> Result<(), RealtimeWorkerError> {
    if (input.screen_enabled || input.game_audio_enabled) && input.source_id.is_none() {
        return Err(RealtimeWorkerError::Protocol);
    }
    if input.game_audio_enabled && !input.screen_enabled {
        return Err(RealtimeWorkerError::Protocol);
    }
    Ok(())
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
    }
}

fn spawn_worker(
    launch: &RealtimeWorkerLaunch,
    app: AppHandle,
    usage: Arc<Mutex<RealtimeWorkerUsage>>,
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
                    if value.get("type").and_then(Value::as_str) == Some("usage") {
                        if let Ok(next) =
                            serde_json::from_value::<RealtimeWorkerUsage>(value.clone())
                        {
                            if let Ok(mut current) = usage.lock() {
                                *current = next;
                            }
                        }
                    }
                    let _ = app.emit(REALTIME_WORKER_EVENT, value);
                }
                Ok(None) | Err(_) => {
                    if !reader_expected_shutdown.load(Ordering::Acquire) {
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
        }),
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            Err(RealtimeWorkerError::Protocol)
        }
    }
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

pub fn development_realtime_launch(data_dir: &Path) -> RealtimeWorkerLaunch {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    RealtimeWorkerLaunch {
        program: manifest.join("target/debug/fairy-realtime-worker.exe"),
        log_path: data_dir.join("logs/realtime-worker.log"),
    }
}

pub fn bundled_realtime_launch(data_dir: &Path, resource_dir: &Path) -> RealtimeWorkerLaunch {
    RealtimeWorkerLaunch {
        program: resource_dir.join("runtime/realtime-worker/fairy-realtime-worker.exe"),
        log_path: data_dir.join("logs/realtime-worker.log"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_map_to_separate_credential_accounts() {
        assert_eq!(RealtimeProvider::GeminiLive.credential_provider(), "gemini");
        assert_eq!(
            RealtimeProvider::GlmRealtimeFlash.credential_provider(),
            "zhipu"
        );
        assert_eq!(
            RealtimeProvider::GlmRealtimeAir.credential_provider(),
            "zhipu"
        );
    }

    #[test]
    fn development_launch_uses_workspace_debug_binary() {
        let launch = development_realtime_launch(Path::new("C:/fairy-data"));
        assert!(launch
            .program
            .ends_with("target/debug/fairy-realtime-worker.exe"));
    }

    #[test]
    fn capture_and_process_audio_require_one_selected_window() {
        let mut input = RealtimeWorkerStartInput {
            session_id: "session-1".to_owned(),
            provider: RealtimeProvider::GeminiLive,
            voice_mode: "native".to_owned(),
            source_id: None,
            screen_enabled: true,
            game_audio_enabled: false,
        };
        assert!(validate_capture_scope(&input).is_err());

        input.source_id = Some(42);
        input.screen_enabled = false;
        input.game_audio_enabled = true;
        assert!(validate_capture_scope(&input).is_err());

        input.screen_enabled = true;
        assert!(validate_capture_scope(&input).is_ok());
    }
}
