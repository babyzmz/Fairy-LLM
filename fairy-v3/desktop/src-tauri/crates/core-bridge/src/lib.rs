use std::collections::BTreeMap;
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;

use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct CoreLaunchSpec {
    pub program: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub current_dir: Option<PathBuf>,
}

impl CoreLaunchSpec {
    pub fn development(
        core_root: impl AsRef<std::path::Path>,
        data_dir: impl AsRef<std::path::Path>,
    ) -> Self {
        let core_root = core_root
            .as_ref()
            .canonicalize()
            .unwrap_or_else(|_| core_root.as_ref().to_path_buf());
        let data_dir = data_dir.as_ref().to_path_buf();
        let windows_python = core_root.join(".venv/Scripts/python.exe");
        let unix_python = core_root.join(".venv/bin/python");
        let program = env::var("FAIRY_CORE_PROGRAM").unwrap_or_else(|_| {
            if windows_python.is_file() {
                windows_python.to_string_lossy().into_owned()
            } else if unix_python.is_file() {
                unix_python.to_string_lossy().into_owned()
            } else {
                "python".to_owned()
            }
        });
        let env = BTreeMap::from([
            (
                "FAIRY_V3_DATA_DIR".to_owned(),
                data_dir.to_string_lossy().into_owned(),
            ),
            (
                "PYTHONPATH".to_owned(),
                core_root.join("src").to_string_lossy().into_owned(),
            ),
            ("PYTHONIOENCODING".to_owned(), "utf-8".to_owned()),
            ("PYTHONUNBUFFERED".to_owned(), "1".to_owned()),
        ]);
        Self {
            program,
            args: vec![
                "-u".to_owned(),
                "-m".to_owned(),
                "fairy_core.transports.stdio".to_owned(),
            ],
            env,
            current_dir: Some(core_root),
        }
    }
}

#[derive(Debug, Error)]
pub enum CoreBridgeError {
    #[error("failed to launch or communicate with Fairy Core: {0}")]
    Io(#[from] std::io::Error),
    #[error("Fairy Core returned invalid JSON: {0}")]
    InvalidResponse(#[from] serde_json::Error),
    #[error("JSON-RPC request is missing an integer id")]
    MissingRequestId,
    #[error("JSON-RPC response id mismatch: expected {expected}, got {actual}")]
    ResponseIdMismatch { expected: i64, actual: i64 },
    #[error("Fairy Core process was interrupted")]
    WorkerInterrupted,
    #[error("Fairy Core bridge lock is poisoned")]
    LockPoisoned,
}

struct ProcessIo {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl Drop for ProcessIo {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub struct CoreBridge {
    process: Mutex<ProcessIo>,
}

impl CoreBridge {
    pub fn spawn(spec: CoreLaunchSpec) -> Result<Self, CoreBridgeError> {
        let mut command = Command::new(spec.program);
        command
            .args(spec.args)
            .envs(spec.env)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        if let Some(current_dir) = spec.current_dir {
            command.current_dir(current_dir);
        }
        let mut child = command.spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or(CoreBridgeError::WorkerInterrupted)?;
        let stdout = child
            .stdout
            .take()
            .ok_or(CoreBridgeError::WorkerInterrupted)?;
        Ok(Self {
            process: Mutex::new(ProcessIo {
                child,
                stdin,
                stdout: BufReader::new(stdout),
            }),
        })
    }

    pub fn call(&self, request: Value) -> Result<Value, CoreBridgeError> {
        let expected_id = request
            .get("id")
            .and_then(Value::as_i64)
            .ok_or(CoreBridgeError::MissingRequestId)?;
        let mut process = self
            .process
            .lock()
            .map_err(|_| CoreBridgeError::LockPoisoned)?;

        let mut payload = serde_json::to_vec(&request)?;
        payload.push(b'\n');
        write_request(&mut process.stdin, &payload)?;

        let mut response_line = String::new();
        let bytes_read = process
            .stdout
            .read_line(&mut response_line)
            .map_err(map_process_io)?;
        if bytes_read == 0 {
            return Err(CoreBridgeError::WorkerInterrupted);
        }
        let response: Value = serde_json::from_str(&response_line)?;
        let actual_id = response
            .get("id")
            .and_then(Value::as_i64)
            .ok_or(CoreBridgeError::MissingRequestId)?;
        if actual_id != expected_id {
            return Err(CoreBridgeError::ResponseIdMismatch {
                expected: expected_id,
                actual: actual_id,
            });
        }
        Ok(response)
    }
}

fn write_request(stdin: &mut ChildStdin, payload: &[u8]) -> Result<(), CoreBridgeError> {
    stdin.write_all(payload).map_err(map_process_io)?;
    stdin.flush().map_err(map_process_io)
}

fn map_process_io(error: std::io::Error) -> CoreBridgeError {
    match error.kind() {
        std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::UnexpectedEof => {
            CoreBridgeError::WorkerInterrupted
        }
        _ => CoreBridgeError::Io(error),
    }
}
