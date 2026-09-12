use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use fairy_realtime_worker::RealtimeActivityProfile;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

use crate::local_readiness::{
    runtime_error_code, LocalReadinessError, LocalReadinessReport, LocalReadinessService,
    OmniRuntimeReadiness,
};
use crate::omni_model_download::{
    OmniArtifactTransfer, OmniModelDownloadError, OmniModelDownloader,
};
use crate::omni_model_manager::{OmniCancellationToken, OmniModelManager, OmniModelManagerError};
use crate::omni_model_manifest::{OmniModelFile, OmniModelManifest};
use crate::omni_model_store::{OmniModelInstallPhase, OmniModelInstallState};
use crate::omni_runtime_self_test::OmniRuntimeSelfTestError;

pub const OMNI_MODEL_PROGRESS_EVENT: &str = "fairy-omni-model-progress";
const MAIN_WINDOW_LABEL: &str = "main";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OmniModelOperationKind {
    Install,
    Verify,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct OmniModelProgressEvent {
    pub schema_version: u16,
    pub event_sequence: u64,
    pub operation: OmniModelOperationKind,
    pub phase: OmniModelInstallPhase,
    pub current_file: Option<String>,
    pub received_bytes: u64,
    pub total_bytes: u64,
    pub error_code: Option<String>,
}

struct ActiveOperation {
    kind: OmniModelOperationKind,
    cancellation: OmniCancellationToken,
}

struct BackgroundOperationContext {
    app: AppHandle,
    manager_slot: Arc<Mutex<Option<OmniModelManager>>>,
    status: Arc<Mutex<OmniModelInstallState>>,
    operation: Arc<Mutex<Option<ActiveOperation>>>,
    event_sequence: Arc<AtomicU64>,
    kind: OmniModelOperationKind,
}

pub struct LocalModelControl {
    resources: Arc<crate::model_resources::ModelResources>,
    manager: Arc<Mutex<Option<OmniModelManager>>>,
    status: Arc<Mutex<OmniModelInstallState>>,
    operation: Arc<Mutex<Option<ActiveOperation>>>,
    readiness: Arc<Mutex<LocalReadinessService>>,
    event_sequence: Arc<AtomicU64>,
}

impl LocalModelControl {
    pub fn new(
        models_root: &Path,
        runtime_root: &Path,
        manifest: OmniModelManifest,
    ) -> Result<Self, String> {
        let manager =
            OmniModelManager::new(models_root, manifest.clone()).map_err(public_manager_error)?;
        let status = manager.status().clone();
        let readiness = LocalReadinessService::production(models_root, runtime_root, manifest)
            .map_err(public_readiness_error)?;
        Ok(Self {
            resources: Arc::new(crate::model_resources::ModelResources::default()),
            manager: Arc::new(Mutex::new(Some(manager))),
            status: Arc::new(Mutex::new(status)),
            operation: Arc::new(Mutex::new(None)),
            readiness: Arc::new(Mutex::new(readiness)),
            event_sequence: Arc::new(AtomicU64::new(0)),
        })
    }

    pub fn status(&self) -> Result<OmniModelInstallState, String> {
        self.status
            .lock()
            .map(|status| status.clone())
            .map_err(|_| "OMNI_MODEL_STATUS_UNAVAILABLE".to_owned())
    }

    pub fn with_resources(
        mut self,
        resources: Arc<crate::model_resources::ModelResources>,
    ) -> Self {
        self.resources = resources;
        self
    }

    pub fn readiness(
        &self,
        profile: RealtimeActivityProfile,
        refresh_hardware: bool,
    ) -> Result<LocalReadinessReport, String> {
        let status = self.status()?;
        self.readiness
            .lock()
            .map_err(|_| "LOCAL_READINESS_UNAVAILABLE".to_owned())?
            .report(&status, profile, refresh_hardware)
            .map_err(public_readiness_error)
    }

    pub fn start_install(
        &self,
        app: &AppHandle,
        profile: RealtimeActivityProfile,
    ) -> Result<OmniModelInstallState, String> {
        let readiness = self.readiness(profile, true)?;
        if !readiness.capability.static_eligible {
            return Err("LOCAL_HARDWARE_UNSUPPORTED".to_owned());
        }
        let available = readiness
            .hardware
            .disk_available_bytes
            .ok_or_else(|| "OMNI_MODEL_DISK_UNKNOWN".to_owned())?;
        if available < readiness.model_install_required_bytes {
            return Err("OMNI_MODEL_DISK_INSUFFICIENT".to_owned());
        }
        if readiness.model_shallow_present
            && matches!(
                readiness.model.phase,
                OmniModelInstallPhase::RuntimeMissing
                    | OmniModelInstallPhase::SelfTestFailed
                    | OmniModelInstallPhase::Ready
            )
        {
            return Err("OMNI_MODEL_ALREADY_INSTALLED".to_owned());
        }

        let mut operation = self
            .operation
            .lock()
            .map_err(|_| "OMNI_MODEL_OPERATION_UNAVAILABLE".to_owned())?;
        if operation.is_some() {
            return Err("OMNI_MODEL_OPERATION_BUSY".to_owned());
        }
        self.readiness
            .lock()
            .map_err(|_| "LOCAL_READINESS_UNAVAILABLE".to_owned())?
            .invalidate_verification_attestation();
        let mut manager_guard = self
            .manager
            .lock()
            .map_err(|_| "OMNI_MODEL_MANAGER_UNAVAILABLE".to_owned())?;
        let mut manager = manager_guard
            .take()
            .ok_or_else(|| "OMNI_MODEL_OPERATION_BUSY".to_owned())?;
        let cancellation = match manager.begin_install(available) {
            Ok(cancellation) => cancellation,
            Err(error) => {
                *manager_guard = Some(manager);
                return Err(public_manager_error(error));
            }
        };
        let initial = manager.status().clone();
        self.replace_status(initial.clone())?;
        *operation = Some(ActiveOperation {
            kind: OmniModelOperationKind::Install,
            cancellation: cancellation.clone(),
        });
        drop(manager_guard);
        drop(operation);
        self.publish(app, OmniModelOperationKind::Install, &initial);

        let app = app.clone();
        let manager_slot = Arc::clone(&self.manager);
        let status = Arc::clone(&self.status);
        let operation = Arc::clone(&self.operation);
        let event_sequence = Arc::clone(&self.event_sequence);
        tauri::async_runtime::spawn_blocking(move || {
            let outcome = catch_unwind(AssertUnwindSafe(|| {
                let manifest = manager.manifest().clone();
                let transfer = ProgressTransfer {
                    inner: OmniModelDownloader::new()?,
                    app: app.clone(),
                    manifest,
                    status: Arc::clone(&status),
                    event_sequence: Arc::clone(&event_sequence),
                };
                manager.download_and_install(&transfer)
            }));
            finish_background_operation(
                manager,
                outcome,
                BackgroundOperationContext {
                    app,
                    manager_slot,
                    status,
                    operation,
                    event_sequence,
                    kind: OmniModelOperationKind::Install,
                },
            );
        });
        Ok(initial)
    }

    pub fn start_verify(&self, app: &AppHandle) -> Result<OmniModelInstallState, String> {
        let mut operation = self
            .operation
            .lock()
            .map_err(|_| "OMNI_MODEL_OPERATION_UNAVAILABLE".to_owned())?;
        if operation.is_some() {
            return Err("OMNI_MODEL_OPERATION_BUSY".to_owned());
        }
        self.readiness
            .lock()
            .map_err(|_| "LOCAL_READINESS_UNAVAILABLE".to_owned())?
            .invalidate_verification_attestation();
        let mut manager_guard = self
            .manager
            .lock()
            .map_err(|_| "OMNI_MODEL_MANAGER_UNAVAILABLE".to_owned())?;
        let mut manager = manager_guard
            .take()
            .ok_or_else(|| "OMNI_MODEL_OPERATION_BUSY".to_owned())?;
        let cancellation = match manager.begin_verify() {
            Ok(cancellation) => cancellation,
            Err(error) => {
                *manager_guard = Some(manager);
                return Err(public_manager_error(error));
            }
        };
        let initial = manager.status().clone();
        self.replace_status(initial.clone())?;
        *operation = Some(ActiveOperation {
            kind: OmniModelOperationKind::Verify,
            cancellation,
        });
        drop(manager_guard);
        drop(operation);
        self.publish(app, OmniModelOperationKind::Verify, &initial);

        let app = app.clone();
        let manager_slot = Arc::clone(&self.manager);
        let status = Arc::clone(&self.status);
        let operation = Arc::clone(&self.operation);
        let readiness = Arc::clone(&self.readiness);
        let resources = Arc::clone(&self.resources);
        let event_sequence = Arc::clone(&self.event_sequence);
        tauri::async_runtime::spawn_blocking(move || {
            let outcome = catch_unwind(AssertUnwindSafe(|| {
                manager.verify_installed()?;
                replace_shared_status(&status, manager.status().clone())?;
                let runtime = readiness
                    .lock()
                    .map_err(|_| {
                        OmniModelManagerError::Verification("LOCAL_READINESS_UNAVAILABLE")
                    })?
                    .report(manager.status(), RealtimeActivityProfile::Auto, false)
                    .map_err(|_| {
                        OmniModelManagerError::Verification("LOCAL_READINESS_UNAVAILABLE")
                    })?
                    .runtime;
                if runtime == OmniRuntimeReadiness::Missing {
                    return Ok(());
                }

                manager.begin_runtime_self_test()?;
                let _reservation = resources
                    .reserve("omni_self_test", None)
                    .map_err(OmniModelManagerError::Verification)?;
                replace_shared_status(&status, manager.status().clone())?;
                publish_progress(
                    &app,
                    &event_sequence,
                    OmniModelOperationKind::Verify,
                    manager.status(),
                );
                let self_test = readiness
                    .lock()
                    .map_err(|_| {
                        OmniModelManagerError::Verification("LOCAL_READINESS_UNAVAILABLE")
                    })?
                    .run_self_test(manager.status());
                match self_test {
                    Ok(_) => manager.finish_runtime_self_test(None),
                    Err(LocalReadinessError::Runtime(error)) => {
                        let code = runtime_error_code(&error);
                        manager.finish_runtime_self_test(Some(code))?;
                        Err(OmniModelManagerError::Verification(code))
                    }
                    Err(_) => {
                        manager.finish_runtime_self_test(Some("LOCAL_READINESS_UNAVAILABLE"))?;
                        Err(OmniModelManagerError::Verification(
                            "LOCAL_READINESS_UNAVAILABLE",
                        ))
                    }
                }
            }));
            finish_background_operation(
                manager,
                outcome,
                BackgroundOperationContext {
                    app,
                    manager_slot,
                    status,
                    operation,
                    event_sequence,
                    kind: OmniModelOperationKind::Verify,
                },
            );
        });
        Ok(initial)
    }

    pub fn cancel(&self, app: &AppHandle) -> Result<OmniModelInstallState, String> {
        let operation = self
            .operation
            .lock()
            .map_err(|_| "OMNI_MODEL_OPERATION_UNAVAILABLE".to_owned())?;
        let active = operation
            .as_ref()
            .ok_or_else(|| "OMNI_MODEL_OPERATION_NOT_ACTIVE".to_owned())?;
        active.cancellation.cancel();
        let mut status = self
            .status
            .lock()
            .map_err(|_| "OMNI_MODEL_STATUS_UNAVAILABLE".to_owned())?;
        status.sequence = status.sequence.saturating_add(1);
        status.phase = OmniModelInstallPhase::Cancelling;
        status.error_code = None;
        let snapshot = status.clone();
        drop(status);
        self.publish(app, active.kind, &snapshot);
        Ok(snapshot)
    }

    pub fn remove(&self, realtime_running: bool) -> Result<OmniModelInstallState, String> {
        if realtime_running {
            return Err("OMNI_MODEL_IN_USE".to_owned());
        }
        if self
            .operation
            .lock()
            .map_err(|_| "OMNI_MODEL_OPERATION_UNAVAILABLE".to_owned())?
            .is_some()
        {
            return Err("OMNI_MODEL_OPERATION_BUSY".to_owned());
        }
        let mut manager_guard = self
            .manager
            .lock()
            .map_err(|_| "OMNI_MODEL_MANAGER_UNAVAILABLE".to_owned())?;
        let manager = manager_guard
            .as_mut()
            .ok_or_else(|| "OMNI_MODEL_OPERATION_BUSY".to_owned())?;
        manager.remove().map_err(public_manager_error)?;
        let status = manager.status().clone();
        self.replace_status(status.clone())?;
        let mut readiness = self
            .readiness
            .lock()
            .map_err(|_| "LOCAL_READINESS_UNAVAILABLE".to_owned())?;
        readiness.invalidate_verification_attestation();
        readiness.invalidate_hardware_cache();
        Ok(status)
    }

    fn replace_status(&self, next: OmniModelInstallState) -> Result<(), String> {
        replace_shared_status(&self.status, next)
            .map_err(|_| "OMNI_MODEL_STATUS_UNAVAILABLE".to_owned())
    }

    fn publish(
        &self,
        app: &AppHandle,
        operation: OmniModelOperationKind,
        status: &OmniModelInstallState,
    ) {
        publish_progress(app, &self.event_sequence, operation, status);
    }
}

struct ProgressTransfer {
    inner: OmniModelDownloader,
    app: AppHandle,
    manifest: OmniModelManifest,
    status: Arc<Mutex<OmniModelInstallState>>,
    event_sequence: Arc<AtomicU64>,
}

impl OmniArtifactTransfer for ProgressTransfer {
    fn download(
        &self,
        file: &OmniModelFile,
        partial_path: &Path,
        cancellation: &OmniCancellationToken,
        progress: &mut dyn FnMut(u64) -> Result<(), OmniModelDownloadError>,
    ) -> Result<(), OmniModelDownloadError> {
        let completed = self
            .manifest
            .files
            .iter()
            .take_while(|candidate| candidate.path != file.path)
            .map(|candidate| candidate.size)
            .sum::<u64>();
        self.inner
            .download(file, partial_path, cancellation, &mut |file_received| {
                progress(file_received)?;
                let mut status = self
                    .status
                    .lock()
                    .map_err(|_| OmniModelDownloadError::ProgressState)?;
                status.phase = OmniModelInstallPhase::Downloading;
                status.current_file = Some(file.path.clone());
                status.received_bytes = completed
                    .saturating_add(file_received)
                    .min(status.total_bytes);
                status.error_code = None;
                let snapshot = status.clone();
                drop(status);
                publish_progress(
                    &self.app,
                    &self.event_sequence,
                    OmniModelOperationKind::Install,
                    &snapshot,
                );
                Ok(())
            })
    }
}

fn finish_background_operation(
    mut manager: OmniModelManager,
    outcome: Result<Result<(), OmniModelManagerError>, Box<dyn std::any::Any + Send>>,
    context: BackgroundOperationContext,
) {
    match &outcome {
        Ok(Err(error)) => {
            let _ = manager.settle_operation_error(error);
        }
        Err(_) => {
            let _ = manager.settle_operation_panic();
        }
        Ok(Ok(())) => {}
    }
    let final_status = manager.status().clone();
    let _ = replace_shared_status(&context.status, final_status.clone());
    if let Ok(mut slot) = context.manager_slot.lock() {
        *slot = Some(manager);
    }
    if let Ok(mut active) = context.operation.lock() {
        *active = None;
    }
    publish_progress(
        &context.app,
        &context.event_sequence,
        context.kind,
        &final_status,
    );
}

fn replace_shared_status(
    status: &Mutex<OmniModelInstallState>,
    next: OmniModelInstallState,
) -> Result<(), OmniModelManagerError> {
    *status
        .lock()
        .map_err(|_| OmniModelManagerError::Verification("OMNI_MODEL_STATUS_UNAVAILABLE"))? = next;
    Ok(())
}

fn publish_progress(
    app: &AppHandle,
    sequence: &AtomicU64,
    operation: OmniModelOperationKind,
    status: &OmniModelInstallState,
) {
    let event = progress_event(sequence, operation, status);
    let _ = app.emit_to(MAIN_WINDOW_LABEL, OMNI_MODEL_PROGRESS_EVENT, event);
}

fn progress_event(
    sequence: &AtomicU64,
    operation: OmniModelOperationKind,
    status: &OmniModelInstallState,
) -> OmniModelProgressEvent {
    OmniModelProgressEvent {
        schema_version: 1,
        event_sequence: sequence.fetch_add(1, Ordering::AcqRel).saturating_add(1),
        operation,
        phase: status.phase,
        current_file: status.current_file.clone(),
        received_bytes: status.received_bytes,
        total_bytes: status.total_bytes,
        error_code: status.error_code.clone(),
    }
}

fn public_manager_error(error: OmniModelManagerError) -> String {
    match error {
        OmniModelManagerError::Busy => "OMNI_MODEL_OPERATION_BUSY",
        OmniModelManagerError::NotActive => "OMNI_MODEL_OPERATION_NOT_ACTIVE",
        OmniModelManagerError::Cancelled => "OMNI_MODEL_OPERATION_CANCELLED",
        OmniModelManagerError::InsufficientDisk { .. } => "OMNI_MODEL_DISK_INSUFFICIENT",
        OmniModelManagerError::Verification(code) => code,
        OmniModelManagerError::Manifest(_) => "OMNI_MODEL_MANIFEST_INVALID",
        OmniModelManagerError::Store(_) => "OMNI_MODEL_STORE_FAILED",
        OmniModelManagerError::Io(_) => "OMNI_MODEL_IO_FAILED",
        OmniModelManagerError::Download(_) => "OMNI_MODEL_DOWNLOAD_FAILED",
    }
    .to_owned()
}

fn public_readiness_error(error: LocalReadinessError) -> String {
    match error {
        LocalReadinessError::Store(_) => "OMNI_MODEL_STORE_FAILED",
        LocalReadinessError::Runtime(OmniRuntimeSelfTestError::RuntimeMissing) => {
            "OMNI_RUNTIME_MISSING"
        }
        LocalReadinessError::Runtime(_) => "OMNI_SELF_TEST_FAILED",
        LocalReadinessError::SizeOverflow => "OMNI_MODEL_SIZE_OVERFLOW",
        LocalReadinessError::AttestationWrite => "OMNI_ATTESTATION_WRITE_FAILED",
    }
    .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::omni_model_catalog::bundled_minicpm_o45_manifest;

    #[test]
    fn progress_events_are_monotonic_and_path_bounded() {
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let mut status = OmniModelInstallState::initial(&manifest);
        status.phase = OmniModelInstallPhase::Downloading;
        status.current_file = Some(manifest.files[0].path.clone());
        status.received_bytes = 10;
        let sequence = AtomicU64::new(0);

        let first = progress_event(&sequence, OmniModelOperationKind::Install, &status);
        status.received_bytes = 20;
        let second = progress_event(&sequence, OmniModelOperationKind::Install, &status);

        assert_eq!(first.event_sequence, 1);
        assert_eq!(second.event_sequence, 2);
        assert_eq!(
            second.current_file.as_deref(),
            Some("MiniCPM-o-4_5-Q4_K_M.gguf")
        );
        assert!(!serde_json::to_string(&second)
            .expect("event")
            .contains(":\\\\"));
    }
}
