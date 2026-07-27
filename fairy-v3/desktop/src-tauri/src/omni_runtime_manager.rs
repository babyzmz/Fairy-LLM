use std::fs;
use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use thiserror::Error;

use crate::process_lifetime::configure_managed_child;

pub const LOAD_DEADLINE: Duration = Duration::from_secs(90);
pub const STOP_DEADLINE: Duration = Duration::from_secs(5);
const STDERR_LIMIT: usize = 64 * 1024;
const PIPE_PREFIX: &str = r"\\.\pipe\fairy-omni-";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeManagerState {
    Idle,
    Loading,
    Ready,
    Quarantined,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeRecovery {
    None,
    RestartOnce,
    Quarantined,
}

#[derive(Clone, Debug)]
pub struct RuntimeLaunchSpec {
    pub runtime_path: PathBuf,
    pub media_pipe: String,
}

pub struct RuntimeControlIo {
    pub stdin: ChildStdin,
    pub stdout: ChildStdout,
}

#[derive(Debug, Error)]
pub enum OmniRuntimeManagerError {
    #[error("the Omni runtime is quarantined")]
    Quarantined,
    #[error("the Omni runtime is already active")]
    AlreadyActive,
    #[error("the Omni runtime launch path is unsafe")]
    UnsafeLaunch,
    #[error("the Omni runtime control pipes are unavailable")]
    ControlUnavailable,
    #[error("the Omni runtime process operation failed: {0}")]
    Io(#[from] std::io::Error),
}

pub struct OmniRuntimeManager {
    state: RuntimeManagerState,
    child: Option<Child>,
    stderr_reader: Option<JoinHandle<Vec<u8>>>,
    load_deadline: Option<Instant>,
    crash_streak: u8,
    pre_candidate_restart_used: bool,
}

impl Default for OmniRuntimeManager {
    fn default() -> Self {
        Self {
            state: RuntimeManagerState::Idle,
            child: None,
            stderr_reader: None,
            load_deadline: None,
            crash_streak: 0,
            pre_candidate_restart_used: false,
        }
    }
}

impl OmniRuntimeManager {
    #[must_use]
    pub fn state(&self) -> RuntimeManagerState {
        self.state
    }

    #[must_use]
    pub fn is_quarantined(&self) -> bool {
        self.state == RuntimeManagerState::Quarantined
    }

    pub fn spawn(
        &mut self,
        spec: &RuntimeLaunchSpec,
        now: Instant,
    ) -> Result<RuntimeControlIo, OmniRuntimeManagerError> {
        if self.is_quarantined() {
            return Err(OmniRuntimeManagerError::Quarantined);
        }
        if self.child.is_some() || self.state != RuntimeManagerState::Idle {
            return Err(OmniRuntimeManagerError::AlreadyActive);
        }
        validate_launch_spec(spec)?;

        let working_directory = spec
            .runtime_path
            .parent()
            .ok_or(OmniRuntimeManagerError::UnsafeLaunch)?;
        let mut command = Command::new(&spec.runtime_path);
        command
            .arg("--stdio")
            .arg("--media-pipe")
            .arg(&spec.media_pipe)
            .current_dir(working_directory)
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        configure_managed_child(&mut command);

        let mut child = command.spawn()?;
        let Some(stdin) = child.stdin.take() else {
            terminate_child(&mut child);
            return Err(OmniRuntimeManagerError::ControlUnavailable);
        };
        let Some(stdout) = child.stdout.take() else {
            terminate_child(&mut child);
            return Err(OmniRuntimeManagerError::ControlUnavailable);
        };
        let Some(stderr) = child.stderr.take() else {
            terminate_child(&mut child);
            return Err(OmniRuntimeManagerError::ControlUnavailable);
        };
        self.stderr_reader = Some(thread::spawn(move || drain_bounded(stderr)));
        self.child = Some(child);
        self.state = RuntimeManagerState::Loading;
        self.load_deadline = Some(now + LOAD_DEADLINE);
        Ok(RuntimeControlIo { stdin, stdout })
    }

    pub fn mark_ready(&mut self) -> Result<(), OmniRuntimeManagerError> {
        if self.state != RuntimeManagerState::Loading {
            return Err(OmniRuntimeManagerError::AlreadyActive);
        }
        self.state = RuntimeManagerState::Ready;
        self.load_deadline = None;
        self.crash_streak = 0;
        Ok(())
    }

    pub fn load_timed_out(&self, now: Instant) -> bool {
        self.state == RuntimeManagerState::Loading
            && self.load_deadline.is_some_and(|deadline| now >= deadline)
    }

    pub fn record_unexpected_exit(&mut self, candidate_active: bool) -> RuntimeRecovery {
        self.child = None;
        self.join_stderr();
        self.load_deadline = None;
        self.crash_streak = self.crash_streak.saturating_add(1);

        if !candidate_active && !self.pre_candidate_restart_used {
            self.pre_candidate_restart_used = true;
            self.state = RuntimeManagerState::Idle;
            return RuntimeRecovery::RestartOnce;
        }
        if self.crash_streak >= 2 || self.pre_candidate_restart_used {
            self.state = RuntimeManagerState::Quarantined;
            RuntimeRecovery::Quarantined
        } else {
            self.state = RuntimeManagerState::Idle;
            RuntimeRecovery::None
        }
    }

    pub fn wait_for_stop(&mut self) -> Result<(), OmniRuntimeManagerError> {
        let Some(mut child) = self.child.take() else {
            self.state = if self.is_quarantined() {
                RuntimeManagerState::Quarantined
            } else {
                RuntimeManagerState::Idle
            };
            return Ok(());
        };
        let deadline = Instant::now() + STOP_DEADLINE;
        loop {
            if child.try_wait()?.is_some() {
                break;
            }
            if Instant::now() >= deadline {
                child.kill()?;
                child.wait()?;
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        self.join_stderr();
        self.load_deadline = None;
        self.state = RuntimeManagerState::Idle;
        Ok(())
    }

    pub fn explicit_verify_recovery(&mut self) -> Result<(), OmniRuntimeManagerError> {
        self.wait_for_stop()?;
        self.state = RuntimeManagerState::Idle;
        self.crash_streak = 0;
        self.pre_candidate_restart_used = false;
        Ok(())
    }

    fn join_stderr(&mut self) {
        if let Some(reader) = self.stderr_reader.take() {
            let _ = reader.join();
        }
    }
}

impl Drop for OmniRuntimeManager {
    fn drop(&mut self) {
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child = None;
        self.join_stderr();
    }
}

fn validate_launch_spec(spec: &RuntimeLaunchSpec) -> Result<(), OmniRuntimeManagerError> {
    let metadata = fs::symlink_metadata(&spec.runtime_path)
        .map_err(|_| OmniRuntimeManagerError::UnsafeLaunch)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || spec.runtime_path.file_name().and_then(|name| name.to_str())
            != Some("fairy-omni-runtime.exe")
    {
        return Err(OmniRuntimeManagerError::UnsafeLaunch);
    }
    let Some(token) = spec.media_pipe.strip_prefix(PIPE_PREFIX) else {
        return Err(OmniRuntimeManagerError::UnsafeLaunch);
    };
    if token.is_empty()
        || token.len() > 96
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(OmniRuntimeManagerError::UnsafeLaunch);
    }
    Ok(())
}

fn drain_bounded(mut input: impl Read) -> Vec<u8> {
    let mut retained = Vec::with_capacity(4096);
    let mut buffer = [0_u8; 4096];
    loop {
        let Ok(read) = input.read(&mut buffer) else {
            break;
        };
        if read == 0 {
            break;
        }
        let remaining = STDERR_LIMIT.saturating_sub(retained.len());
        retained.extend_from_slice(&buffer[..remaining.min(read)]);
    }
    retained
}

fn terminate_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_pre_candidate_restart_then_quarantine_is_enforced() {
        let mut manager = OmniRuntimeManager::default();
        manager.state = RuntimeManagerState::Loading;
        assert_eq!(
            manager.record_unexpected_exit(false),
            RuntimeRecovery::RestartOnce
        );
        assert_eq!(manager.state(), RuntimeManagerState::Idle);
        manager.state = RuntimeManagerState::Loading;
        assert_eq!(
            manager.record_unexpected_exit(false),
            RuntimeRecovery::Quarantined
        );
        assert!(manager.is_quarantined());
    }

    #[test]
    fn candidate_crashes_do_not_auto_restart_and_repeat_crashes_quarantine() {
        let mut manager = OmniRuntimeManager::default();
        manager.state = RuntimeManagerState::Ready;
        assert_eq!(manager.record_unexpected_exit(true), RuntimeRecovery::None);
        manager.state = RuntimeManagerState::Ready;
        assert_eq!(
            manager.record_unexpected_exit(true),
            RuntimeRecovery::Quarantined
        );
    }

    #[test]
    fn explicit_verify_is_the_only_quarantine_recovery() {
        let mut manager = OmniRuntimeManager::default();
        manager.state = RuntimeManagerState::Quarantined;
        manager.crash_streak = 2;
        manager.pre_candidate_restart_used = true;
        manager.explicit_verify_recovery().expect("verify recovery");
        assert_eq!(manager.state(), RuntimeManagerState::Idle);
        assert_eq!(manager.crash_streak, 0);
        assert!(!manager.pre_candidate_restart_used);
    }

    #[test]
    fn load_and_stop_deadlines_are_bounded() {
        assert_eq!(LOAD_DEADLINE, Duration::from_secs(90));
        assert_eq!(STOP_DEADLINE, Duration::from_secs(5));
        let now = Instant::now();
        let mut manager = OmniRuntimeManager::default();
        manager.state = RuntimeManagerState::Loading;
        manager.load_deadline = Some(now + LOAD_DEADLINE);
        assert!(!manager.load_timed_out(now + LOAD_DEADLINE - Duration::from_millis(1)));
        assert!(manager.load_timed_out(now + LOAD_DEADLINE));
    }

    #[test]
    fn unsafe_pipe_names_are_rejected_before_spawn() {
        let spec = RuntimeLaunchSpec {
            runtime_path: PathBuf::from("fairy-omni-runtime.exe"),
            media_pipe: r"\\.\pipe\fairy-omni-..\escape".to_owned(),
        };
        assert!(matches!(
            validate_launch_spec(&spec),
            Err(OmniRuntimeManagerError::UnsafeLaunch)
        ));
    }
}
