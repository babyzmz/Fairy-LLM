use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RealtimeSidecarDecision {
    RestartOnce,
    Quarantine,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeSidecarSnapshot {
    pub restart_used: bool,
    pub quarantined: bool,
    pub context_interrupted: bool,
    pub failure_count: u8,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct SidecarQuarantineRecord {
    schema_version: u8,
    model_version: String,
    manifest_digest: String,
    app_version: String,
    failure_count: u8,
    error_code: String,
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
pub enum RealtimeSidecarSupervisorError {
    #[error("the realtime Sidecar quarantine store failed")]
    Store,
}

#[derive(Clone, Debug)]
pub struct RealtimeSidecarSupervisor {
    path: PathBuf,
    model_version: String,
    manifest_digest: String,
    restart_used: bool,
    quarantined: bool,
    context_interrupted: bool,
    failure_count: u8,
    error_code: Option<String>,
}

impl RealtimeSidecarSupervisor {
    pub fn load(path: PathBuf, model_version: String, manifest_digest: String) -> Self {
        let mut supervisor = Self {
            path,
            model_version,
            manifest_digest,
            restart_used: false,
            quarantined: false,
            context_interrupted: false,
            failure_count: 0,
            error_code: None,
        };
        let Some(bytes) = fs::read(&supervisor.path).ok() else {
            return supervisor;
        };
        let Ok(record) = serde_json::from_slice::<SidecarQuarantineRecord>(&bytes) else {
            supervisor.fail_closed("SIDECAR_QUARANTINE_INVALID");
            return supervisor;
        };
        if record.schema_version != 1
            || record.model_version != supervisor.model_version
            || record.manifest_digest != supervisor.manifest_digest
            || record.app_version != APP_VERSION
        {
            if fs::remove_file(&supervisor.path).is_err() {
                supervisor.fail_closed("SIDECAR_QUARANTINE_STALE");
            }
            return supervisor;
        }
        if !valid_failure_code(&record.error_code) || record.failure_count < 2 {
            supervisor.fail_closed("SIDECAR_QUARANTINE_INVALID");
            return supervisor;
        }
        supervisor.restart_used = true;
        supervisor.quarantined = true;
        supervisor.failure_count = record.failure_count;
        supervisor.error_code = Some(record.error_code);
        supervisor
    }

    pub fn snapshot(&self) -> RealtimeSidecarSnapshot {
        RealtimeSidecarSnapshot {
            restart_used: self.restart_used,
            quarantined: self.quarantined,
            context_interrupted: self.context_interrupted,
            failure_count: self.failure_count,
            error_code: self.error_code.clone(),
        }
    }

    pub fn begin_session(&mut self) {
        if self.quarantined {
            return;
        }
        self.restart_used = false;
        self.context_interrupted = false;
        self.failure_count = 0;
        self.error_code = None;
    }

    pub fn record_failure(
        &mut self,
        error_code: &str,
        candidate_emitted: bool,
    ) -> RealtimeSidecarDecision {
        if !valid_failure_code(error_code) {
            return self.quarantine("LOCAL_SIDECAR_FAILURE_INVALID");
        }
        self.failure_count = self.failure_count.saturating_add(1);
        self.context_interrupted |= candidate_emitted;
        self.error_code = Some(error_code.to_owned());
        if !self.restart_used && !self.quarantined {
            self.restart_used = true;
            return RealtimeSidecarDecision::RestartOnce;
        }
        self.quarantine(error_code)
    }

    pub fn quarantine(&mut self, error_code: &str) -> RealtimeSidecarDecision {
        self.restart_used = true;
        self.quarantined = true;
        self.failure_count = self.failure_count.max(2);
        self.error_code = Some(error_code.to_owned());
        if self.persist().is_err() {
            self.error_code = Some("SIDECAR_QUARANTINE_PERSIST_FAILED".to_owned());
        }
        RealtimeSidecarDecision::Quarantine
    }

    pub fn clear_for_explicit_verify(&mut self) -> Result<(), RealtimeSidecarSupervisorError> {
        if self.path.exists() {
            fs::remove_file(&self.path).map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        }
        self.restart_used = false;
        self.quarantined = false;
        self.context_interrupted = false;
        self.failure_count = 0;
        self.error_code = None;
        Ok(())
    }

    fn persist(&self) -> Result<(), RealtimeSidecarSupervisorError> {
        let parent = self
            .path
            .parent()
            .ok_or(RealtimeSidecarSupervisorError::Store)?;
        fs::create_dir_all(parent).map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        reject_symlink(parent)?;
        let record = SidecarQuarantineRecord {
            schema_version: 1,
            model_version: self.model_version.clone(),
            manifest_digest: self.manifest_digest.clone(),
            app_version: APP_VERSION.to_owned(),
            failure_count: self.failure_count,
            error_code: self
                .error_code
                .clone()
                .unwrap_or_else(|| "LOCAL_SIDECAR_INTERRUPTED".to_owned()),
        };
        let temporary = self
            .path
            .with_extension(format!("tmp-{}", std::process::id()));
        let mut file =
            fs::File::create(&temporary).map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        file.write_all(
            &serde_json::to_vec_pretty(&record)
                .map_err(|_| RealtimeSidecarSupervisorError::Store)?,
        )
        .map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        file.sync_all()
            .map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        replace_file(&temporary, &self.path).map_err(|_| RealtimeSidecarSupervisorError::Store)?;
        if let Ok(directory) = fs::File::open(parent) {
            let _ = directory.sync_all();
        }
        Ok(())
    }

    fn fail_closed(&mut self, code: &str) {
        self.restart_used = true;
        self.quarantined = true;
        self.failure_count = 2;
        self.error_code = Some(code.to_owned());
    }
}

fn reject_symlink(path: &Path) -> Result<(), RealtimeSidecarSupervisorError> {
    fs::symlink_metadata(path)
        .map_err(|_| RealtimeSidecarSupervisorError::Store)
        .and_then(|metadata| {
            (!metadata.file_type().is_symlink())
                .then_some(())
                .ok_or(RealtimeSidecarSupervisorError::Store)
        })
}

fn valid_failure_code(code: &str) -> bool {
    matches!(
        code,
        "LOCAL_SIDECAR_PROCESS_EXIT"
            | "LOCAL_SIDECAR_PROTOCOL_DISCONNECTED"
            | "LOCAL_SIDECAR_START_FAILED"
            | "LOCAL_SIDECAR_TIMEOUT"
            | "LOCAL_SIDECAR_RECOVERY_FAILED"
            | "LOCAL_SIDECAR_FAILURE_INVALID"
            | "GPU_DEVICE_REMOVED"
    )
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn supervisor(root: &Path) -> RealtimeSidecarSupervisor {
        RealtimeSidecarSupervisor::load(
            root.join("quarantine.json"),
            "model-v1".to_owned(),
            "a".repeat(64),
        )
    }

    #[test]
    fn exactly_one_restart_then_persisted_quarantine() {
        let root = tempfile::tempdir().expect("root");
        let mut controller = supervisor(root.path());
        assert_eq!(
            controller.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false),
            RealtimeSidecarDecision::RestartOnce
        );
        assert!(!controller.snapshot().quarantined);
        assert_eq!(
            controller.record_failure("LOCAL_SIDECAR_PROTOCOL_DISCONNECTED", true),
            RealtimeSidecarDecision::Quarantine
        );
        let snapshot = controller.snapshot();
        assert!(snapshot.quarantined);
        assert!(snapshot.context_interrupted);

        let restored = supervisor(root.path()).snapshot();
        assert!(restored.quarantined);
        assert!(restored.restart_used);
        assert_eq!(restored.failure_count, 2);
    }

    #[test]
    fn explicit_verify_and_version_change_clear_quarantine() {
        let root = tempfile::tempdir().expect("root");
        let mut supervisor = supervisor(root.path());
        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        supervisor.clear_for_explicit_verify().expect("clear");
        assert!(!supervisor.snapshot().quarantined);

        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        let changed = RealtimeSidecarSupervisor::load(
            root.path().join("quarantine.json"),
            "model-v2".to_owned(),
            "b".repeat(64),
        );
        assert!(!changed.snapshot().quarantined);
        assert!(!root.path().join("quarantine.json").exists());
    }

    #[test]
    fn new_session_resets_restart_budget_but_not_quarantine() {
        let root = tempfile::tempdir().expect("root");
        let mut supervisor = supervisor(root.path());
        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", true);
        supervisor.begin_session();
        let snapshot = supervisor.snapshot();
        assert!(!snapshot.restart_used);
        assert!(!snapshot.context_interrupted);
        assert_eq!(snapshot.failure_count, 0);

        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        supervisor.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false);
        supervisor.begin_session();
        assert!(supervisor.snapshot().quarantined);
    }

    #[test]
    fn invalid_persisted_state_fails_closed_without_exposing_content() {
        let root = tempfile::tempdir().expect("root");
        fs::write(root.path().join("quarantine.json"), b"{secret").expect("fixture");
        let snapshot = supervisor(root.path()).snapshot();
        assert!(snapshot.quarantined);
        assert_eq!(
            snapshot.error_code.as_deref(),
            Some("SIDECAR_QUARANTINE_INVALID")
        );
    }
}
