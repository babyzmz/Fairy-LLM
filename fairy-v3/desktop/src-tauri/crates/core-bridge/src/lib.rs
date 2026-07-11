use std::collections::BTreeMap;
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;

use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct CoreLaunchSpec {
    pub program: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub clear_environment: bool,
    pub current_dir: Option<PathBuf>,
}

impl CoreLaunchSpec {
    pub fn development(
        core_root: impl AsRef<std::path::Path>,
        data_dir: impl AsRef<std::path::Path>,
    ) -> Self {
        Self::development_with_environment(core_root, data_dir, &env::vars().collect())
    }

    pub fn development_with_environment(
        core_root: impl AsRef<Path>,
        data_dir: impl AsRef<Path>,
        parent_environment: &BTreeMap<String, String>,
    ) -> Self {
        let core_root = core_root
            .as_ref()
            .canonicalize()
            .unwrap_or_else(|_| core_root.as_ref().to_path_buf());
        let capabilities_root = core_root
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("capabilities");
        let data_dir = data_dir.as_ref().to_path_buf();
        let core_python = development_python(&core_root);
        let capabilities_python = development_python(&capabilities_root);
        let composed_available = capabilities_python.is_some()
            && capabilities_root
                .join("src/fairy_capabilities/stdio.py")
                .is_file();
        let module = parent_environment
            .get("FAIRY_CORE_MODULE")
            .filter(|value| !value.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| {
                if composed_available {
                    "fairy_capabilities.stdio".to_owned()
                } else {
                    "fairy_core.transports.stdio".to_owned()
                }
            });
        let program = parent_environment
            .get("FAIRY_CORE_PROGRAM")
            .filter(|value| !value.trim().is_empty())
            .cloned()
            .or_else(|| {
                if module.starts_with("fairy_capabilities.") {
                    capabilities_python.clone()
                } else {
                    None
                }
            })
            .or(core_python)
            .unwrap_or_else(|| "python".to_owned());
        let mut child_environment = filtered_child_environment(parent_environment);
        let mut python_paths = vec![core_root.join("src")];
        if capabilities_root.join("src").is_dir() {
            python_paths.insert(0, capabilities_root.join("src"));
        }
        let python_path = env::join_paths(python_paths)
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        child_environment.insert(
            "FAIRY_V3_DATA_DIR".to_owned(),
            data_dir.to_string_lossy().into_owned(),
        );
        child_environment.insert("PYTHONPATH".to_owned(), python_path);
        child_environment.insert("PYTHONIOENCODING".to_owned(), "utf-8".to_owned());
        child_environment.insert("PYTHONUNBUFFERED".to_owned(), "1".to_owned());
        Self {
            program,
            args: vec!["-u".to_owned(), "-m".to_owned(), module.clone()],
            env: child_environment,
            clear_environment: true,
            current_dir: Some(if module.starts_with("fairy_capabilities.") {
                capabilities_root
            } else {
                core_root
            }),
        }
    }
}

fn development_python(root: &Path) -> Option<String> {
    let windows_python = root.join(".venv/Scripts/python.exe");
    let unix_python = root.join(".venv/bin/python");
    [windows_python, unix_python]
        .into_iter()
        .find(|candidate| candidate.is_file())
        .map(|candidate| candidate.to_string_lossy().into_owned())
}

fn filtered_child_environment(
    parent_environment: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    const OS_RUNTIME_KEYS: &[&str] = &[
        "APPDATA",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    ];
    parent_environment
        .iter()
        .filter(|(key, _)| {
            key.starts_with("FAIRY_PROVIDER_")
                || OS_RUNTIME_KEYS
                    .iter()
                    .any(|allowed| key.eq_ignore_ascii_case(allowed))
        })
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
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
        if spec.clear_environment {
            command.env_clear();
        }
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
